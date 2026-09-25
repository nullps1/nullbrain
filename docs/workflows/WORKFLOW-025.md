# Workflow 25

A live `repo-execute-v1` run whose candidate compiled and ran green but had
replaced the existing test suite with a much smaller one. The minimum
executed-test floor correctly rejected it. However, the workflow then
terminated instead of entering the bounded repair, even though the selected
scope already held the test file that could restore the coverage. It is the
live evidence behind the deterministic insufficient-test-count repair
narrowing (`88c015b`).

The only repository source for this run is the commit message of `88c015b`
(merged in PR #3 as `f5a5db6`). No milestone or state document described
Workflow 25 before this record, so most metadata is not recorded.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 25 |
| Profile | `repo-execute-v1` |
| Date | not recorded in repository documentation (before `88c015b`, 2026-09-21) |
| Environment | live run; host details not recorded in repository documentation |
| Target repository | not recorded in repository documentation |
| Base branch / commit | not recorded in repository documentation |
| Task | not recorded in repository documentation |
| Inference job IDs | not recorded in repository documentation |

## Selected scope

Not recorded by path. The selected scope included an editable test file: the
commit message says the selected scope "already contained the editable test
file that could fix it".

## Execution path

The individual state sequence is not recorded. Per `88c015b`, the candidate
reached candidate verification and the workflow terminated there, before any
repair.

## Model decisions

The model's edit replaced an 8-case test suite with a 1-case suite. No
routing or repair decision was made, because repair was never entered.

## Verification results

| Check | Result |
| --- | --- |
| Candidate compile | clean |
| Candidate tests | green, zero JUnit failures |
| Executed test cases | **5**, against a minimum floor of **12** |
| Verifier result | `passed=False`, `repairable=False` |

At the time, `gradle_workflow.verify()` folded the minimum-test floor into
`passed` but not into `repairable`, which fired only for a conventional
failing JUnit run (test exit 1 with failures > 0). A green-but-short
candidate was therefore not repairable. `verification_diagnostic()` also had
no branch for this state and fell through to "Verification failed without a
concise diagnostic".

## Final outcome

**Failed** at candidate verification, without entering repair. The exact
terminal message and any commit, publication or merge are not recorded in
repository documentation. A candidate that fails verification does not reach
the commit stage.

## Safety behavior observed

- **Minimum executed-test floor held.** A green run with too few cases did not
  pass.
- **Fail-closed.** The unrecognised state ended the workflow rather than being
  treated as success.
- The cost was a lost repair opportunity, not a safety breach.

## Artifacts

Not recorded in repository documentation. By the runtime convention the run's
artifacts live under `jobs/workflow-25/` on the host, outside Git.

## What we learned

A candidate can pass every compile and test check while quietly deleting
coverage. The floor catches that, but a floor that blocks without a route to
repair wastes the bounded repair budget, whose purpose is exactly this kind of
recoverable fault.

Later work (`88c015b`, after this run) introduced, locally in
`repo-execute-v1` and without changing the shared `verify()` contract used by
`gradle-junit-v1` and `accepted-java-v1`:

- `insufficient_test_count()` / `candidate_repairable()`, which recognise the
  state only with a clean compile, a clean test run, zero JUnit failures, no
  infrastructure error, a tolerable cleanup and an already-selected editable
  test file;
- **repair-target narrowing to the selected test files** for this failure
  reason only, because a production edit cannot restore deleted cases;
- an explicit diagnostic branch for the shortfall;
- prompt guidance to keep existing `@Test` methods (guidance only; the gates
  remain the enforcement).

No gate was relaxed and no budget grew: `minimum_tests`, the original-suite
regression, the skipped-tests exclusion, the added-coverage comparison and the
two-attempt repair limit were all unchanged. The narrowing removes a choice;
it never adds authority.

## Follow-up

- The narrowing is carried forward unchanged by
  [Milestone 7B.2](../milestones/MILESTONE-7B-2.md). Since 7B.2, an
  insufficient-count repair route must additionally declare
  `fault_domain: "test"`.
- The narrowing is covered by the `repo-execute-v1` tests added in `88c015b`,
  including `test_insufficient_count_repair_is_offered_only_selected_test_files`
  and `test_insufficient_count_repair_uses_the_existing_two_attempt_budget`.
