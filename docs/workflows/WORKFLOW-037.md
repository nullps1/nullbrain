# Workflow 37

The first live 7C-1 run to reach scope inference selected the semantically
natural IdentifierFormatter production/test pair, then failed closed because
the test file exceeded the unchanged 900-byte 7B source limit.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 37 |
| Profile | `repo-scope-v1` |
| Date | 2026-09-26 |
| Environment | Raspberry Pi 5 8 GB, live Ollama / Nullbrain worker |
| Target repository | `/srv/nullbrain/repos/nullcode-java-lab` |
| Base branch / commit | `main` / `7a95d1437eb2b4b18f1ee4e6f6436fd5ff4c5bae` |
| Task | Add `pascalCase(String)` to `IdentifierFormatter` and tests |
| Inference job IDs | 139 |

## Selected scope

The model nominated `src/main/java/lab/format/IdentifierFormatter.java` and
its natural test `src/test/java/lab/format/IdentifierFormatterTest.java`.

## Execution path

`queued → generating → inspecting → selecting-scope → validating-selection → failed`

Call 2 was never reached.

## Verification results

Deterministic validation rejected the test source:

`src/test/java/lab/format/IdentifierFormatterTest.java exceeds the 900-byte 7B source limit`

## Final outcome

`failed`. No proposal, scope review, grant, execution, commit or publication
occurred.

## Safety behavior observed

The model's semantically sensible choice could not bypass the existing source
limit. The workflow stopped before any proposed authority artifact was created.

## Artifacts

- `jobs/workflow-37/attempt-1/`
- inference job 139

## What we learned

Patient Zero's natural ungranted pairs were not suitable for a clean success
path because their matching tests exceeded the 900-byte limit. The limit was
kept unchanged.

## Follow-up

Patient Zero PR #4 added a dedicated healthy, ungranted
`Initials.java` / `InitialsTest.java` pair under the limit. Workflow 38
used that pair.
