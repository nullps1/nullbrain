# Workflow 43

A live `repo-execute-v1` rerun of the dedicated Initials task after the
Workflow 42 repair-routing prompt patch. File selection succeeded, but planning
again embedded Java source inside `steps`. The model emitted the illegal JSON
escape `\'`, strict parsing rejected it, and the workflow failed closed before
editing. This recurrence shows that planner prompt guidance alone is not a
sufficient format control.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 43 |
| Profile | `repo-execute-v1` |
| Date | 2026-09-26 |
| Environment | Raspberry Pi 5 Nullbrain host |
| Target repository | `/srv/nullbrain/repos/nullcode-java-lab` |
| Base commit | `d2a356a59cfb339cf935ba1dc2009e2b85ba267b` |
| Task | Add `dotted(String)` to Initials; uppercase initials separated and terminated by periods, example `H.J.2.`; preserve null/blank behavior and add focused InitialsTest coverage |
| Inference job IDs | selection **152**, planning **153** |

## Selected scope

Selection inference 152 passed with:

- production: `src/main/java/lab/text/Initials.java`
- test: `src/test/java/lab/text/InitialsTest.java`

The selection reason was: "Adding a new method to Initials and testing it in
InitialsTest."

No scope expansion occurred.

## Execution path

`inspecting → planning → failed`

There was no editing, compile, test, diagnosis, repair-selection or repair-edit
phase.

## Model decisions

The planning prompt explicitly said:

`Steps: prose only; no code, fences, literals, or escapes.`

Planning inference 153 nevertheless returned a `steps` array containing:

- a ```java` fence;
- a complete `public static String dotted(String text)` implementation;
- implementation statements and control flow;
- a complete JUnit test method;
- string/character literals and escaped source syntax.

The key malformed line in the raw answer was:

`result.append(\'.\');`

The answer also proposed a production implementation that, like Workflow 42,
only inserted periods between initials and still omitted the required terminal
period. That candidate was never edited into the checkout because planning
failed first.

## Failure evidence

The worker reported:

`Invalid model JSON: Invalid \\escape: line 23 column 53 (char 1083)`

The raw answer was wrapped in a Markdown JSON fence. `extract_json()` removes
that outer fence before parsing, so the parser's line 23 corresponds to the raw
answer's numbered line 24 containing the `\'` character-literal escape.

JSON permits escapes such as `\"`, `\\`, `\n` and `\t`; `\'` is not a
valid JSON escape. Strict `json.loads()` therefore rejected the plan exactly as
designed.

## Final outcome

Terminal status: **failed**.

- no plan artifact accepted;
- no edit inference;
- no Java source written;
- no verification;
- no repair routing;
- no commit;
- no publication.

Workflow 43 did not reach the Workflow 42 repair-routing patch, so that patch
still lacks live validation.

## Safety behavior observed

- Strict JSON parsing failed closed; malformed model syntax was not repaired,
  normalized or guessed.
- Selected-file authority remained intact.
- The workflow did not proceed to editing from an invalid plan.
- The 2000-byte controller limit was not relaxed.
- No retry was silently added.

## Artifacts

Runtime artifacts on the Pi, not committed:

- `/srv/nullbrain/jobs/workflow-43/attempt-1/answer.txt`
- `/srv/nullbrain/jobs/workflow-43/attempt-1/prompt.txt`
- `/srv/nullbrain/jobs/workflow-43/attempt-1/result.json`
- `/srv/nullbrain/jobs/workflow-43/attempt-2/answer.txt`
- `/srv/nullbrain/jobs/workflow-43/attempt-2/prompt.txt`

## What we learned

Workflow 41 had already shown that a planner could ignore a no-code instruction
and produce malformed JSON. The prompt was tightened afterward, but Workflow 43
repeated the same failure class. That is evidence that **prompt-only planner
format guidance is insufficient**.

The right hardening boundary is two-layered:

1. keep the prompt compact and explicit about plain-English, "what not how"
   steps; and
2. after successful JSON parsing, deterministically reject code-shaped step
   content.

Malformed JSON remains terminal. The controller should not attempt to recover
or reinterpret an answer that never satisfied the JSON contract.

## Follow-up implemented in this PR

- remove the conflicting `implementation-oriented` planner phrase;
- share one compact `PLAN_STEPS_GUIDANCE` string between ordinary planning
  and semantic re-planning;
- preserve strict `extract_json()`;
- reject parseable step strings containing code fences, backslashes, braces,
  semicolons, Java declaration/control prefixes, or JUnit assertion calls;
- preserve method-level plain-English planning prose;
- add an exact Workflow 43 invalid-`\'` regression;
- retain the unchanged 2000-byte controller ceiling and all existing authority,
  source, repair and semantic-replan budgets.

## Validation state

Before this patch, after PR #14 was pulled, the Pi ran:

`python -m unittest discover -s tests`

Result: **309 tests, OK**.

The new patch adds planner-format regression coverage and still requires the
post-merge targeted and full Pi test runs before the next live workflow.

## Next live validation

After merge:

1. pull current `main` on the Pi;
2. run the targeted planner tests and full unittest suite;
3. restart `nullcode-worker`;
4. submit the exact same Initials task again.

The next workflow should first prove that planning now stays inside the
plain-English plan contract. If candidate production again omits the terminal
period, it can then finally exercise the Workflow 42 repair-routing hardening.
