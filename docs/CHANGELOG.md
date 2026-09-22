# Changelog

Concise project history. Detailed per-increment write-ups live in
[`milestones/`](milestones/); this file is the index, not a replacement.

Dates before the reorganization are not recorded in Git — the repository was
imported as a single "Initial NullBrain server backup" commit — so entries up to
milestone 6 are ordered by increment, not by date.

## Unreleased

### Documentation synchronization — 2026-09-21

- Refresh README, architecture, project state and 7B.1 records against current
  `main` after the live Pi validation.
- Replace stale refactor-era claims about test counts, Git history, deployment
  certainty and outstanding smoke tests with current or explicitly historical
  wording.
- Document the already-implemented `accepted-java-v1` workflow and the narrow
  `javac-string-array-stream-loop-v1` repair rule, closing the remaining named
  workflow documentation gaps.
- Record the agreed near-term direction without implementing it: 7C-1
  model-proposed/human-granted scope, followed by GitHub issue/task ingestion.

### Milestone 7B.1: behavioral-delta evidence hardening — 2026-09-21

- Record an explicit `evidence_level` with every behavioral-delta result:
  `behavioral` for a candidate suite that fails against pinned-base
  production, `structural` for one that cannot be compiled against it. Nothing
  reports the two as equal-quality evidence any more, and structural evidence
  states in its own summary that it does not prove runtime behavioral novelty.
- Choose the API compile-failure policy from a recorded experiment
  (`tests/test_behavioral_delta_policy.py`) rather than intuition: a legitimate
  new API and a deliberately trivial one produce identical hybrid evidence, so
  rejecting structural evidence would reject every legitimate new-API task.
  Policy A (allow, labelled structural) is selected and documented; Policy B
  has no robust implementation without a Java/JUnit parser, which stays out of
  scope.
- Give a no-behavioral-delta candidate exactly one semantic re-plan, budgeted
  separately from the unchanged two-attempt repair loop. The superseded
  candidate is reset to pinned-base content and the replacement re-runs every
  gate from planning onwards on its own evidence. A repeated no-delta, an
  invalid diagnosis or a widened plan terminates fail-closed, unchanged.
- Document, in code and in prose, that the re-plan diagnostic prompt is
  advisory: scope, verification, coverage, regression, counterfactual and
  review remain the enforcement.
- Persist semantic re-plan telemetry (`semantic-replan.json`) and monotonic
  stage timings (`timing.json`) for every terminal state, so the budget and
  the counterfactual's cost can be revisited from observed data. No
  performance threshold is introduced.
- Broaden validation: multi-file hybrids (2+1, 1+2 and 2+2), a 48-case
  eight-file lab, legitimate and trivial new-API fixtures, and adversarial
  test-side logic fixtures.
- Record the test-side logic risk as a known limitation with concrete
  evidence, plus an advisory, non-gating diff statistic. No blanket
  prohibition on test helpers was introduced.
- Publisher stays draft-only and `succeeded`-gated; it now refuses records
  whose novelty evidence is missing or inconsistent and states the evidence
  level in the preview. Suite: 171 tests, up from 133.
- After merge to `main` (`84f0dc1`), validate 7B.1 on the Raspberry Pi: 171/171
  tests pass; bounded no-delta semantic re-plan/exhaustion, behavioral novelty,
  structural API novelty, and hybrid snapshot integrity are exercised live.
  Existing repair, scope, regression, publishing and human-acceptance boundaries
  remain intact. See [7B.1](milestones/MILESTONE-7B-1.md).

### Milestone 7B hardening: behavioral-novelty validation — 2026-09-21

- Add a behavioral-delta counterfactual stage to `repo-execute-v1`, between the
  added-coverage comparison and targeted review. The candidate test suite is
  run against pinned-base production; a suite that fully passes there proves
  nothing about the production edit and is rejected.
- Construct the hybrid state deterministically from the base commit with only
  approved candidate test sources overlaid, and pin it to the verifier's own
  snapshot hashes so candidate production cannot enter it.
- Treat a test-compilation failure caused by API absent from the base as
  distinguishing evidence, decided from the compiler output; everything the
  evidence does not explain is infrastructure failure and fails closed.
- Add the terminal state `rejected-no-behavioral-delta`, distinct from the
  generic `failed` state and recorded with `finished_at` like other terminal
  states. Nothing is committed or publishable from it.
- Record `behavioral-delta.json` and a `behavioral-delta-verification/`
  directory (manifest, hashes, image id, compile and test logs, JUnit summary,
  classification, diagnostic) as auditable evidence.
- Encode Workflow 26 as an automated regression fixture, plus genuine-change,
  new-API and infrastructure-failure fixtures. Existing gates, scopes, floors
  and the repair budget are unchanged. Suite: 133 tests. Live Pi smoke test
  still outstanding. See
  [7B hardening](milestones/MILESTONE-7B-HARDENING.md).

### Milestone 7C-2 — 2026-09-21

- Add profile-specific validation and explicit draft-PR publishing for
  successful multi-file `repo-execute-v1` workflows, including repaired jobs.
- Check both verification snapshots, original-test regression, protected files,
  executed-case increases, exact committed scope, and per-file review hashes.
- Preserve existing draft-only delivery and preview behavior. Scope proposals
  (7C-1) remain unimplemented. See [7C-2](milestones/MILESTONE-7C-2.md) for test
  coverage and live-deployment limits.
- Harden the new validator after a Linux review: re-derive the approved
  `editable_test_files` scope with the producer's own rules instead of trusting
  the committed field, and fold a lone CR when reading committed text so a
  stray CR no longer fails a matching review hash. Suite: 105 tests.
- Record Linux validation of 7C-2. Live Ollama, Docker/Gradle, GitHub and Pi
  checks remain outstanding.
- Add [7C-1 scope proposals](milestones/PROPOSAL-7C-1.md) as a design proposal.
  Nothing is implemented.

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
