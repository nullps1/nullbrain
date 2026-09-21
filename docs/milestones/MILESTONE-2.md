# Milestone 2: automated Java verification and one repair

The Rust API stays unchanged. A trusted Python host worker coordinates inference through that API and launches restricted Java containers. This avoids placing the Docker socket inside the HTTP controller. The host worker runs as null with Docker-group access, which is privileged access to the host. Model-generated Java runs only in the restricted child container, without that socket or credentials.

Supported workflow: the fixed Numbers.max task and its eight fixed tests. This is not yet a general repository agent. Workflow IDs and inference-job IDs are separate, with each attempt linking its inference job. Existing inference history is preserved. A workflow succeeds only after compilation and the test process succeed with the expected completion marker. This smoke harness is for functional checking, not adversarial proof of correctness.

## Install on the Pi

Extract the milestone archive over `/srv/nullbrain/src/nullcode`. It adds the worker and tests; no Rust rebuild or existing database migration is needed.

```sh
sudo install -d -o null -g null -m 0750 /srv/nullbrain/data/nullcode-workflows
cd /srv/nullbrain/src/nullcode
python3 -m unittest -v test_workflow.py
sudo install -m 0644 nullcode-worker.service /etc/systemd/system/nullcode-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now nullcode-worker
python3 java_workflow.py submit
python3 java_workflow.py list
python3 java_workflow.py show 1
```

The service requires the existing Rust controller at localhost:8080 and the already-pulled `eclipse-temurin:21-jdk` image. It selects the local image ID and records it for each verification. No downloads happen inside job containers. No new listening port or firewall rule is added.

## Verify a repair

```sh
python3 java_workflow.py submit --repair-demo
python3 java_workflow.py list
python3 java_workflow.py show 2
journalctl -u nullcode-worker -n 40 --no-pager
```

Use the returned workflow IDs. Repair demo starts with deliberately incorrect code returning zero, so the first test must fail. The model then gets the task, previous source, and diagnostic, and has one repair attempt. A model failure remains failed; success is not guaranteed.

The worker records generating, compiling, testing, repairing, and final succeeded/failed states. It keeps every attempt under `/srv/nullbrain/jobs/workflow-ID/attempt-N` and in `/srv/nullbrain/data/nullcode-workflows/workflows.sqlite3`. Restarted active workflows become interrupted; queued jobs resume. A lock prevents two workers claiming jobs concurrently. On worker startup, only leftover containers bearing its own workflow label are removed. Containers also have a five-minute maximum lifetime. Compile and test each have a two-minute timeout and captured logs are capped at 64 KiB each. Infrastructure errors and timeouts do not trigger model repairs.

Prompt context remains small: repair source is capped at 900 bytes and diagnostics at 500 bytes to fit the existing 2000-byte controller input limit. Output uses the controller's existing model/context/token settings.

Stop just this milestone's worker with `sudo systemctl disable --now nullcode-worker`. The Rust controller and all stored records remain intact. Records are not automatically deleted.

## Validation

The included unit tests use fake inference and verification results to test orchestration deterministically. They do not claim to exercise Docker or the actual model. Run both the normal workflow and repair demo on the Pi to establish end-to-end behavior.
