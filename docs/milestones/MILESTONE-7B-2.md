# Milestone 7B.2: typed repair-target routing

A narrow hardening increment inside `repo-execute-v1`. The repair-selection
reply now carries its fault diagnosis as a typed, validated field. A reply
whose diagnosis and target disagree fails closed at the point it is made,
with an accurate reason. Before this change it failed one model call later
with a misleading one.

It implements [Proposal 7B.2](PROPOSAL-7B-2.md), which is kept as the design
record, with the decisions that proposal left open now settled (§9). It is
not an autonomy step: [7C-1](PROPOSAL-7C-1.md) remains the next autonomy
milestone.

Implementation commit: `3cd3ed1` on branch
`claude/patient-zero-7b2-design-58zo10`, based on `main` at `119cd3b`.

## 1. Problem

When candidate verification fails and the failure is repairable, the workflow
asks the model which already-selected file to repair, validates the reply,
then asks for a complete replacement of that file. The selection prompt already
asked the model to decide "whether the failure comes from the implementation
or from an incorrect test expectation". That decision lived only in free-text
`reason`, which the validator never read. So a reply could diagnose one fault
domain and route the repair to the other.

## 2. Workflow 32 evidence

Workflow 32 ran live on the Raspberry Pi against the Patient Zero Java lab
after PR #7 merged. The full run record, including inference job IDs and the
execution path, is [Workflow 32](../workflows/WORKFLOW-032.md).

| Field | Value |
| --- | --- |
| Task | "Add a small tested behavior improvement consistent with the existing project API." |
| Selected scope | `src/main/java/lab/TextStats.java`, `src/test/java/lab/TextStatsTest.java` |
| Plan | "Add a method to count punctuation marks in the text." |
| Verification | `countPunctuation(): expected: <4> but was: <31>` |
| Routing reply | `{"file": "src/main/java/lab/TextStats.java", "reason": "The test expectation is incorrect. …"}` |
| Outcome | failed on `Repair 1 returned unchanged source`; no commit, no publication |

The production method was correct: the input held 31 punctuation characters.
The test expectation was wrong. The reply said so, then named the production
file. The old validator accepted it because it checked only JSON shape,
approved scope and a non-empty reason.

## 3. Implemented schema

```json
{
  "fault_domain": "production" | "test",
  "file": "selected/path.java",
  "reason": "short evidence-based explanation"
}
```

All three fields are required. `file` stays required even when the chosen
domain offers exactly one candidate.

`FAULT_DOMAINS = ("production", "test")` in
`src/nullcode/repo/repo_execute_workflow.py` is the whole vocabulary.

## 4. Deterministic invariants

`validate_repair_selection(data, candidates, production, tests,
required_domain=None, repair_number=1)` returns `(fault_domain, file, reason)`.
It rejects, in this order:

1. a reply that is not a JSON object;
2. a missing `fault_domain`;
3. a `fault_domain` that is not a string or not exactly `"production"` or
   `"test"`. There is no case folding, no trimming, no alias, no default, and
   no inference from the file or the reason;
4. a `file` that is not one of the candidates offered this round. This is the
   pre-existing check, with its message unchanged (`Repair selected an
   unapproved file`);
5. a domain other than `required_domain`, when one applies. For an
   insufficient executed-test-count failure it is `"test"`;
6. a `file` not listed under the stated domain (the contradiction check);
7. an empty or non-string `reason`.

Domain membership comes from `repair_route_domains(candidates, production,
tests)`: the offered candidates intersected with the approved
`editable_files` and `editable_test_files` lists the selection was already
validated against. The prompt uses that same function, and so does the
validator. No second path-classification system was introduced. Grouping can
only remove choices.

What the validator deliberately does **not** do:

- **Correct anything.** A contradiction is never resolved by switching the
  domain to fit the file or the file to fit the domain.
- **Parse `reason`.** It stays human-readable evidence.
- **Ask again.** A rejected reply ends the workflow. There is no reselection
  and no routing retry, and no extra repair allowance exists.

### What this proves, honestly

The validator checks that two model claims **agree**. It cannot check that
either is **right**. A reply of `{"fault_domain": "production", "file":
<production>, "reason": "the test expectation is wrong"}` is consistent in its
typed fields and is accepted: the contradiction is in prose, and prose is not
parsed. That path then behaves exactly as before 7B.2. The unchanged-source
guard, re-verification and every later gate remain the enforcement.
`test_reason_is_evidence_only_and_never_parsed` pins this limit so it cannot
quietly turn into a claim the system does not support.

## 5. Failure behavior

A routing violation raises `ValueError` inside `run_job`. The existing handler
turns it into:

- workflow status `failed`, with the validator's message. No new terminal
  status was added;
- an attempt row with `phase="error"` at the repair attempt directory;
- no repair-edit prompt, no repair inference call, no file written, no
  verification, no commit, nothing publishable.

`timing.json` and `semantic-replan.json` are still written, as for every
terminal state.

| Case | Message |
| --- | --- |
| missing domain | `Repair N routing requires fault_domain` |
| invalid domain | `Repair N routing has invalid fault_domain: <json value>` |
| file not offered | `Repair selected an unapproved file: <path>` (unchanged) |
| wrong domain for failure class | `Repair N routing is contradictory: this failure requires fault_domain 'test'` |
| domain/file disagreement | `Repair N routing is contradictory: fault_domain '<d>' but <path> is not a selected <d> file` |

An unchanged-source repair after a *consistent* route is still terminal
(`Repair N returned unchanged source`), with no reselection and no further
repair. `test_unchanged_source_after_consistent_routing_stays_terminal` pins
that.

## 6. Artifact changes

Under `attempt-<n>/` for each repair round:

| File | Change |
| --- | --- |
| `selection-answer.txt` | unchanged; the raw reply, written before validation |
| `repair-routing.json` | **new**. Written for every routing reply, accepted or rejected: `repair_number`, `offered` (candidates by domain), `required_domain`, `response` (the parsed reply, or `null` if it was not JSON), `accepted`, `error` |
| `repair-selection.json` | adds `fault_domain`; written only for an accepted route, as before |
| `result.json` (repair) | adds `fault_domain` alongside `repair_target` and `repair_reason` |
| candidate failure `result.json` | each `repairs[]` entry adds `fault_domain` |

No existing field was removed. The 7C-2 publisher does not read repair
records, so it is unchanged.

## 7. Prompt budget

The prompt now asks for `fault_domain` and lists candidates as
`Production files: [...]` and `Test files: [...]`. The old version had a
single `Selected files: [...]` line. It keeps the instruction to classify
production against test, the explicit JSON schema, the warning that a JUnit
expected value may itself be wrong, and the strict `> N` guidance. The wording
was tightened so the new prompt is **smaller** than the old one. The 2000-byte
limit is unchanged, and oversize still raises.

Measured with the old and new `repair_selection_prompt` on identical inputs.
Unless stated otherwise, the task and diagnostic are Workflow 32's (81 and 173
bytes):

| Shape | New bytes | Old bytes |
| --- | --- | --- |
| Fixed text (empty inputs) | 542 | 571 |
| Workflow 32: `TextStats` + `TextStatsTest` | 920 | 951 |
| Patient Zero 1+1, all 15 pairs | 916–946 | max 977 |
| Largest approved 1+1 pair (`ReportFormatter` + `ReportFormatterTest`) | 946 | 977 |
| Patient Zero 1+1 with the compatibility suite's task and diagnostic | max 949 | max 980 |
| Patient Zero 1+2, all 15 | max 997 | max 1028 |
| Patient Zero 2+1, all 30 | max 993 | max 1024 |
| Worst case: 500-byte task, 700-byte diagnostic, 220-char plan summary, three longest paths | **raises** | raises |
| Worst case, largest 1+1 pair | **raises** | raises |

Headroom at the extremes: with a 700-byte diagnostic and a 220-character plan
summary, the largest task that still fits is 440 bytes for the largest 1+1
pair (old: 409) and 389 bytes for the three longest paths (old: 358). A
500-byte task with a maximal diagnostic still fails closed, exactly as it did
before 7B.2.

The repair **edit** prompt is untouched. In one 2+1 shape, a consistent
test-domain route passes routing, and the test repair's edit prompt then
needs 2023 bytes, because it carries both production files as reference
context. It fails closed there with `nothing truncated`. That limit predates
7B.2; the Patient Zero compatibility tests already document it.
`MultiFileRoutingTests.test_two_production_one_test` pins that routing
grants nothing that bypasses it.

## 8. Regression coverage

New module `tests/test_repair_routing.py`, 37 tests. It uses the existing
harnesses: real Git fixture and checkouts, canned inference and a canned
verifier.

| Required coverage | Test(s) |
| --- | --- |
| Workflow 32 contradiction | `Workflow32RegressionTests.test_workflow_32_contradiction_fails_closed_before_any_repair`, `RoutingValidatorTests.test_workflow_32_contradiction_is_rejected` |
| Mirror contradiction | `…test_mirror_contradiction_fails_closed_before_any_repair`, `…test_mirror_contradiction_is_rejected` |
| Valid test-domain routing | `…test_consistent_test_routing_repairs_the_test_and_succeeds`, `…test_valid_test_domain_routing` |
| Valid production-domain routing | `…test_consistent_production_routing_repairs_production`, `…test_valid_production_domain_routing` |
| Missing / null / non-string / empty / `"Test"` / `" test"` / `"both"` / `"unknown"` / legacy shape | `MALFORMED_REPLIES` (14 cases) in `…test_malformed_domains_are_rejected_not_normalized` and `…test_malformed_routing_replies_fail_closed_in_the_workflow` |
| Unapproved file still fails | `…test_unapproved_file_still_fails_in_either_domain`, `…test_unapproved_file_still_fails_with_a_valid_domain`, `…test_selected_but_not_offered_file_is_unapproved` |
| Insufficient test count requires the test domain | `…test_insufficient_test_count_requires_the_test_domain`, `…test_insufficient_test_count_requires_test_domain_in_the_workflow`, `…test_insufficient_test_count_with_test_domain_proceeds` |
| 2 production + 1 test | `…test_two_production_one_test_selection`, `MultiFileRoutingTests.test_two_production_one_test` |
| 1 production + 2 tests | `…test_one_production_two_test_selection`, `MultiFileRoutingTests.test_one_production_two_tests` |
| Repair after semantic re-plan | `ReplanRoutingTests` (contradiction fails at attempt 9; a consistent route repairs) |
| Two code repairs remain the maximum | `…test_exactly_two_code_repairs_remain_the_maximum` |
| No reselection call | every rejection asserts the exact prompt count with valid replies still queued; `…test_routing_failure_on_the_second_repair_does_not_reselect` |
| `fault_domain` in `repair-selection.json` / `result.json` | the consistent-routing tests; `…test_repair_history_in_the_failure_record_carries_fault_domain` |
| Prompt ≤ 2000 for Patient Zero 1+1 | `RepairSelectionPromptTests.test_patient_zero_one_plus_one_shapes_fit` (+ `…three_file_shapes_fit`) |
| Worst case fails closed | `…test_worst_case_inputs_fail_closed_rather_than_truncate` |
| Prompt is advisory | `…test_prompt_is_advisory_the_validator_rejects`, `…test_reason_is_evidence_only_and_never_parsed` |

Each workflow-level rejection asserts:
- the routing prompt was the last model call;
- only the first verification ran;
- no `repair-prompt.txt`, `repair-answer.txt`, `repair-selection.json`,
  repair `candidate-verification/` or repair `result.json` exists;
- `selection-answer.txt` and `repair-routing.json` hold the rejected reply;
- both checkout files still hold the candidate content;
- `HEAD` is still the base commit, and no `repository.json` exists.

**Existing tests updated deliberately.** Every canned repair reply in
`tests/test_repo_execute_workflow.py` (`REPAIR_TEST_FILE`, `REPAIR_PROD_FILE`
and five inline replies) and one in `tests/test_repo_execute_publish.py` now
carries the domain matching its intended file, so each still protects the same
property. The two unselected-file replies carry `"production"`, so they still
reach, and fail, the unapproved-file check.
`test_insufficient_count_repair_is_offered_only_selected_test_files` asserted
on the old `Selected files:` line. It now asserts that the production path
appears nowhere in the routing prompt and that `Production files:` is `[]`.
That is stricter than before. No assertion was removed.

## 9. Design decisions settled here

The proposal's open questions, answered for this increment:

| Question | Decision |
| --- | --- |
| Terminal status | ordinary `failed`; no new status |
| Require `file` when a domain has one candidate | yes, always |
| Reselection | none; a rejected reply ends the workflow |
| Prompt headroom | reworded to be smaller than before; limit unchanged |
| Unchanged-source signal | stays terminal; recorded only through existing artifacts; no retry |
| Vocabulary growth | exactly two values; revisit only with recorded data |

## 10. Mutation results

Each mutation was applied to a throwaway copy of `src/` and `tests/` in a
temporary directory, and the full unittest suite was run against it. Nothing
in the repository was mutated. "Failures" counts failing test *and* subtest
results as reported by unittest; the listed tests are the distinct test
methods that failed.

| # | Mutation | Result | Caught by (distinct tests) |
| --- | --- | --- | --- |
| 1 | Domain/file agreement check removed | 18 failures | 12 tests, incl. the Workflow 32 and mirror regressions (workflow and validator level), both multi-file shapes, the re-plan contradiction, `…second_repair_does_not_reselect`, `…prompt_is_advisory_the_validator_rejects` |
| 2 | Domain auto-corrected to match the file | 18 failures | the same 12 tests |
| 3 | File auto-corrected to the domain's first file | 18 failures | the same 12 tests |
| 4 | Missing `fault_domain` defaulted from the file's type | 6 failures | `…malformed_domains_are_rejected_not_normalized`, `…malformed_routing_replies_fail_closed_in_the_workflow` (legacy-shape and missing-domain subtests) |
| 5a | Unknown domains accepted (vocabulary check removed, unknown domain treated as unconstrained) | 21 failures, 1 error | the two malformed-domain tests |
| 5b | Domain case-folded and trimmed into validity | 6 failures | the two malformed-domain tests (`"Test"`, `" test"`, `"test "`) |
| 6 | One further routing call allowed after a rejected reply | 28 failures | 13 tests, incl. the Workflow 32 and mirror regressions, the malformed/unapproved/unparseable workflow tests, both multi-file shapes, the re-plan contradiction, and three pre-existing unapproved-file tests in `test_repo_execute_workflow.py` |
| 7 | Repair loop widened to `(1, 2, 3)` | 3 failures | `…exactly_two_code_repairs_remain_the_maximum`, the pre-existing `test_insufficient_count_repair_uses_the_existing_two_attempt_budget` and `test_a_replanned_candidate_still_gets_two_ordinary_repairs` |
| 8 | `fault_domain` not persisted in `repair-selection.json`, repair `result.json` or repair history | 2 failures, 6 errors | 8 tests: every consistent-routing test that reads the artifacts, plus `…repair_history_in_the_failure_record_carries_fault_domain` |

All mutations were constructed honestly against the real code. One note on 5a:
simply deleting the vocabulary check would not accept an unknown domain,
because the agreement lookup then raises `KeyError` and the workflow still
fails. The mutation therefore also makes an unknown domain unconstrained
(`.get(domain, candidates)`). That is what a genuinely lenient
implementation would look like.

## 11. Validation

Linux x86-64, Python 3.11, no Docker daemon, no Ollama. Canned inference and
verification, as for every earlier milestone's development validation.

| Check | Before (`119cd3b`) | After |
| --- | --- | --- |
| `python3 -m unittest discover -s tests` | 190 tests, OK | **227 tests, OK** |
| `pytest -q` | 190 passed, 1 error | **227 passed, 1 error** |
| `tests/test_repair_routing.py` | — | 37 tests, OK |
| `tests/test_repo_execute_workflow.py` | 39, OK | 39, OK |
| `tests/test_behavioral_delta_evidence.py` | 31, OK | 31, OK |
| `tests/test_repo_execute_publish.py` | 15, OK | 15, OK |
| `tests/test_patient_zero_compat.py` | 19, OK | 19, OK |
| `git diff --check` | — | clean |

The single pytest error is the pre-existing, unrelated collection artifact.
pytest collects the imported production helper `test_side_logic_observation`
as a test, and it fails at setup with `fixture 'patch' not found`. 7B.2 neither
fixes it nor adds to it: the new module imports its sibling test modules, but
no function named `test_*`.

## 12. What this does not change

- the two-repair loop (`for repair_number in (1, 2)`) and
  `SEMANTIC_REPLAN_BUDGET = 1`;
- the 2000-byte controller prompt limit, the 700-byte diagnostic cap, the
  900-byte source limit and the 3-file selection cap;
- file selection, planning, editing, editable scope and the insufficient-count
  candidate narrowing;
- the unchanged-source guard, scope check after repair, Gradle/JUnit
  verification, minimum executed-test floor, baseline regression,
  added-coverage comparison, behavioral-delta counterfactual, evidence levels
  and targeted review;
- `accepted-java-v1`, the 7C-2 publisher, draft-only publication,
  `succeeded`-only eligibility and human acceptance.

## 13. Limitations and what remains unproven

- **Consistent-but-wrong routing is not detected.** Typed routing narrows the
  Workflow 32 failure class; it does not eliminate it (§4).
- **The model may not state its domain honestly.** Under the new schema,
  Workflow 32's model could as easily have replied `production` + production
  file. Only live runs will show how often contradictions are now caught at
  routing, and how often they slip through consistently.
- **The 2+1 test-repair edit budget** still fails closed in some shapes (§7).
  That is pre-existing and out of scope.
- **Worst-case inputs** still exceed the prompt limit and fail closed. The
  prompt got smaller, but the limit is not raised.
- **Live Pi validation is outstanding.** Nothing here has run against live
  Ollama, Docker/Gradle or the Patient Zero lab. The plan below has not been
  executed.

  *Historical as of this record's writing. The first live run since then is
  [Workflow 33](../workflows/WORKFLOW-033.md); see §15.*

## 14. Live validation still to do on the Pi

1. Run the full suite under both `unittest` and `pytest`, and record them
   separately.
2. Re-run Workflow 32's task against the Patient Zero lab. Model output is not
   deterministic, so record what happens rather than expecting one path:
   - a contradictory reply must fail at routing, with no repair-edit call;
   - a consistent `test` reply must repair the test and pass or fail on an
     existing gate;
   - a consistent-but-wrong `production` reply must behave as before
     (unchanged-source guard or later gates).
3. Run at least one task whose natural failure is a production bug, and
   confirm `production` routing still repairs production end to end.
4. Confirm `repair-routing.json` and `fault_domain` appear for every repair.
5. Record the workflow IDs, outcomes and any failures here and in
   `PROJECT_STATE.md`, per the documentation-trail convention.

## 15. Live validation record

Live runs against this milestone are recorded under
[`../workflows/`](../workflows/). This section summarises what they establish
for 7B.2. Each workflow record holds the run's details.

**[Workflow 33](../workflows/WORKFLOW-033.md): failed safely.** This was the
first live Pi run under 7B.2 and re-ran Workflow 32's task (§14 step 2). Both
repair routes were typed `test` routes to the selected test file. Both were
accepted, and both wrote `repair-routing.json` and `repair-selection.json`.
Repair 1 produced contradictory test expectations, and re-verification caught
them (48 tests / 2 failures before, 49 / 3 after). The repair-2 edit never ran:
the unchanged 2000-byte prompt limit failed closed with `Complete repair
context needs 2270/2000 bytes; nothing truncated`. No scope expansion, no
commit, no publication.

Against §14:

| Step | Status |
| --- | --- |
| 1. Suite on the Pi under `unittest` and `pytest` | not recorded in repository documentation |
| 2. Re-run Workflow 32's task | done: Workflow 33 took the consistent-`test` path and failed on existing gates (re-verification, then the prompt limit). A live contradiction rejection was not observed. |
| 3. Production-domain task end to end | **outstanding**; live production-domain routing is unproven |
| 4. `repair-routing.json` and `fault_domain` for every repair | observed for both repair routes in Workflow 33 |
| 5. Record IDs and outcomes | this section, [Workflow 33](../workflows/WORKFLOW-033.md) and `PROJECT_STATE.md` |

Workflow 33 also shows that the repair-edit prompt budget (§7, §13) can be
exceeded by a 1+1 selection at repair 2, not only by the 2+1 shapes measured
here. The limit held; it was not raised.

**[Workflow 34](../workflows/WORKFLOW-034.md): failed safely.** A targeted
`countDigits(String)` task again produced a consistent live `test` route:
the generated test expected 2 for `" 123 "`, while the implementation returned
the correct value 3. The route to `TextStatsTest.java` was accepted and
persisted, but the repair-edit prompt required 2300/2000 bytes and failed
closed before repair inference. Human review also noticed that the generated
implementation used `Character.isDigit`, broader than the task's stated
ASCII `0-9` contract; that observation motivated Workflow 35.

**[Workflow 35](../workflows/WORKFLOW-035.md): failed safely at coverage
check.** The follow-up task explicitly required ASCII-only counting and a
Unicode-digit test. Candidate verification passed 46/46 tests, and the original
suite also passed 46/46 against candidate production. Because the editable
test file changed without increasing the executed JUnit case count, the
added-coverage gate rejected the candidate (`46 vs 46`) before behavioral
delta or repair routing.

After Workflows 33–35, live test-domain routing is well exercised, but §14
step 3 remains outstanding: no live workflow has yet entered a
`fault_domain: production` repair under 7B.2. A live contradictory typed route
has also not been observed; that rejection path remains regression-tested
rather than live-observed.
