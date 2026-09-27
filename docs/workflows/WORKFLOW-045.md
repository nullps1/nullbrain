# Workflow 45

Workflow 45 reran the exact Initials task after PR #16 merged the
explicit-contract `required_domain` enforcement. Selection, planning, editing,
compilation and testing all proceeded normally. Candidate verification again
observed `expected: <H.J.2.> but was: <H.J.2>`.

This time the controller independently derived
`required_domain: "production"` from the exact task literal and JUnit
expected/actual pair. Repair-routing inference 163 nevertheless returned a
typed `test` route to `InitialsTest.java`. The existing validator rejected the
reply immediately as contradictory. No repair-edit prompt was built.

This is the first live proof that the post-Workflow 44 deterministic contract
gate catches the repeated consistent-but-semantically-wrong routing failure
seen in Workflows 42 and 44.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 45 |
| Profile | `repo-execute-v1` |
| Date | 2026-09-26 |
| Environment | Raspberry Pi 5 Nullbrain host |
| Target repository | `/srv/nullbrain/repos/nullcode-java-lab` |
| Base commit | `d2a356a59cfb339cf935ba1dc2009e2b85ba267b` |
| Task | Add `dotted(String)` to Initials; return uppercase initials separated and terminated by periods, example `'hello Java 21' becomes 'H.J.2.'`; preserve null/blank behavior and add focused InitialsTest coverage |
| Inference job IDs | selection **159**, planning **160**, edit attempt latest recorded inference **162**, repair routing **163** |

The workflow summary exposed only the latest inference job associated with
attempt 3, not every per-file edit inference ID. Missing IDs are not
reconstructed here.

## Selected scope

Selection inference 159 passed with exactly:

- `src/main/java/lab/text/Initials.java`
- `src/test/java/lab/text/InitialsTest.java`

Reason:

> Adding a new method to Initials and testing it in InitialsTest

No scope expansion occurred.

## Planning

Planning inference 160 passed the deterministic planner-format gate. The plan
used short prose steps and referenced only the selected files.

The summary said:

> Add a new method 'dotted' to the Initials class that returns uppercase
> initials separated by periods. Add focused tests in InitialsTest.

The plan did not itself repeat the terminal-period phrase in its summary, but
the original task remained the source of truth for deterministic contract
evidence.

## Execution path

`queued → inspecting → planning → editing → compiling → testing → diagnosing-repair-1 → failed`

The terminal `failed` state is the expected fail-closed outcome for a routing
reply that contradicts a controller-required domain.

## Candidate verification

The persisted repair diagnostic was:

```text
JUnit: buildsDottedInitialsFromWhitespaceSeparatedWords(): org.opentest4j.AssertionFailedError: expected: <H.J.2.> but was: <H.J.2>
Test log: > Task :test FAILED | InitialsTest > buildsDottedInitialsFromWhitespaceSeparatedWords() FAILED | org.opentest4j.AssertionFailedError at InitialsTest.java:24 | BUILD FAILED in 14s
```

The diagnostic contains one unique expected/actual pair:

- expected: `H.J.2.`
- actual: `H.J.2`

The task contains the complete single-quoted expected literal `'H.J.2.'` and
does not contain `'H.J.2'` as a complete literal.

## Deterministic required-domain evidence

`repair-routing.json` persisted:

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

This is the exact live evidence the Workflow 44 follow-up was designed to
produce.

The prefix trap is resolved correctly: `H.J.2` was **not** treated as present
merely because it is a substring of the explicit `H.J.2.` task literal.

## Model routing reply

Inference 163 returned:

```json
{
  "fault_domain": "test",
  "file": "src/test/java/lab/text/InitialsTest.java",
  "reason": "The test expected 'H.J.2.' but received 'H.J.2', indicating a discrepancy between the expected and actual results."
}
```

The reply is internally consistent in the old 7B.2 sense: `test` names a
selected test file. It is nevertheless incompatible with the independently
established controller requirement.

The model's reason again accurately described the expected/actual mismatch but
did not choose the required domain. As designed, `reason` remained
non-semantic and was not used to repair or reinterpret the reply.

## Deterministic rejection

The workflow rejected the route with:

`Repair 1 routing is contradictory: this failure requires fault_domain 'production'`

The persisted routing artifact recorded:

- offered production file: `Initials.java`;
- offered test file: `InitialsTest.java`;
- required domain: `production`;
- response domain: `test`;
- `accepted: false`;
- the contradiction error above.

No repair-selection artifact was accepted for execution and no repair-edit
prompt was built.

## Safety behavior observed

Workflow 45 live-validates the important post-Workflow 44 invariants:

- exact task/assertion evidence may establish `required_domain`;
- the requirement is independent of the model's `reason`;
- a typed domain/file pair that conflicts with the requirement fails closed;
- the controller does not auto-correct `test` to `production`;
- there is no second routing call;
- no repair attempt is spent editing the wrong file;
- selected-file authority remains unchanged;
- no controller/source/scope/repair budget is widened;
- the previous 2324/2000 wrong-test-target repair-context path is never
  entered.

This run does **not** yet measure the correctly routed production repair
context because the model chose `test` and the validator correctly stopped the
workflow first.

## Prompt-format observation

The routing prompt successfully carried the controller requirement, but it
rendered this boundary as:

```text
Controller-required fault_domain: "production".\nTask: Add a dotted(String) ...
```

The two characters `\n` were sent literally instead of an actual newline.
This did not weaken deterministic enforcement: the controller independently
rejected the wrong route.

A follow-up cleanup replaces that literal sequence with a real newline and
adds a regression asserting that the prompt contains:

```text
Controller-required fault_domain: "production".
Task: ...
```

This is a formatting-only fix; it does not change routing semantics.

## Final outcome

Terminal status: **failed**, intentionally fail-closed.

- selection passed;
- planning passed;
- editing occurred;
- compilation occurred;
- tests ran;
- repair routing ran;
- controller required `production`;
- model chose `test`;
- routing was rejected;
- no repair edit occurred;
- no repair-context overflow occurred;
- no commit or publication occurred.

## Artifacts inspected

Runtime artifacts on the Pi, not committed:

- `/srv/nullbrain/jobs/workflow-45/attempt-1/`
- `/srv/nullbrain/jobs/workflow-45/attempt-2/`
- `/srv/nullbrain/jobs/workflow-45/attempt-3/`
- `/srv/nullbrain/jobs/workflow-45/attempt-4/diagnostic.txt`
- `/srv/nullbrain/jobs/workflow-45/attempt-4/repair-routing.json`
- `/srv/nullbrain/jobs/workflow-45/attempt-4/selection-answer.txt`
- `/srv/nullbrain/jobs/workflow-45/attempt-4/selection-prompt.txt`

## What we learned

The explicit-contract `required_domain` hardening is now live-proven.

Workflows 42 and 44 showed that better wording alone did not reliably produce
the correct semantic domain. Workflow 45 reproduced the same model behavior,
but the deterministic controller requirement converted it from an accepted
wrong route into an immediate, auditable rejection.

The remaining live gap is different: a correctly routed
`fault_domain: production` repair has still not proceeded far enough to
measure or execute the production repair context for this Initials task.

## Next step

Before Workflow 46, keep the distinction clear:

- Workflow 45 closes the explicit-contract contradiction-detection proof.
- It does **not** prove production repair-context size or successful production
  repair execution.

A future follow-up may narrow offered candidates to the already-required domain
before routing, analogous to the existing insufficient-test-count narrowing.
That design should be reviewed separately because it changes what choices the
model sees, even though it would only remove controller-disallowed choices and
would not grant new edit authority.
