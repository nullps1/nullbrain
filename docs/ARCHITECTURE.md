# NullCode architecture

This describes the architecture **as it exists in this repository today**,
verified against the source, the tests and the configuration. Anything
speculative is explicitly labelled *Future direction*.

## 1. Two processes, one host

NullCode is two cooperating processes on the `nullbrain` host:

```
        ┌──────────────────────────────┐
        │ Ollama (container)           │   compose/ollama.compose.yml
        │ 127.0.0.1:11434              │
        └──────────────┬───────────────┘
                       │ HTTP
        ┌──────────────┴───────────────┐
        │ nullcode controller (Rust)   │   rust/, compose/nullcode.compose.yml
        │ 127.0.0.1:8080               │
        │ SQLite inference-job queue   │
        │ one serialized model worker  │
        └──────────────┬───────────────┘
                       │ HTTP  (POST /jobs, GET /jobs/{id})
        ┌──────────────┴───────────────┐
        │ workflow worker (Python)     │   src/nullcode/, deploy/nullcode-worker.service
        │ SQLite workflow queue        │
        │ drives Docker + Git          │
        └──────────────┬───────────────┘
                       │ docker run / docker exec
        ┌──────────────┴───────────────┐
        │ disposable build sandboxes   │
        │ no network, no credentials,  │
        │ no Docker socket, read-only  │
        └──────────────────────────────┘
```

The split is deliberate and is a **hard constraint**: the HTTP controller never
gets the Docker socket, and model-generated code never runs anywhere except
inside a disposable sandbox container.

### Controller (`rust/src/main.rs`)

Axum service bound to `127.0.0.1:8080` only. Routes: `GET /health`,
`GET|POST /jobs`, `GET /jobs/{id}`. Configuration is environment-driven:
`NULLCODE_DB`, `OLLAMA_URL`, `OLLAMA_MODEL`. One inference worker; jobs are
`queued → running → succeeded|failed`. Truncated model output is a failure, not
a success. Three Rust unit tests cover FIFO claiming, restart recovery, and
model-error handling. Full API notes: [`rust/README.md`](../rust/README.md).

### Workflow worker (`src/nullcode/core/java_workflow.py`)

`python -m nullcode.core.java_workflow run` takes an exclusive `flock` on
`worker.lock`, clears leftover containers carrying its own label, marks
previously-running workflows `interrupted`, then claims queued workflows one at
a time forever. One worker, one job. Workflow IDs and inference-job IDs are
distinct; each attempt records the inference job that produced it.

## 2. Python package layering

Dependencies flow strictly downward. This was verified by reading every import
in `src/nullcode/`.

```
Layer 4   tests/                       scripts/smoke.py  (controller HTTP only)
              │                              ─── depends on nothing in-package
              ▼
Layer 3   publish/        fixtures/
            publish_workflow              create_fixture
            check_acceptance              create_gradle_fixture
            prepare_acceptance
              │                              │
              ▼                              ▼
Layer 2   repo/
            accepted_workflow ──▶ repo_execute_workflow ──▶ repo_plan_workflow
            repo_workflow
              │
              ▼
Layer 1   gradle/
            gradle_workflow  (+ gradle_profile/ templates)
              │
              ▼
Layer 0   core/
            java_workflow ──▶ validate_java
            review_java
```

Exact import edges, read from the source:

| Module | Imports from |
| --- | --- |
| `core.java_workflow` | `core.validate_java` (plus lazy profile imports, see below) |
| `core.validate_java`, `core.review_java` | stdlib only |
| `repo.repo_workflow` | `core.java_workflow`, `core.validate_java`, `core.review_java` |
| `repo.repo_plan_workflow` | `core.java_workflow` |
| `gradle.gradle_workflow` | `core.java_workflow`, `core.review_java`, `repo.repo_workflow` |
| `repo.repo_execute_workflow` | `core.java_workflow`, `core.review_java`, `gradle.gradle_workflow`, `repo.repo_plan_workflow`, `repo.repo_workflow` |
| `repo.accepted_workflow` | `core.java_workflow`, `core.review_java`, `gradle.gradle_workflow`, `repo.repo_execute_workflow`, `repo.repo_workflow` |
| `publish.publish_workflow` | `core.java_workflow`, `core.review_java`, `repo.repo_workflow`, `gradle.gradle_workflow` (lazy) |
| `publish.prepare_acceptance` | `gradle.gradle_workflow`, `repo.repo_workflow` |
| `publish.check_acceptance` | `gradle.gradle_workflow`, `repo.repo_workflow`, `repo.accepted_workflow` |
| `fixtures.create_fixture` | `core.validate_java`, `repo.repo_workflow` |
| `fixtures.create_gradle_fixture` | `gradle.gradle_workflow`, `repo.repo_workflow` |
| `scripts/smoke.py` | nothing in-package; speaks to the controller over HTTP |

Note that `gradle` sits *below* `repo.repo_execute_workflow` but *above*
`repo.repo_workflow` (it uses that module's hardened `git()` helper). The
directory grouping follows subject matter; the table above is the authority on
dependency direction.

`core/java_workflow.py` is the only module that deviates: it imports the higher
profile modules **lazily inside functions** (in `main()` and the worker loop) to
dispatch on a job's profile without creating an import cycle. That pattern is
load-bearing — do not hoist those imports to module scope.

| Package | Modules | Responsibility |
| --- | --- | --- |
| `core` | `java_workflow`, `validate_java`, `review_java` | Workflow `Store` (SQLite), worker loop, controller HTTP client, sandboxed `javac`/`java` verification, source extraction, deterministic review rules. |
| `repo` | `repo_workflow`, `repo_plan_workflow`, `repo_execute_workflow`, `accepted_workflow` | Local-repository profiles, each with its own bounded attempt budget and gates. |
| `gradle` | `gradle_workflow`, `gradle_profile/` | Offline Gradle/JUnit builds, JUnit XML parsing, the approved build-file templates. |
| `publish` | `publish_workflow`, `check_acceptance`, `prepare_acceptance` | Acceptance contract preparation/validation and explicit GitHub draft-PR delivery. |
| `fixtures` | `create_fixture`, `create_gradle_fixture` | Create the local Git fixture repositories used by tests and smoke runs. |

## 3. Workflow profiles

A workflow row carries an optional `repo_spec` JSON blob; its `profile` field
selects the runner in the worker loop.

| Profile | CLI | Module | Scope |
| --- | --- | --- | --- |
| *(none)* | `submit` | `core.java_workflow.run_job` | Fixed `Numbers.max` task, 8 fixed checks, 2 attempts (1 repair). |
| `numbers-jdk21-v1` | `submit-repo` | `repo.repo_workflow` | One editable `Numbers.java` in a local repo; 3 attempts max — 1 build repair + 1 review correction. |
| `gradle-junit-v1` | `submit-gradle` | `gradle.gradle_workflow` | One approved file from committed `.nullcode.json`, offline Gradle/JUnit build, 2 attempts (1 correction). |
| `repo-plan-v1` | `submit-plan` | `repo.repo_plan_workflow` | Read-only inspection and planning: select ≤3 files, produce a validated JSON plan. No edits. |
| `repo-execute-v1` | `submit-execute` | `repo.repo_execute_workflow` | Planned, bounded multi-file edit of approved production **and** test files; ≤2 repairs. |
| `accepted-java-v1` | `submit-accepted` | `repo.accepted_workflow` | Edits one production file against **committed, human-reviewed** acceptance tests; ≤2 repairs. |

### Execution model, common to the repository profiles

1. Resolve and record the exact base commit at submission time.
2. Clone the source repository `--no-hardlinks --no-checkout` into the job
   directory, branch `agent/workflow-<id>`, then **remove the `origin` remote**
   so a job can never push back to the source.
3. Generate a candidate via the controller. Prompts are hard-capped at 2000
   bytes; oversized prompts raise rather than silently truncate.
4. Write only the approved file(s); assert the changed-file set matches.
5. Verify in a sandbox container. Save answer, source, diff, logs and result.
6. Gate: build/test must pass **and** the review must pass.
7. Only then commit locally. Failure is recorded honestly; no commit is created.

### Attempt budgets are gates, not suggestions

Each profile's repair limit is fixed in code and is part of the safety model.
Infrastructure failures (Docker errors, timeouts, failed cleanup) never consume
a model repair and never count as passing.

## 4. Acceptance flow (`accepted-java-v1`)

This is the strongest gate in the system and the one that most directly
expresses the project's intent.

```
prepare_acceptance  →  human review  →  commit contract+tests  →  check_acceptance  →  submit-accepted
   (isolated             (mandatory)      (into the repo)          (reference +          (--acceptance-
    checkout, no                                                    3 mutants)            reviewed)
    commit, no
    submission)
```

- `publish/prepare_acceptance.py` creates an isolated review checkout with the
  proposed contract and acceptance tests added. It never commits, pushes or
  submits a workflow. Its assets are package data in `src/nullcode/acceptance/`.
- `publish/check_acceptance.py` runs the proposed suite against a reference
  implementation and three mutants, so a suite that cannot fail is rejected.
- `repo/accepted_workflow.py` refuses to run unless `acceptance_reviewed` is
  `True`, hashes the protected test files before and after, and raises if the
  model touched anything outside its single production target. Skipped tests,
  insufficient test counts, tampered snapshots and failed container cleanup all
  block the commit.

`--acceptance-reviewed` is a human attestation. **Never default it, infer it, or
set it programmatically.**

## 5. Publishing / PR flow

`publish/publish_workflow.py` is an explicit CLI step. The worker never
publishes on its own.

For `repo-execute-v1`, it delegates local validation to
`publish/repo_execute_publish.py`. That validator resolves the authoritative
final result against root repository metadata, checks candidate and original-test
snapshots, verifies protected files and per-file reviews, re-derives the approved
`editable_test_files` scope rather than trusting it, and requires an
increase in executed test cases. See [Milestone 7C-2](milestones/MILESTONE-7C-2.md).

- Default mode is a local preview making **no** GitHub requests.
- `--publish` pushes the task branch and opens a **draft** PR.
- Preconditions: succeeded workflow, zero compile/test/cleanup exit codes, the
  expected completion marker, a passing review, a clean unchanged checkout,
  exactly one commit on the recorded base, only the allowed files changed, and
  source/diff SHA-256 hashes matching the saved review evidence.
- GitHub `main` must exactly equal the recorded base commit; a moved base is
  rejected rather than silently rebased. No force push. No merge. `main` is
  never pushed.
- Credentials come from the host's authenticated `gh`. They are never mounted
  into build containers or exposed to the model.
- A `publication.json` receipt is written into the workflow directory.

## 6. Filesystem: source, fixtures, and runtime state

Four distinct categories. Keeping them separate is a constraint, not a style
preference.

| Category | Location | Tracked by Git? |
| --- | --- | --- |
| **Source** | `src/nullcode/`, `rust/`, `tests/`, `scripts/` | Yes |
| **Source-controlled fixture assets** | `src/nullcode/gradle/gradle_profile/`, `src/nullcode/acceptance/` | Yes — they are package data read at runtime |
| **Generated fixture repositories** | wherever the operator points `create_fixture` (the Pi uses `/srv/nullbrain/repos/`) | No |
| **Runtime / generated state** | job directories, workflow DB, checkouts, worktrees, logs, models | No |

Runtime paths, all overridable by environment variable:

| What | Default | Variable |
| --- | --- | --- |
| Workflow database directory | `/srv/nullbrain/data/nullcode-workflows` | `NULLCODE_WORKFLOW_DIR` |
| Job artifact directories | `/srv/nullbrain/jobs` | `NULLCODE_JOBS_DIR` |
| Controller database | `/data/nullcode.sqlite3` in-container, bind-mounted from `/srv/nullbrain/data/nullcode` | `NULLCODE_DB` |
| Ollama data | `/srv/nullbrain/data/ollama` | — |

Per job, under `<jobs>/workflow-<id>/`: `repo/` (the isolated checkout),
`repository.json`, and `attempt-<n>/` holding `answer.txt`, `prompt.txt`,
`diff.patch`, `review.json`, `result.json`, `verification.json` and build logs.

**Runtime state must never move inside the importable package**, and the
`.gitignore` patterns covering `jobs/`, `logs/`, `repos/`, `worktrees/`,
`models/`, `*.gguf`, `*.sqlite3*` and secrets must not be weakened.

## 7. Sandbox constraints

Every verification container runs with: `--network=none`, `--read-only`,
`--user=1001:1001`, `--cap-drop=ALL`, `--security-opt=no-new-privileges:true`,
bounded memory/CPU/PIDs, `--log-driver=none`, tmpfs work directories, a
read-only bind of the source snapshot, and a hard lifetime. Images are pinned by
resolved image **ID**, not tag, and recorded in the verification result.
Container cleanup failure invalidates a pass.

## 8. Boundaries that must not be violated

1. The HTTP controller never receives the Docker socket.
2. Model-generated code executes only inside a disposable, network-less sandbox.
3. Job checkouts have their `origin` remote removed before any edit.
4. Review and acceptance gates are preconditions for committing — not advisory.
5. Attempt budgets are fixed; infrastructure errors never consume a repair.
6. Publishing is explicit, human-initiated, draft-only, and never merges.
7. `--acceptance-reviewed` represents a human decision.
8. Prompts exceeding the 2000-byte controller limit raise; nothing is truncated.
9. Runtime state stays outside the package and outside Git.
10. `core.java_workflow`'s lazy profile imports stay lazy.

## 9. Future direction *(not implemented)*

The next planned increment is 7C-1: model-proposed scope with a separate human
grant. The current design proposal is
[`milestones/PROPOSAL-7C-1.md`](milestones/PROPOSAL-7C-1.md); it is not yet
implemented and does not weaken committed `.nullcode.json` authority.

After that, the planned autonomy step is GitHub issue/task ingestion feeding the
same bounded execution and draft-PR path. Explicitly **not** present today:
automatic scope grants, autonomous task selection, automatic PR publication,
multi-worker concurrency, non-Java languages, Maven, dependency resolution
inside job containers, or any merge capability.
