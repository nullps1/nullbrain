# Workflow 41

Workflow 41 again selected the exact granted Initials production/test pair, but
the planning inference returned malformed JSON after ignoring the planner's
"Do not write code" instruction and embedding Java source directly in the
`steps` array.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 41 |
| Profile | `repo-execute-v1` |
| Date | 2026-09-26 |
| Environment | Raspberry Pi 5 8 GB, live Ollama / Nullbrain worker |
| Target repository | `/srv/nullbrain/repos/nullcode-java-lab` |
| Base branch / commit | `main` / `d2a356a59cfb339cf935ba1dc2009e2b85ba267b` |
| Task | Add `dotted(String)` to `Initials` and focused tests |
| Inference job IDs | selection 145, planning 146 |

## Selection

Attempt 1 passed with exactly:

- `src/main/java/lab/text/Initials.java`
- `src/test/java/lab/text/InitialsTest.java`

This confirms the authority and selection path remained stable after the
Workflow 40 prompt-budget patch.

## Planning failure

Attempt 2 returned a plan-like JSON object, but its `steps` array contained a
large Java code block despite the planning prompt saying `Do not write code`.

The answer included:

`result.append(Character.toUpperCase(word.charAt(0))).append(\'.\');`

The sequence `\'` is not a legal JSON escape. `json.loads` therefore
rejected the answer with:

`Invalid model JSON: Invalid \\escape: line 23 column 74 (char 1011)`

The answer also embedded test source and string-literal examples inside
`steps`, increasing the risk of JSON-escape failures for no planning benefit.

## Final outcome

`failed`.

No edit inference, verification, repair, commit or publication occurred.

## Safety behavior observed

Malformed model output failed closed. Nullbrain did not normalize, patch,
re-escape or otherwise reinterpret invalid JSON.

The parser remains strict and deterministic.

## Root cause

The planner contract said `Do not write code`, but did not explicitly constrain
the `steps` strings to prose-only planning language. Inference job 146 ignored
the broad instruction and emitted implementation code inside the JSON schema.

## Remediation

Keep strict JSON parsing unchanged.

The shared planning prompt now explicitly requires:

- short prose-only steps;
- no code;
- no code fences;
- no string literals; and
- no escaped source snippets.

This narrows the response format without changing authority, file limits, prompt
limits, edit limits, retry behavior or parser behavior.

A regression assertion pins that instruction and the unchanged 2000-byte
planning-prompt ceiling.

## Follow-up

After the planning prompt patch is pulled, tested on the Pi and the worker is
restarted, rerun the same Initials task as Workflow 42 against the same pinned
Java-lab base unless that base changes.
