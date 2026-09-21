# Milestone 7B: planned, bounded multi-file execution

> **Superseded in part.** The seven stages below are unchanged, but a
> behavioral-delta counterfactual now runs between stage 6 (baseline-test
> regression, including its added-coverage comparison) and stage 7
> (deterministic review, then commit). Without it a candidate could reach
> `succeeded` while changing no production behavior at all. See
> [7B hardening](MILESTONE-7B-HARDENING.md); the test counts at the bottom of
> this document are historical.

`repo_execute_workflow.py` extends the single-file Gradle workflow (Milestone
6) to a small, plan-driven multi-file change: production code and its test
together. Profile `repo-execute-v1`. This is not general repository editing —
file choice is model-proposed but tightly bounded, every edit is verified by
the same offline Gradle/JUnit harness as Milestone 6, and a failing candidate
never reaches a commit.

The workflow runs as seven stages against an isolated, `origin`-less clone on
a task branch:

1. **File selection.** The model picks 2–3 files from the repository's
   already-approved `editable_files` / `editable_test_files` lists (the same
   `.nullcode.json` configuration Milestone 6 uses), and the selection must
   include at least one production file and at least one test file. It
   cannot select anything outside that approved list.
2. **Planning.** Given the full source of the selected files (budgeted to
   ~1100 bytes total, split across files), the model returns a JSON plan —
   summary, per-file reasons, ordered steps, risks — the same shape as
   Milestone 7A's planner, but scoped to files it's actually allowed to edit.
3. **Editing.** Each selected file is edited in its own model call, production
   file(s) first, then test file(s). A test edit is given the full source of
   the selected production file(s) as reference context (never clipped mid
   file) so the model isn't writing assertions blind. Every edit prompt is
   capped at 2000 bytes and construction fails loudly rather than truncating.
   After each file is written, a scope check (`git diff --name-only`) confirms
   the model hasn't touched anything outside the current selection; at the end
   of the stage, every selected file must show a real change.
4. **Candidate verification.** The same Gradle/JUnit harness from Milestone 6
   (`gradle_workflow.verify`) compiles and tests the candidate. If tests were
   part of the selection, the JUnit case count must strictly increase over
   `minimum_tests` — changing a test without adding coverage is treated as a
   failure, not a pass.
5. **Bounded repair (up to 2 attempts).** On a repairable failure, a separate
   model call — given only the diagnostic, not the raw logs — chooses exactly
   one of the originally selected files to fix; it cannot expand scope to a
   new file. A repair that returns byte-identical source is rejected outright
   (no wasted verification cycle on a no-op). Each repair is independently
   re-verified. A non-repairable failure, or a second failed repair, ends the
   job with nothing committed.
6. **Baseline-test regression.** If any *test* file was part of the selection,
   its edited version is temporarily swapped back to the original (base
   commit) content and the suite is re-run before the swap is reverted. This
   is the guard against the model quietly loosening an assertion to make
   broken production code pass — the original test, not just the edited one,
   has to agree the change is correct.
7. **Deterministic review, then commit.** Every changed file goes through the
   same targeted text-based review as earlier milestones. Only after
   selection, verification, regression, and review all pass does the workflow
   `git add` the selected files and commit — never anything the model touched
   outside that set. A final hash comparison against both the pre-edit
   snapshot and the verified candidate snapshot guards against the checkout
   mutating between verification and commit.

## Checks worth knowing about

- `extract_java` requires exactly one fenced ` ```java ` block (or none, if
  the model returned bare source) and a real type declaration matching the
  target filename — a response containing zero or multiple code blocks is
  rejected before it's ever written to disk.
- Selection, editing, and repair are three independently-scoped gates: a file
  can only be repaired if it was selected, and a repair is re-checked against
  the *same* selected-file set afterward, not just the one file it changed.
- Attempt numbering: 1 = selection, 2 = planning, 3 = editing + first
  verification, 4 and 5 = the two possible repairs. The exception handler
  reports the highest attempt directory that actually got created, so a
  failure's reported attempt number tells you which stage it reached.

## Try it

```sh
python3 -m nullcode.core.java_workflow submit-execute \
  --repo /srv/nullbrain/repos/<repo-with-.nullcode.json> --base main \
  --task 'A task touching both a production file and its test.'
python3 -m nullcode.core.java_workflow wait <id>
```

Artifacts under `/srv/nullbrain/jobs/workflow-<id>/`: `candidate-files.json`
(selection), `plan.json`, `attempt-3/` (the edits, per-file prompts/answers,
`candidate-verification/`), `attempt-4/` and `attempt-5/` if repairs ran
(each with its own `diagnostic.txt` and `repair-selection.json`), and
`repository.json` once a commit succeeds.

## What it does not do

It does not consume a 7A plan — it re-selects and re-plans independently,
with its own, stricter file-source rules (production-and-test required, from
an approved list, not an open committed-file inventory). It edits at most 3
files per job. There is no support for adding a brand-new file, only editing
files that are already tracked and pre-approved.

## Validation

`tests/test_repo_execute_workflow.py` exercises `run_job`'s full seven-stage
orchestration with a real Git fixture, real checkouts, canned model answers,
and a canned verifier (no real Ollama, Docker, or Gradle). It covers:

- a clean two-file (production + test) edit passing verification and
  committing only those two files, with the source repository left untouched
- selection being rejected without at least one production and one test file
- an "edit" that returns byte-identical content being rejected as not a real
  edit
- the two-attempt repair loop actually working: a repairable failure gets
  diagnosed, repaired by a second model call, and re-verified to success
- a repair that names a file outside the original selection being rejected
- a repair returning identical source being rejected without a wasted
  re-verification cycle
- a non-repairable failure stopping immediately, with no repair attempt and
  no repository mutation
- **the baseline-regression gate genuinely blocking a bad commit** — a case
  where the edited test passes against the edited code but the *original*
  test would not, confirming Stage 6 actually catches that rather than
  trusting the candidate result
- the coverage check rejecting an edited test file that doesn't add any new
  JUnit cases

10 tests, run with `python3 -m unittest tests.test_repo_execute_workflow -v`.
One gate (the identical-repair-source check) was deliberately disabled in a
throwaway copy of the source to confirm the corresponding test actually fails
without it — this suite catches regressions, not just describes intended
behavior. Confirmed passing on the Pi (ARM64) as part of the full 93-test
suite.
