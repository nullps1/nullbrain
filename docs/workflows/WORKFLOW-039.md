# Workflow 39

Workflow 39 reran the Initials task after the Workflow 38 prompt hardening, but
the execution selector never received edit authority for the dedicated Initials
fixture. The committed Patient Zero `.nullcode.json` omitted both fixture
paths. Inference job 141 improvised a different production path, and Nullbrain
rejected it because that exact path was not approved.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 39 |
| Profile | `repo-execute-v1` |
| Date | 2026-09-26 |
| Environment | Raspberry Pi 5 8 GB, live Ollama / Nullbrain worker |
| Target repository | `/srv/nullbrain/repos/nullcode-java-lab` |
| Base branch / commit | `main` / `feb39e83c710a1b4c9c07a3a74bf4c260104b022` |
| Task | Add `dotted(String)` to `Initials` and focused tests |
| Inference job IDs | 141 |

## Approved scope at the pinned base

The committed `.nullcode.json` exposed these production edit targets:

- `src/main/java/lab/Slugs.java`
- `src/main/java/lab/TextStats.java`
- `src/main/java/lab/format/CaseConverter.java`
- `src/main/java/lab/format/ReportFormatter.java`
- `src/main/java/lab/service/TextAnalysisService.java`
- `src/main/java/lab/text/TextMetrics.java`
- `src/main/java/lab/text/TextNormalizer.java`

and these test targets:

- `src/test/java/lab/TextStatsTest.java`
- `src/test/java/lab/format/CaseConverterTest.java`
- `src/test/java/lab/format/ReportFormatterTest.java`

The task-target fixture files existed and were tracked, but were absent from the
authority grant:

- `src/main/java/lab/text/Initials.java`
- `src/test/java/lab/text/InitialsTest.java`

## Model decision

Inference job 141 returned:

- `src/main/java/lab/TextNormalizer.java`
- `src/test/java/lab/format/CaseConverterTest.java`

with the stated intent of putting `dotted` in `TextNormalizer`.

The production path was not merely the wrong class; it was also not an exact
approved path. The approved path was
`src/main/java/lab/text/TextNormalizer.java`.

## Execution path

`queued → generating → inspecting → failed during editable-file selection`

No edit, verification, commit or publication followed.

## Verification results

Nullbrain rejected the model response with:

`Model selected unapproved file: src/main/java/lab/TextNormalizer.java`

The workflow therefore failed before any unauthorized file could be changed.

## Final outcome

`failed`.

No candidate commit, publication or merge occurred.

## Safety behavior observed

The exact-path authority boundary failed closed. A stale/incomplete grant did
not cause Nullbrain to widen authority, normalize the hallucinated path, or
silently substitute another file.

This is useful safety evidence, but it is not successful 7C-1 live validation.

## Artifacts

- `jobs/workflow-39/attempt-1/prompt.txt`
- `jobs/workflow-39/attempt-1/answer.txt`
- `jobs/workflow-39/semantic-replan.json`
- `jobs/workflow-39/timing.json`

Runtime artifacts remain outside Git.

## Root cause

Patient Zero PR #4 added the dedicated `Initials.java` /
`InitialsTest.java` fixture pair, but the committed `.nullcode.json` was not
updated to grant those files execution edit authority. Because
`repo-execute-v1` derives authority from the committed configuration at the
pinned base, the selector could not legally choose the files named by the task.

## Remediation

The Java lab authority manifest was updated on `main` in commit
`13d0cb2be9e946a9ca2ea81a040d549818fdb0bf` to add:

- `src/main/java/lab/text/Initials.java` to `editable_files`
- `src/test/java/lab/text/InitialsTest.java` to `editable_test_files`

No Nullbrain workflow-engine or validator change was made for this failure.

## Follow-up

Workflow 40 should rerun the same Initials task against Java-lab base
`13d0cb2be9e946a9ca2ea81a040d549818fdb0bf`.

Workflow 39 remains recorded as a failure: the safety guard succeeded, while the
workflow itself did not.
