# Changelog

Concise project history. Detailed per-increment write-ups live in
[`milestones/`](milestones/); this file is the index, not a replacement.

Dates before the reorganization are not recorded in Git — the repository was
imported as a single "Initial NullBrain server backup" commit — so entries up to
milestone 6 are ordered by increment, not by date.

## Unreleased

### Milestone 7C-2 — 2026-09-21

- Add profile-specific validation and explicit draft-PR publishing for
  successful multi-file `repo-execute-v1` workflows, including repaired jobs.
- Check both verification snapshots, original-test regression, protected files,
  executed-case increases, exact committed scope, and per-file review hashes.
- Preserve existing draft-only delivery and preview behavior. Scope proposals
  (7C-1) remain unimplemented. See [7C-2](milestones/MILESTONE-7C-2.md) for test
  coverage and live-deployment limits.

### Repository reorganization — 2026-09-21

Structural and documentation refactor. **No behaviour changed.**

- Python source became a proper `src/`-layout package `nullcode`, split into
  `core/`, `repo/`, `gradle/`, `publish/` and `fixtures/` subpackages.
- All intra-project imports became absolute package imports
  (`from nullcode.core.java_workflow import ...`). The `sys.path` workaround in
  `test_accepted_workflow.py` was removed.
- Tests moved from beside the source to `tests/`.
- The Rust controller moved from `src/nullcode/` to its own `rust/` project
  alongside its `Dockerfile`, `.dockerignore` and README.
- Compose files were renamed for clarity: `compose/nullcode.compose.yml` and
  `compose/ollama.compose.yml`. The controller build context now points at `rust/`.
- The systemd unit moved to `deploy/` and now runs
  `python3 -m nullcode.core.java_workflow run` — **the live Pi service needs
  updating**; see `PROJECT_STATE.md` §4.
- `pyproject.toml` added (setuptools, no third-party dependencies) with pytest
  configuration, so imports are deterministic with or without an install.
- Milestone documents moved to `docs/milestones/`; `README.md`,
  `ARCHITECTURE.md`, `PROJECT_STATE.md` and this file were written.
- Pre-install backup directories moved to `checkpoints/` and **retained** —
  no commit in this repository represents the states they hold.
- Test counts unchanged: 77 Python, 3 Rust.

## Increments

### Acceptance-gated production edits (`accepted-java-v1`)

One production file edited against committed, human-reviewed acceptance tests.
Requires an explicit `--acceptance-reviewed` human attestation. Hashes protected
test files before and after; skipped tests, insufficient test counts, tampered
snapshots and failed container cleanup all block the commit. At most two repairs.
Added `prepare_acceptance.py` (isolated review checkout, never commits) and
`check_acceptance.py` (runs a proposed suite against a reference plus three
mutants). *No milestone document exists.*

Later extended with `javac-string-array-stream-loop-v1`, a narrowly-matched
compiler repair rule that adds targeted guidance to a repair prompt without
granting an extra attempt. *No milestone document exists.*

### Milestone 7B — planned, bounded multi-file execution

`submit-execute` / `repo-execute-v1`. Executes a validated plan across approved
production and test files, up to 3 selected files, 900 bytes of source each, with
Gradle/JUnit verification and up to two repairs. *No milestone document exists.*

### Milestone 7A — read-only repository inspection and planning

`submit-plan` / `repo-plan-v1`. Builds a committed-file inventory, has the model
select at most 3 relevant files, and produces a validated JSON plan. Makes no
edits and creates no commits. *No milestone document exists.*

### [Milestone 6](milestones/MILESTONE-6.md) — configurable Gradle/JUnit tasks

`submit-gradle` / `gradle-junit-v1`. Offline Gradle 8.14.3 / JDK 21 / JUnit
Jupiter builds in a dependency-cache image, driven by a committed `.nullcode.json`
listing up to 8 selectable files. JUnit XML must show at least the configured
number of non-skipped passing cases. 50 Python tests at the time.

### [Milestone 5](milestones/MILESTONE-5.md) — draft pull-request delivery

`publish_workflow.py`, an explicit CLI step. Preview mode makes no GitHub calls;
`--publish` pushes the task branch and opens a draft PR using the host's `gh`
credentials. Verifies workflow evidence, hashes, a clean checkout, exactly one
commit, and that GitHub `main` still equals the recorded base. Never merges,
never force-pushes, never pushes `main`.

### [Milestone 4](milestones/MILESTONE-4.md) — targeted review before committing

A passing review became a precondition for committing, alongside build/test
success. Three deterministic rules (impossible int range checks, demo `main`,
console output), with findings, rule-set version and SHA-256 hashes of the
reviewed source and diff saved as evidence. One review correction permitted,
three candidate attempts total. 29 Python tests at the time.

### [Milestone 3](milestones/MILESTONE-3.md) — repository-backed Java jobs

`submit-repo` / `numbers-jdk21-v1`. Records the base commit at submission, clones
the local repository `--no-hardlinks` into the job directory, branches
`agent/workflow-<id>`, removes `origin`, edits only the configured source file,
preserves the committed tests, and saves a Git patch. No push, no PR. Added the
`repo_spec` column. 19 Python tests at the time.

### [Milestone 2](milestones/MILESTONE-2.md) — Java verification and one repair

Introduced the trusted Python host worker that drives inference through the Rust
API and launches restricted Java containers, keeping the Docker socket out of the
HTTP controller. Fixed `Numbers.max` task with eight fixed checks; workflow IDs
distinct from inference-job IDs; one repair attempt; restart recovery; per-attempt
artifact directories.

### Milestone 1 — local inference queue

Rust/Axum API on `127.0.0.1:8080`, SQLite persistence, one Ollama worker.
Generation only: no repository access, no code execution, no compilation, no PRs.
See [`rust/README.md`](../rust/README.md).
