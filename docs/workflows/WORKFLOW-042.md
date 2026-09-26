# Workflow 42 — Initials dotted terminal-period repair-routing failure

**Date:** 2026-09-26  
**Profile:** `repo-execute-v1`  
**Outcome:** failed safely  
**Purpose:** live 7C-1 / Patient Zero execution of the dedicated Initials task after planner hardening

## Task

Add `dotted(String)` to `Initials` so uppercase initials are separated and
terminated by periods; the task explicitly gave `hello Java 21 -> H.J.2.`.
Preserve existing null/blank behavior and add focused tests.

## Execution record

| Stage | Evidence |
| --- | --- |
| Selection | inference job **147**; selected `src/main/java/lab/text/Initials.java` and `src/test/java/lab/text/InitialsTest.java` |
| Planning | inference job **148**; planned the same two files |
| Production edit | inference job **149**; added `dotted(String)`, but emitted periods only between initials |
| Test edit | inference job **150**; added focused dotted/null/blank tests |
| Compile | passed |
| Candidate tests | **52 executed, 1 failed** |
| Failing assertion | expected `H.J.2.`, actual `H.J.2` |
| Repair routing | inference job **151**; selected `fault_domain: test`, target `InitialsTest.java` |
| Repair edit | not run |
| Terminal guard | complete repair context required **2307/2000 bytes**; nothing truncated |

Runtime artifacts remain under
`/srv/nullbrain/jobs/workflow-42/` and are not repository content.

## What changed in the candidate

Generated production implemented:

`H.J.2`

instead of the required:

`H.J.2.`

The generated test assertion expected the latter and therefore correctly encoded
the explicit task contract.

## Root cause

The typed 7B.2 routing fields were internally consistent but semantically wrong.
The router chose `test` and a selected test file, so deterministic
domain/file validation correctly accepted the reply. Its reason was also
self-contradictory: it said the expected value should be `H.J.2.` while
blaming the test that already expected exactly that value.

This is the known boundary of typed routing: the controller validates the
structure and domain membership of the model's diagnosis; it does not prove the
diagnosis is semantically correct and does not parse or override `reason`.

The later 2307/2000 repair-context overflow is real but secondary. It occurred
only after the wrong test route had already been accepted. The fail-closed
budget prevented any wrong-target repair edit.

## Remediation

Harden the advisory repair-selection prompt without changing controller
authority:

1. TASK is explicitly the source of truth.
2. Compare assertion EXPECTED and ACTUAL against TASK.
3. If expected matches TASK and actual does not, choose `production`.
4. If actual matches TASK and expected does not, choose `test`.
5. A failing assertion alone does not mean the test is wrong.
6. An expected value explicitly stated or exemplified by TASK is strong
   evidence for production when actual disagrees.

Add paired regression coverage for this Workflow 42 shape and the inverse
contract case.

## Unchanged guarantees

- exact typed `fault_domain` vocabulary remains `production|test`;
- named file must still be an offered candidate in that domain;
- `reason` remains evidence only and is not parsed deterministically;
- no auto-correction or second routing call;
- at most two ordinary repairs;
- one semantic re-plan;
- 3-file edit cap and 900-byte source cap unchanged;
- 2000-byte controller prompt/context ceiling unchanged;
- overflow still fails closed with no truncation.

## Next live validation

After merge, pull current Nullbrain `main` to the Pi, restart
`nullcode-worker`, and rerun the same Initials task as a new workflow.
Success criteria for the routing stage are a live `production` route to
`src/main/java/lab/text/Initials.java`. If that correct route still cannot
build complete repair context within 2000 bytes, treat repair-context
compaction as a separate follow-up rather than masking it by increasing the
limit.
