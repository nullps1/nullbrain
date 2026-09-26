# Workflow 36

The first live Milestone 7C-1 submission failed before inference because the
long-running Pi worker was still executing pre-merge dispatch code and sent the
new profile through the legacy repository workflow.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 36 |
| Profile | `repo-scope-v1` |
| Date | 2026-09-26 |
| Environment | Raspberry Pi 5 8 GB, live Nullbrain worker |
| Target repository | `/srv/nullbrain/repos/nullcode-java-lab` |
| Base branch / commit | `main` / `7a95d1437eb2b4b18f1ee4e6f6436fd5ff4c5bae` |
| Task | Add `pascalCase(String)` to `IdentifierFormatter` and tests |
| Inference job IDs | none |

## Selected scope

No scope selection occurred.

## Execution path

`queued → generating → failed` before scope inference. The stored
`repo_spec` correctly contained `"profile": "repo-scope-v1"`, but the
running worker dispatched to the legacy repository path.

## Verification results

Terminal error:

`Repository needs src/main/java/Numbers.java and src/test/java/NumbersTest.java`

The worker service was subsequently inspected: working directory
`/srv/nullbrain`, `PYTHONPATH=/srv/nullbrain/src`. Restarting the service
loaded the merged dispatch code, and an import/source inspection confirmed the
`repo-scope-v1` branch was present.

## Final outcome

`failed`. No inference, proposal, grant, edit, commit, publication or merge
occurred.

## Safety behavior observed

The failure did not widen authority or execute generated code. It exposed a
deployment-state problem: a long-running Python worker can remain on old
imported code after the repository is updated.

## Artifacts

No workflow artifact directory was recorded for the failed attempt. The
workflow database preserves the spec, error and attempt row.

## What we learned

Code-on-disk health and long-running worker health are distinct. After pulling
workflow-dispatch changes, the worker must be restarted before live validation.

## Follow-up

The worker was restarted and Workflow 37 became the first live inference run of
`repo-scope-v1`.
