# Workflow 38

Workflow 38 selected the exact intended ungranted Initials production/test
pair, but the model also repeated the production file as read-only context.
The deterministic duplicate-path validator rejected call 1 before any final
proposal could be made.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 38 |
| Profile | `repo-scope-v1` |
| Date | 2026-09-26 |
| Environment | Raspberry Pi 5 8 GB, live Ollama / Nullbrain worker |
| Target repository | `/srv/nullbrain/repos/nullcode-java-lab` |
| Base branch / commit | `main` / `feb39e83c710a1b4c9c07a3a74bf4c260104b022` |
| Task | Add `dotted(String)` to `Initials` and tests |
| Inference job IDs | 140 |

## Selected scope

The raw call-1 answer named:

- production: `src/main/java/lab/text/Initials.java`
- test: `src/test/java/lab/text/InitialsTest.java`
- context: `src/main/java/lab/text/Initials.java` again

The edit pair itself was exactly the intended fixture.

## Execution path

`queued → generating → inspecting → selecting-scope → validating-selection → failed`

Call 2 was never reached.

## Model decisions

The model correctly identified which production and test files had to change,
but treated the production source as both an edit target and a context file.

## Verification results

The call-1 prompt was 1846 bytes. The raw answer was 302 bytes.

Deterministic validation rejected it with:

`Scope selection names src/main/java/lab/text/Initials.java more than once (production_files, context_files)`

## Final outcome

`failed`. No final scope proposal, review, grant, execution, commit or
publication occurred.

## Safety behavior observed

Cross-class duplicate rejection remained fail-closed. The workflow did not
silently deduplicate, infer intent, auto-correct the response or make a second
inference call.

## Artifacts

- `jobs/workflow-38/attempt-1/selection-prompt.txt`
- `jobs/workflow-38/attempt-1/selection-answer.txt`
- `jobs/workflow-38/attempt-1/result.json`

## What we learned

The validator's rule was stronger than the call-1 instructions. The prompt said
the model may name one “other” context file, but did not explicitly state that
a path may occur in only one list. Live inference exposed that ambiguity while
the deterministic boundary held.

## Follow-up

The follow-up patch adds one explicit instruction: a path may appear in only
one list and an edit file must never be repeated as context. The duplicate
validator, prompt limit, source limit and authority model are unchanged.
Workflow 39 should rerun the same Initials task after the patch is merged,
Pi-tested and the worker restarted.
