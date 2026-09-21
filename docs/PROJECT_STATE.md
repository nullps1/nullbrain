# NullCode project state

**Updated:** 2026-09-21, at the "reorganize nullcode project structure" refactor.
**Branch:** `claude/nullcode-repo-reorganize-ppmwni`
**Pre-refactor commit:** `7e077ee` ("Initial NullBrain server backup")

This is the authoritative handoff document. Read it before changing anything.
It describes the repository **as it actually is**, verified by running the
commands in [§8](#8-verifying-this-state) — not by copying older notes.

---

## 1. What is known-good today

Verified in this environment at the refactor:

| Check | Result |
| --- | --- |
| Python suite (`python3 -m unittest discover -s tests`) | **93 passed**, 0 failed |
| Python suite under `pytest` | **93 passed** (subtests included) |
| Rust suite (`cargo test` in `rust/`) | **3 passed** |
| `cargo check` | clean |
| `docker compose -f compose/nullcode.compose.yml config` | valid; build context resolves to `rust/` |
| `docker compose -f compose/ollama.compose.yml config` | valid |
| All 14 package modules import | yes |
| All 7 CLI entry points respond to `--help` via `-m` | yes |
| `create_fixture` / `create_gradle_fixture` produce working repos | yes |
| `gradle_workflow.inspect()` accepts a freshly generated fixture | yes (template comparison passes from the new path) |
| Wheel build includes `gradle_profile/` and `acceptance/` package data | yes |

The test counts are identical before and after the reorganization.

## 2. Capabilities that work

- Local inference through a Rust/Axum controller in front of Ollama, with a
  persistent SQLite job queue and restart recovery.
- A single serialized Python workflow worker with its own SQLite workflow store,
  `flock`-based single-instance guarantee, and leftover-container cleanup.
- Java generation → sandboxed compile → sandboxed test → bounded repair.
- Deterministic review rules that block a commit when they find a finding.
- Local-repository workflows on isolated clones with `origin` removed, task
  branches, saved diffs and review evidence, and local-only commits.
- Offline Gradle/JUnit verification against approved build templates, with JUnit
  XML parsed for a minimum non-skipped passing test count.
- Read-only repository planning (`repo-plan-v1`) producing a validated JSON plan.
- Planned, bounded multi-file execution (`repo-execute-v1`).
- Acceptance-gated production edits against committed, human-reviewed tests
  (`accepted-java-v1`), including hash-based tamper detection.
- Explicit, human-initiated GitHub **draft** PR delivery with a preview mode that
  makes no network calls.

## 3. Current modules

```
src/nullcode/
  core/      java_workflow.py  validate_java.py  review_java.py
  repo/      repo_workflow.py  repo_plan_workflow.py
             repo_execute_workflow.py  accepted_workflow.py
  gradle/    gradle_workflow.py  gradle_profile/
  publish/   publish_workflow.py  check_acceptance.py  prepare_acceptance.py
  fixtures/  create_fixture.py  create_gradle_fixture.py
  acceptance/  contract.json  TextStatsAcceptanceTest.java   (package data)
rust/        Cargo.toml  src/main.rs  Dockerfile  README.md
tests/       6 files, 77 unittest cases
scripts/     smoke.py
```

Dependency direction is documented and verified in
[`ARCHITECTURE.md` §2](ARCHITECTURE.md#2-python-package-layering).

## 4. Runtime and host assumptions

- Host `nullbrain`, a Raspberry Pi running Linux, service user `null` (1001:1001)
  in the `docker` group.
- Repository deployed at `/srv/nullbrain` (was `/srv/nullbrain/src/nullcode`).
- Controller on `127.0.0.1:8080`; Ollama on `127.0.0.1:11434`; no LAN exposure
  and no authentication — this is a localhost-only system.
- Workflow DB: `/srv/nullbrain/data/nullcode-workflows/workflows.sqlite3`
  (`NULLCODE_WORKFLOW_DIR`).
- Job artifacts: `/srv/nullbrain/jobs/workflow-<id>/` (`NULLCODE_JOBS_DIR`).
- Controller DB: `/srv/nullbrain/data/nullcode` bind-mounted to `/data`.
- Fixture repositories on the Pi live under `/srv/nullbrain/repos/`.
- Images required locally: `eclipse-temurin:21-jdk` and
  `nullcode-gradle:8.14.3-jdk21`.
- Python 3.11+, standard library only. No third-party Python dependencies.

### ⚠️ Deployment action required after this refactor

The systemd unit changed because the worker is now a package module. The live
host still runs the old unit. **The repository was changed; the host was not.**
Apply on the Pi when convenient:

```sh
sudo systemctl stop nullcode-worker
sudo install -m 0644 /srv/nullbrain/deploy/nullcode-worker.service \
    /etc/systemd/system/nullcode-worker.service
sudo systemctl daemon-reload
sudo systemctl start nullcode-worker
systemctl status nullcode-worker
```

The unit now uses `WorkingDirectory=/srv/nullbrain`,
`Environment=PYTHONPATH=/srv/nullbrain/src` and
`ExecStart=/usr/bin/python3 -u -m nullcode.core.java_workflow run`.
Compose invocations also moved: the files are `compose/nullcode.compose.yml`
and `compose/ollama.compose.yml`, and the controller build context is `rust/`.

## 5. Completed milestones

| # | Title | Doc |
| --- | --- | --- |
| 1 | Local inference queue (Rust/Axum + SQLite + Ollama) | [`rust/README.md`](../rust/README.md) |
| 2 | Automated Java verification and one repair | [`milestones/MILESTONE-2.md`](milestones/MILESTONE-2.md) |
| 3 | Repository-backed Java jobs | [`milestones/MILESTONE-3.md`](milestones/MILESTONE-3.md) |
| 4 | Targeted review before committing | [`milestones/MILESTONE-4.md`](milestones/MILESTONE-4.md) |
| 5 | Draft pull-request delivery | [`milestones/MILESTONE-5.md`](milestones/MILESTONE-5.md) |
| 6 | Configurable small Gradle/JUnit tasks | [`milestones/MILESTONE-6.md`](milestones/MILESTONE-6.md) |
| 7A | Read-only repository inspection and planning | [`milestones/MILESTONE-7A.md`](milestones/MILESTONE-7A.md) |
| 7B | Planned, bounded multi-file execution | [`milestones/MILESTONE-7B.md`](milestones/MILESTONE-7B.md) |
| — | Acceptance-gated production edits (`accepted-java-v1`) | *no document — see `accepted_workflow.py`* |
| — | `javac`-driven repair rule (`javac-string-array-stream-loop-v1`) | *no document — see `compiler_repair_rule`* |

## 6. Known limitations and unresolved issues

1. **The acceptance workflow and the compiler repair rule still have no
   milestone document.** 7A and 7B are now documented in
   `docs/milestones/`, each backed by a dedicated test file
   (`test_repo_plan_workflow.py`, `test_repo_execute_workflow.py`) that
   exercises their orchestration with fake inference — not just a source
   reading. The acceptance workflow (`accepted-java-v1`) and the
   `javac-string-array-stream-loop-v1` repair rule remain undocumented,
   though both do have real test coverage via `test_accepted_workflow.py`.
2. **No Git history to recover from.** The repository has a single commit
   (`7e077ee`). See [§7](#7-checkpoint-snapshots).
3. **`Cargo.lock` is not tracked.** The root `.gitignore` `*.lock` pattern
   (intended for lock files generally) also matches `Cargo.lock`, while
   `rust/README.md` says the resolved lock file should be preserved. This is a
   pre-existing contradiction; it was **not** changed in the refactor.
4. **Docker-dependent paths are untested here.** The Java/Gradle container
   builds and real model inference cannot run in this environment; the Python
   suite simulates them. Real behaviour must be exercised on the Pi.
5. **Review rules are narrow.** Three text-based checks for the Numbers profile.
   Not a parser, not a static analyser, not a security review.
6. **Single worker, single job.** No concurrency; interrupted workflows become
   `interrupted` on restart and must be resubmitted.
7. **No authentication anywhere.** Localhost-only by design.
8. **Java only.** Gradle only; no Maven, no dependency resolution inside job
   containers.

## 7. Checkpoint snapshots

`checkpoints/` holds two pre-install rollback snapshots that were copied into
the working tree before this repository had useful Git history:

| Directory | Represents |
| --- | --- |
| `accepted-backup-81e837678814497aa9384c59dde2df54/` | `java_workflow.py` as it was **before** the `accepted-java-v1` workflow was installed, plus `installed-files.json` listing that increment's files. |
| `stream-rule-backup-75cee6743c604cdf82d16cbbf9da5c4d/` | `accepted_workflow.py` and `test_accepted_workflow.py` as they were **before** the `javac-string-array-stream-loop-v1` repair rule was added. |

**They were moved out of the source tree but deliberately not deleted, and no
Git tags were created.** The repository contains exactly one commit, so no
commit represents either pre-install state; tagging `HEAD` would point a
"checkpoint" tag at the state *after* both increments, which would be wrong.
Deleting them would destroy the only record of those states.

They can be retired once the corresponding states are verifiably reachable from
Git — for example by importing the original per-milestone archives as commits,
or by accepting that the pre-install states are no longer needed. Until then,
leave them in place. `checkpoints/README.md` repeats this.

## 8. Verifying this state

```sh
# Python (no install needed)
PYTHONPATH=src python3 -m unittest discover -s tests   # expect: Ran 93 tests ... OK
pytest -q                                              # expect: 77 passed

# Rust controller
cd rust && cargo test && cargo check                   # expect: 3 passed

# Compose
docker compose -f compose/nullcode.compose.yml config --quiet
docker compose -f compose/ollama.compose.yml config --quiet

# Imports and entry points
PYTHONPATH=src python3 -m nullcode.core.java_workflow --help
PYTHONPATH=src python3 -m nullcode.publish.publish_workflow --help

# On the Pi only: real containers and inference
docker build -t nullcode-gradle:8.14.3-jdk21 src/nullcode/gradle/gradle_profile
PYTHONPATH=src python3 -m nullcode.fixtures.create_gradle_fixture /srv/nullbrain/repos/nullcode-java-lab
PYTHONPATH=src python3 -m nullcode.core.java_workflow submit
PYTHONPATH=src python3 -m nullcode.core.java_workflow wait <id>
```

## 9. Active direction and next milestone

**Active direction:** consolidating the project into a maintainable platform —
this refactor, the documentation set, and Git as the source of truth for history
and recovery rather than copied directories.

**Next intended milestone, as established by the repository itself:** there is
no committed statement of the next feature milestone. The two candidates the
repository actually supports are:

1. **Document the acceptance workflow and the compiler repair rule**, the
   same way 7A and 7B were just done: real tests first, then the milestone
   doc written from what the tests actually proved. Closes the rest of §6.1.
2. **Retire the checkpoint snapshots** by establishing real Git checkpoints
   (§7), completing the "Git is the recovery mechanism" goal.

Anything beyond that — new languages, concurrency, model-driven repository
browsing, automatic publishing — is *not* established by the repository and
should be confirmed with the project owner before starting.

## 10. Constraints for anyone continuing this work

Full list in [`ARCHITECTURE.md` §8](ARCHITECTURE.md#8-boundaries-that-must-not-be-violated).
The short version:

- Do not weaken review or acceptance gates, or the conditions for "passed".
- Do not increase attempt budgets or let infrastructure errors count as repairs.
- Do not give the HTTP controller the Docker socket.
- Do not make publishing automatic, non-draft, or capable of merging.
- Do not set `--acceptance-reviewed` programmatically.
- Do not move runtime/generated state into the package or into Git.
- Do not hoist `core.java_workflow`'s lazy profile imports to module scope.
- Develop in small, verified milestones; run the suites before and after.
