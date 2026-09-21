# NullBrain / NullCode

**NullBrain** is a Raspberry Pi–hosted, local-inference software-development agent
platform. **NullCode** is its coding-workflow subsystem: the part that accepts a
development task, generates code with a local model, compiles and tests it in a
sandbox, attempts bounded repairs, runs a review gate, and — only on explicit
human command — prepares a draft GitHub pull request.

Everything runs on the host named `nullbrain`. Inference is local (Ollama); no
code or prompt leaves the machine except during the explicit publish step.

## What it does today

NullCode is a working, incrementally-built pipeline, not a prototype and not a
general autonomous agent. It can, today:

- queue workflow jobs in SQLite and run them through one serialized worker;
- generate Java through a local Ollama model behind a small Rust/Axum controller;
- compile and test candidates inside locked-down, network-less Docker containers;
- feed compile/test diagnostics back for a strictly bounded number of repairs;
- run deterministic review rules and refuse to commit when they find something;
- commit passing work to an isolated branch in an isolated clone;
- plan and execute bounded multi-file edits against a Gradle/JUnit project;
- verify a model's work against committed, human-reviewed acceptance tests;
- push a task branch and open a **draft** PR when a human runs the publisher.

It deliberately does **not** browse the internet, choose its own tasks, edit
arbitrary repositories, merge anything, or publish without a human command.

See [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) for the verified current
state and the next intended milestone.

## Major components

| Area | Path | Role |
| --- | --- | --- |
| Inference controller | `rust/` | Rust/Axum HTTP API on `127.0.0.1:8080`, SQLite-backed job queue, one Ollama worker. |
| Workflow engine | `src/nullcode/core/` | Workflow store, the single worker loop, Java verification, review rules. |
| Repository profiles | `src/nullcode/repo/` | Restricted, plan, execute and acceptance-gated repository workflows. |
| Gradle profile | `src/nullcode/gradle/` | Offline Gradle/JUnit verification and the approved build templates. |
| Publishing | `src/nullcode/publish/` | Acceptance preparation/checking and explicit GitHub draft-PR delivery. |
| Fixtures | `src/nullcode/fixtures/` | Generators for the local Git repositories the smoke tests use. |
| Tests | `tests/` | 171 unittest cases: real Git and SQLite, simulated inference and Docker. |
| Deployment | `compose/`, `deploy/` | Compose files for the controller and Ollama; the worker systemd unit. |
| Documentation | `docs/` | Architecture, current state, changelog and the milestone record. |

## Requirements

- Linux host with Docker, Git and Python 3.11+ (the Pi runs this as user `null`,
  UID/GID 1001, in the `docker` group).
- Ollama reachable on `127.0.0.1:11434` with the configured model pulled.
- `eclipse-temurin:21-jdk` pulled locally for the plain-JDK profiles.
- `nullcode-gradle:8.14.3-jdk21` built locally for the Gradle profile
  (`docker build -t nullcode-gradle:8.14.3-jdk21 src/nullcode/gradle/gradle_profile`).
- The GitHub CLI (`gh`), authenticated, only for the publish step.

The Python side has **no third-party dependencies**; it uses the standard
library plus host tools invoked as subprocesses.

## Running it

```sh
# 1. Local inference
docker compose -f compose/ollama.compose.yml up -d

# 2. Inference controller (builds the Rust project in rust/)
docker compose -f compose/nullcode.compose.yml build
docker compose -f compose/nullcode.compose.yml up -d
curl --fail http://127.0.0.1:8080/health

# 3. Workflow worker (see deploy/ for the systemd unit)
PYTHONPATH=src python3 -m nullcode.core.java_workflow run
```

Submitting and inspecting work:

```sh
PYTHONPATH=src python3 -m nullcode.core.java_workflow submit
PYTHONPATH=src python3 -m nullcode.core.java_workflow list
PYTHONPATH=src python3 -m nullcode.core.java_workflow show 1
PYTHONPATH=src python3 -m nullcode.core.java_workflow wait 1
```

`PYTHONPATH=src` is only needed when the package is not installed. With
`pip install -e .` the `-m nullcode...` commands work from anywhere.

Full command reference, including the repository, Gradle, plan, execute and
acceptance profiles: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Running the tests

```sh
PYTHONPATH=src python3 -m unittest discover -s tests   # 171 tests
pytest                                                 # same 77, config in pyproject.toml
cd rust && cargo test                                  # 3 controller tests
```

The Python suite uses real Git repositories and real SQLite databases but
simulates inference and Docker verification, so it runs anywhere. Actual model
and container behaviour must be exercised on the Pi.

## Where to look next

| Question | File |
| --- | --- |
| What is built, verified and next? | [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) |
| How is it structured and why? | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| What changed when? | [`docs/CHANGELOG.md`](docs/CHANGELOG.md) |
| How was each increment built and tested? | [`docs/milestones/`](docs/milestones/) |
| What is the controller API? | [`rust/README.md`](rust/README.md) |
| Why are there snapshot directories? | [`checkpoints/README.md`](checkpoints/README.md) |

**Agents and new contributors: read `docs/PROJECT_STATE.md` first.** It is the
authoritative handoff document and lists the architectural constraints that must
not be violated.
