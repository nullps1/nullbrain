# Workflow 40

Workflow 40 validated that the Patient Zero Initials authority grant was
correct: selection and planning both chose exactly `Initials.java` and
`InitialsTest.java`. The production edit inference then ran, but the workflow
failed when building the test-edit prompt because the complete prompt measured
2001 bytes against the unchanged 2000-byte limit.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 40 |
| Profile | `repo-execute-v1` |
| Date | 2026-09-26 |
| Environment | Raspberry Pi 5 8 GB, live Ollama / Nullbrain worker |
| Target repository | `/srv/nullbrain/repos/nullcode-java-lab` |
| Base branch / commit | `main` / `d2a356a59cfb339cf935ba1dc2009e2b85ba267b` |
| Task | Add `dotted(String)` to `Initials` and focused tests |
| Inference job IDs | selection 142, planning 143, edit 144 |

## Selection

Attempt 1 passed with exactly:

- `src/main/java/lab/text/Initials.java`
- `src/test/java/lab/text/InitialsTest.java`

This confirms the Workflow 39 authority-manifest remediation worked.

## Planning

Attempt 2 passed with a plan limited to the same two files. The plan called for
adding `dotted(String)` to `Initials` and focused tests to `InitialsTest`.

No scope widening occurred.

## Production edit

Attempt 3 produced a complete replacement for `Initials.java`.

The generated method preserved the existing null and blank behavior, but it
inserted periods only between initials and did not append the required terminal
period. For example, it would produce `H.J.2` instead of the required
`H.J.2.`.

That candidate had not yet reached candidate verification, so the existing
verification/repair path did not get a chance to evaluate this defect.

## Failure

The workflow then attempted to build the `InitialsTest.java` edit prompt.

The prompt requires:

- the complete task;
- the target-specific plan;
- the complete edited production source as read-only reference;
- the complete existing test target; and
- the existing-test preservation guidance.

For the exact Workflow 40 shape, the assembled prompt measured **2001 bytes**.
The controller limit remains 2000 bytes.

The workflow failed with:

`Complete edit context exceeds 2000 bytes; nothing truncated`

## Final outcome

`failed`.

No verification, repair, commit or publication occurred.

## Safety behavior observed

The prompt-budget boundary failed closed and did not truncate Java evidence.
The 2000-byte limit was not exceeded or bypassed.

Workflow 40 therefore provides two useful results:

1. the Initials authority grant and file selection path are now correct; and
2. the next blocker is a one-byte prompt-budget inefficiency in the edit stage.

## Artifacts

- `jobs/workflow-40/attempt-1/`
- `jobs/workflow-40/attempt-2/`
- `jobs/workflow-40/attempt-3/1-Initials.java.prompt.txt`
- `jobs/workflow-40/attempt-3/1-Initials.java.answer.txt`

Runtime artifacts remain outside Git.

## Remediation

Do not raise the 2000-byte limit and do not truncate task or Java evidence.

The edit-prompt boilerplate was shortened from:

`preserve existing behavior/API except requested additions`

to:

`preserve behavior/API except requested additions`

This removes 9 redundant bytes while preserving the same instruction. The exact
Workflow 40 test-edit shape now fits under the unchanged 2000-byte limit.

A regression test reproduces the Workflow 40 task, plan, production edit,
production reference and test target and asserts that the complete prompt fits
without truncation.

## Follow-up

After the prompt patch is pulled and the Nullbrain test suite passes on the Pi,
restart the worker and rerun the same Initials task as Workflow 41 against the
same Java-lab base unless that base changes.
