# Changelog

Concise project history. Detailed per-increment write-ups live in
[`milestones/`](milestones/); this file is the index, not a replacement.

Dates before the reorganization are not recorded in Git — the repository was
imported as a single "Initial NullBrain server backup" commit — so entries up to
milestone 6 are ordered by increment, not by date.

## Unreleased

### 7C-1 live validation and duplicate-path prompt hardening — 2026-09-26

- Record [Workflow 36](workflows/WORKFLOW-036.md): the first live
  `repo-scope-v1` submission carried the correct profile but a stale deployed
  worker fell through to the legacy Numbers workflow. It failed before
  inference; restarting the worker loaded the merged dispatch code.
- Record [Workflow 37](workflows/WORKFLOW-037.md): live inference job 139 chose
  the natural `IdentifierFormatter` production/test pair, and the unchanged
  900-byte source gate rejected the oversized test before call 2.
- Patient Zero PR #4 added a dedicated ungranted
  `Initials.java` / `InitialsTest.java` pair below the 900-byte limit; the Pi
  verified the updated Gradle project successfully.
- Record [Workflow 38](workflows/WORKFLOW-038.md): live inference job 140 chose
  the intended Initials production/test pair but repeated the production file
  as context. The existing duplicate-path validator failed closed before call 2.
  The selection prompt was 1846 bytes.
- Harden only the call-1 instruction with: `A path may appear in only one
  list; never repeat an edit file as context.` Add a regression assertion that
  the instruction is present and the prompt remains within 2000 bytes.
  Deterministic duplicate rejection remains authoritative; there is no
  deduplication, retry, validator relaxation, authority change or limit change.
- Record [Workflow 39](workflows/WORKFLOW-039.md): execution inference job 141
  ran against Patient Zero base
  `feb39e83c710a1b4c9c07a3a74bf4c260104b022`, whose committed
  `.nullcode.json` omitted the dedicated Initials production/test fixture.
  The model returned an unapproved `src/main/java/lab/TextNormalizer.java`
  path; exact-path authority validation failed closed before any edit.
- Human-directed remediation updated the Java-lab authority manifest on
  `main` at `13d0cb2be9e946a9ca2ea81a040d549818fdb0bf`, adding only
  `src/main/java/lab/text/Initials.java` and
  `src/test/java/lab/text/InitialsTest.java`. Nullbrain runtime code and
  limits are unchanged. Workflow 40 is the next live execution target.


### Planner hardening budget correction — 2026-09-26

- Pi validation of the first Workflow 41 planner-format patch exposed 9
  `test_patient_zero_compat` errors: the added instruction pushed supported
  1-production + 2-test planning prompts over the unchanged 2000-byte ceiling.
- Keep the same prose-only/no-code planning contract, but compress the wording
  to `Steps: prose only; no code, fences, literals, or escapes.`
- No controller limit, repository-context budget, file-count limit or parser
  behavior changed. Re-run the exact planner regression and the full unittest
  suite before the next live workflow.

### Workflow 41 planner-format follow-up — 2026-09-26

- Record [Workflow 41](workflows/WORKFLOW-041.md): selection inference 145 again
  chose exactly the granted Initials production/test pair.
- Planning inference 146 ignored the existing `Do not write code` instruction,
  embedded Java and test source in `steps`, and emitted the illegal JSON escape
  `\'`. Strict JSON parsing failed closed before editing.
- Keep `extract_json()` strict. Harden the shared planning prompt so `steps`
  must be short prose only, with no code, code fences, string literals or
  escaped source snippets. Add a regression assertion for that contract and
  retain the unchanged 2000-byte planning-prompt limit.

### Workflow 40 prompt-budget follow-up — 2026-09-26

- Record [Workflow 40](workflows/WORKFLOW-040.md): selection inference 142 and
  planning inference 143 both chose exactly the granted Initials production/test
  pair on Patient Zero base `d2a356a59cfb339cf935ba1dc2009e2b85ba267b`.
- Edit inference 144 produced a complete `Initials.java` candidate, but the
  following complete `InitialsTest.java` edit prompt measured 2001/2000 bytes
  and failed closed before test editing or verification.
- Keep the 2000-byte limit and all complete Java/task/plan/reference evidence.
  Shorten only redundant static edit-prompt wording by 9 bytes and add an exact
  Workflow 40 regression fixture proving the complete prompt fits without
  truncation.
- The generated production candidate itself omitted the required terminal
  period (`H.J.2` vs `H.J.2.`); candidate verification had not yet run, so
  no conclusion is drawn about repair behavior from Workflow 40.

### 7C-1 — model-proposed scope, human-granted scope — 2026-09-25

- Add the read-only profile `repo-scope-v1` (`submit-scope`). It makes two
  model calls: candidate selection from 7A's committed inventory, then a final
  proposal over complete contents of only the accepted paths. Validation is
  deterministic. Call 2 may only narrow call 1 within each class, so a context
  file can never become edit scope. It does not edit, stage, commit, push,
  run Gradle or publish, and it hands nothing to `repo-execute-v1`.
- A proposal must be a selection current `repo-execute-v1` could make once
  granted: 1+ production and 1+ test file, 3 files at most, 900 bytes each, the
  resulting configuration within 8 + 8 and accepted by 7B's own validators.
  `.nullcode.json`, `build.gradle`, `settings.gradle` and `gradle.properties`
  are never edit scope. At most one context file is allowed, chosen from prompt
  measurements. The 2000-byte limit is unchanged; overflow fails closed.
- Add `python -m nullcode.repo.repo_scope_review <id> --scope-reviewed`. It
  renders the `.nullcode.json` grant diff into the workflow directory and never
  touches the target repository. `--scope-reviewed` is a required human
  attestation, not edit authority. The grant is a human commit.
- The publisher refuses `repo-scope-v1` first: `Scope proposal workflows are
  not publishable`. Worker dispatch now names the profile explicitly, instead
  of letting it fall through to the legacy editing profile.
- Extract 7B/Gradle scope predicates into pure helpers
  (`validate_editable_files`, `validate_editable_test_files`,
  `check_source_limit`, …) with identical behavior, so 7C-1 is judged by the
  same code. No 7B budget, limit or gate changed.
- Tests: 227 → 304 unittest cases (`test_repo_scope_workflow.py`,
  `test_scope_authority.py`), including import-graph, read-audit and
  human-commit tests showing proposals are inert. All recorded mutations are
  caught. pytest: 304 passed plus the same pre-existing collection error.
  **Live Pi validation outstanding.** See
  [MILESTONE-7C-1](milestones/MILESTONE-7C-1.md).

### Workflows 34–35 live validation — 2026-09-25

- Record [Workflow 34](workflows/WORKFLOW-034.md): a second live 7B.2
  `test`-domain route correctly targeted `TextStatsTest.java` for a bad
  generated expectation (`" 123 "` expected 2, actual 3). The accepted route
  was persisted, then the unchanged 2000-byte prompt gate failed closed at
  2300/2000 bytes before repair inference. No commit or publication.
- Record [Workflow 35](workflows/WORKFLOW-035.md): an ASCII-only follow-up
  candidate and the original-suite baseline both passed 46/46, but the
  deterministic added-coverage gate rejected the changed editable test because
  executed JUnit cases did not increase (46 vs 46). No repair routing occurred.
- Human review of Workflow 34 found the generated `Character.isDigit`
  implementation was broader than the stated ASCII `0-9` requirement; that
  observation motivated Workflow 35. Production-domain live repair routing
  remains unproven.
- Documentation only; no runtime artifacts are committed.


### Workflow execution records — 2026-09-25

- Add [`workflows/`](workflows/README.md): durable records of notable live
  workflow executions, kept separate from the capability-focused milestone
  records, with an index, the criteria for writing a record and a template.
- Record [Workflow 25](workflows/WORKFLOW-025.md) (insufficient-test-count
  repair narrowing), [Workflow 26](workflows/WORKFLOW-026.md) (behavioral-delta
  evidence), [Workflow 32](workflows/WORKFLOW-032.md) (the routing
  contradiction that motivated 7B.2) and
  [Workflow 33](workflows/WORKFLOW-033.md), the first live Pi run of 7B.2.
  It **failed safely**: both repair routes were typed `test` routes and were
  accepted, re-verification caught a bad repair, and the 2000-byte prompt
  limit stopped repair 2. No commit or publication. Live production-domain
  routing remains unproven.
- Cross-link the records from the 7B hardening, 7B.1 and 7B.2 milestone
  documents, the milestone index, `PROJECT_STATE.md` and `README.md`. Add a
  live validation section (§15) to 7B.2, and extend the documentation-trail
  convention to cover workflow records.
- Documentation only. No source, test, configuration or runtime change, and
  no runtime artifacts (`jobs/`) committed.

### Milestone 7B.2: typed repair-target routing — 2026-09-25

- Require a typed `fault_domain` (`production` | `test`, exact match only) in
  every `repo-execute-v1` repair-selection reply, and deterministically require
  the named file to be an offered candidate in that domain. Membership comes
  from the approved `editable_files` / `editable_test_files` lists, not from a
  new path rule.
- Missing, malformed, unknown, legacy-shaped and contradictory replies now fail
  the workflow (`failed`) before any repair-edit prompt is built. There is no
  auto-correction, no parsing of `reason`, no reselection and no extra repair.
  An insufficient-executed-test-count failure additionally requires the `test`
  domain.
- Record `fault_domain` in `repair-selection.json`, the repair `result.json`
  and the failure record's repair history; add `repair-routing.json`, which
  preserves every routing reply, accepted or rejected.
- Rework the repair-selection prompt to carry the typed schema and
  domain-grouped candidates, while shrinking it by 29–31 bytes. The 2000-byte
  limit and every budget, cap and gate are unchanged.
- Encode Workflow 32 and its mirror as regression tests; add
  `tests/test_repair_routing.py` (37 tests). Existing canned repair replies
  were updated to the typed shape with the matching domain. Nine mutations
  were applied to throwaway copies, and every one was caught. Suite: 227
  unittest cases, up from 190; pytest 227 passed plus the same pre-existing
  collection error.
- Validated on Linux with canned inference and verification only. **Live Pi
  validation is outstanding.** See [7B.2](milestones/MILESTONE-7B-2.md).

### Patient Zero validation, Workflow 32 and Proposal 7B.2 — 2026-09-25

- Merge Patient Zero compatibility regression coverage (PR #7, `60c15ce`):
  `tests/test_patient_zero_compat.py`, 19 test-only cases on a synthetic
  fixture mirroring the upgraded Java lab's bounded shape. No production
  source changed. Suite: 190 unittest cases.
- Validate Patient Zero live on the Pi: the Java lab builds with Java 21 /
  Gradle 8.14.3, including offline after dependency-cache seeding. Nullbrain
  `pytest` on the Pi reports 190 passed and 1 pre-existing collection error
  (an imported production helper named `test_*` is collected as a test);
  `unittest` runs the same 190 cases cleanly.
- Record Workflow 32 (**failed, safely**): repair selection returned a reason
  blaming the test expectation but targeted the production file;
  `validate_repair_selection(...)` does not check that agreement, and the
  workflow failed closed on `Repair 1 returned unchanged source`. No commit or
  publication.
- Add [Proposal 7B.2](milestones/PROPOSAL-7B-2.md), typed repair-target
  routing, as the immediate next design item. Recommends immediate fail-closed
  on contradictory routing and no reselection. Nothing is implemented; 7C-1
  remains the next autonomy milestone after it.
- Establish the documentation-trail convention in
  [`PROJECT_STATE.md` §11](PROJECT_STATE.md#11-documentation-trail-convention),
  and annotate stale "no milestone document exists" notes below as historical.

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
mutants). *No milestone document existed when this entry was written; see
[ACCEPTED-JAVA-V1](milestones/ACCEPTED-JAVA-V1.md), added 2026-09-21.*

Later extended with `javac-string-array-stream-loop-v1`, a narrowly-matched
compiler repair rule that adds targeted guidance to a repair prompt without
granting an extra attempt. *No milestone document existed when this entry was
written; see [JAVAC-REPAIR-RULE](milestones/JAVAC-REPAIR-RULE.md), added
2026-09-21.*

### Milestone 7B — planned, bounded multi-file execution

`submit-execute` / `repo-execute-v1`. Executes a validated plan across approved
production and test files, up to 3 selected files, 900 bytes of source each, with
Gradle/JUnit verification and up to two repairs. *This entry originally said no
milestone document existed; one now does: [MILESTONE-7B](milestones/MILESTONE-7B.md).*

### Milestone 7A — read-only repository inspection and planning

`submit-plan` / `repo-plan-v1`. Builds a committed-file inventory, has the model
select at most 3 relevant files, and produces a validated JSON plan. Makes no
edits and creates no commits. *This entry originally said no milestone document
existed; one now does: [MILESTONE-7A](milestones/MILESTONE-7A.md).*

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
