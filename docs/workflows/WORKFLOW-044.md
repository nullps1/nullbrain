# Workflow 44

Workflow 44 reran the exact Initials task after the Workflow 43 planner-format
patch. Selection and planning both passed, editing reached compilation and
testing, and the run entered repair diagnosis. The planner hardening therefore
worked live. Repair routing inference still chose the `test` domain even though
its own reason stated that the test expected `H.J.2.` while actual output was
`H.J.2`. The typed route was internally consistent, so the pre-WF44 validator
accepted it. The subsequent wrong-target repair context measured 2324/2000
bytes and failed closed before a repair edit.

This run is the second live Initials example showing that prompt-only semantic
routing is insufficient when the task itself explicitly contains the expected
assertion value.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 44 |
| Profile | `repo-execute-v1` |
| Date | 2026-09-26 |
| Environment | Raspberry Pi 5 Nullbrain host |
| Target repository | `/srv/nullbrain/repos/nullcode-java-lab` |
| Base commit | `d2a356a59cfb339cf935ba1dc2009e2b85ba267b` |
| Task | Add `dotted(String)` to Initials; return uppercase initials separated and terminated by periods, example `'hello Java 21' becomes 'H.J.2.'`; preserve null/blank behavior and add focused InitialsTest coverage |
| Inference job IDs | selection **154**, planning **155**, edit attempt latest recorded inference **157**, repair routing **158** |

The supplied workflow output did not expose every per-file edit inference ID,
so no missing ID is reconstructed here.

## Selected scope

Selection inference 154 passed with exactly:

- `src/main/java/lab/text/Initials.java`
- `src/test/java/lab/text/InitialsTest.java`

Reason:

> Adding a new method to Initials and testing it in InitialsTest.

No scope expansion occurred.

## Planning

Planning inference 155 passed the planner-format gate introduced after Workflow
43. Its four steps were plain-English actions:

1. add `dotted(String)`;
2. make it return uppercase initials separated and terminated by periods;
3. preserve null/blank behavior;
4. add focused Initials tests.

There were no embedded source bodies, code fences or invalid JSON escapes.
This is live evidence that the Workflow 43 planner-format patch corrected the
failure class seen in Workflows 41 and 43.

## Execution path

`queued → inspecting → planning → editing → compiling → testing → diagnosing-repair-1 → failed`

The run reached candidate verification and repair diagnosis. No repair edit was
made.

## Repair routing

Attempt 4 persisted:

```json
{
  "repair_number": 1,
  "required_domain": null,
  "response": {
    "fault_domain": "test",
    "file": "src/test/java/lab/text/InitialsTest.java",
    "reason": "The test expects 'H.J.2.' but the actual output is 'H.J.2'."
  },
  "accepted": true
}
```

The response is semantically wrong for the task contract:

- the task explicitly gives `'H.J.2.'` as the required result;
- the route reason says the test expects `H.J.2.`;
- the route reason says actual output is `H.J.2`;
- nevertheless, inference 158 selected `fault_domain: test`.

The pre-WF44 deterministic validator could prove only that the typed domain and
selected file agreed. Because `required_domain` was `null`, the consistent but
wrong test route was accepted.

The model's free-text reason is preserved as evidence but is not parsed for
routing semantics.

## Repair-context failure

After the accepted test route, Nullbrain attempted to construct complete repair
context for `InitialsTest.java` and stopped with:

`Complete repair context needs 2324/2000 bytes; nothing truncated`

This does **not** establish that a correctly routed production repair needs
2324 bytes. The measurement belongs to the wrong test target and should not be
used to justify raising the controller ceiling.

The 2000-byte guard held and no evidence was silently truncated.

## Final outcome

Terminal status: **failed**.

Observed outcome:

- selection passed;
- planning passed;
- editing occurred;
- compilation occurred;
- tests ran;
- repair routing ran;
- the wrong but type/file-consistent test route was accepted;
- repair-context construction failed closed at 2324/2000;
- no repair edit occurred;
- no commit or publication occurred.

## Artifacts inspected

Runtime paths on the Pi, not committed:

- `/srv/nullbrain/jobs/workflow-44/attempt-1/`
- `/srv/nullbrain/jobs/workflow-44/attempt-2/`
- `/srv/nullbrain/jobs/workflow-44/attempt-3/`
- `/srv/nullbrain/jobs/workflow-44/attempt-4/diagnostic.txt`
- `/srv/nullbrain/jobs/workflow-44/attempt-4/repair-routing.json`
- `/srv/nullbrain/jobs/workflow-44/attempt-4/repair-selection.json`
- `/srv/nullbrain/jobs/workflow-44/attempt-4/selection-answer.txt`
- `/srv/nullbrain/jobs/workflow-44/attempt-4/selection-prompt.txt`

A root-level `attempt-3/verification.json` was not present when checked; this
record does not invent verification fields that were not displayed.

## What we learned

Workflow 42 tightened advisory routing language around task-contract
precedence. Workflow 44 repeated the same semantic error after that patch:
the model accurately described expected versus actual and still selected the
wrong domain.

That is enough live evidence to stop treating prompt wording as the enforcement
boundary for this narrow case.

The controller already has `required_domain`. The safe follow-up is to populate
it only when a domain is mechanically established from:

1. one unique JUnit `expected: <...> but was: <...>` pair; and
2. an exact single-quoted, double-quoted or backticked task literal matching
   exactly one side.

Exact equality is required. Substring matching is forbidden, so the WF44 pair
`H.J.2.` versus `H.J.2` cannot accidentally classify both values as present
in the task.

If both values are explicit, neither is explicit, or multiple distinct
assertion pairs are present, the controller keeps `required_domain: null`.

## Follow-up implemented in this PR

- add `explicit_contract_required_domain(task, diagnostic)`;
- infer `production` only when expected is an exact explicit task literal and
  actual is not;
- infer `test` only for the inverse case;
- preserve ambiguity as `None`;
- state any deterministic requirement in the routing prompt;
- pass it through the existing `validate_repair_selection()` gate;
- reject a conflicting model reply instead of correcting it;
- add `required_domain_evidence` to `repair-routing.json`;
- preserve the existing insufficient-test-count `test` requirement;
- keep `reason` non-semantic;
- keep both candidate domains offered for ordinary assertion failures;
- keep the 2000-byte limit, repair budget and semantic re-plan budget unchanged.

## Next live validation

Workflow 45 should rerun the exact same Initials task after this patch is
validated, merged, pulled, and the worker is restarted.

Expected controller evidence for the same assertion pair:

```json
{
  "required_domain": "production",
  "required_domain_evidence": {
    "kind": "explicit-task-literal-vs-junit",
    "expected": "H.J.2.",
    "actual": "H.J.2",
    "expected_in_task": true,
    "actual_in_task": false
  }
}
```

If inference still replies `test`, the workflow must now fail at routing with
`requires fault_domain 'production'` and make no repair-edit call. If it
replies `production`, only then is a production repair-context measurement
meaningful.
