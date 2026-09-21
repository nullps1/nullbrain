# NullCode inference controller

*Milestone 1. This document describes the Rust/Axum controller in this
directory. For the project as a whole see the [repository README](../README.md)
and [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).*

Rust/Axum API, SQLite persistence, one Ollama worker. This first increment generates answers only. It does not read repositories, execute generated code, compile Java, or create GitHub PRs. A succeeded job means inference completed, not that the suggested code passed tests.

## Deploy on nullbrain

The supplied Compose file targets the existing Linux Pi, user null UID/GID 1001, and Ollama listening on localhost port 11434. Host networking lets the controller reach that endpoint; the application itself binds only 127.0.0.1:8080. No firewall changes are needed. There is no authentication in this localhost-only prototype; do not expose it to the LAN or internet.

Deploy the repository at `/srv/nullbrain`, then:

```sh
sudo install -d -o 1001 -g 1001 -m 0750 /srv/nullbrain/data/nullcode
cd /srv/nullbrain
sudo docker compose -f compose/nullcode.compose.yml config --quiet
sudo docker compose -f compose/nullcode.compose.yml build --progress plain
sudo docker compose -f compose/nullcode.compose.yml up -d
curl --fail http://127.0.0.1:8080/health
```

The Compose file's build context is this `rust/` directory.

The build runs three Rust tests before creating the runtime image. This first build resolves dependencies and uses a floating Rust 1 builder; preserve the resolved Cargo.lock and pin image digests before treating this as a reproducible release. Runtime limits do not constrain the build; compilation uses at most two Cargo jobs. Stop the Ollama container temporarily while compiling if memory is tight, then restart it before submitting jobs.

## Use

```sh
curl --fail --silent --show-error http://127.0.0.1:8080/jobs \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Write a Java 21 class Numbers with static int max(int[] values). Reject null and empty arrays with IllegalArgumentException. Return only Java source."}'
curl --fail --silent --show-error http://127.0.0.1:8080/jobs/1
curl --fail --silent --show-error http://127.0.0.1:8080/jobs
sudo docker compose -f compose/nullcode.compose.yml logs --tail 50 controller
```

Use the actual returned ID for the detail request. States are queued, running, succeeded, failed. Requests have a 15-minute timeout; there are no automatic retries. At most 16 unfinished jobs may be queued. Prompts are limited to 2000 UTF-8 bytes for this small-context prototype. Responses are limited to 768 generated tokens with a 2048-token context. Output-limit truncation is a failure.

Jobs and answers persist in `/srv/nullbrain/data/nullcode/nullcode.sqlite3`. An interrupted running job becomes failed on restart; queued jobs remain queued and resume in order. Keep one controller instance for this database. `/health` reports the controller's liveness, not Ollama readiness. The latest 50 jobs are listed; records are not automatically deleted.

The image has no host Docker socket, repository mounts, GitHub credentials, or model-controlled command execution. Later increments added isolated Java builds and repository operations as a separate Python worker; see [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

API reference: https://docs.ollama.com/api/chat
