# Workflow 46

Workflow 46 reran the Initials task after the required-domain candidate
narrowing preparation ([WORKFLOW-045.md](WORKFLOW-045.md)). Selection and
planning again passed. The production edit succeeded and produced a
semantically imperfect but structurally valid candidate. The workflow then
failed while preparing the second selected file, `InitialsTest.java`, before
that file's edit prompt could even be constructed.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 46 |
| Profile | `repo-execute-v1` |
| Base branch / commit | `main` / `d2a356a59cfb339cf935ba1dc2009e2b85ba267b` |
| Selected files | `src/main/java/lab/text/Initials.java`, `src/test/java/lab/text/InitialsTest.java` |
| Task | Add `dotted(String)` to `Initials`: uppercase initials separated and terminated by periods, e.g. `'hello Java 21'` → `'H.J.2.'`; preserve existing null/blank behavior; add focused tests in `InitialsTest` |

## Selection and planning

Attempt 1 (selection) and attempt 2 (planning) both passed with exactly the
two intended files, matching every prior Initials-task run since Workflow 40.

## Production edit

Attempt 3 constructed the production-file prompt, generated `Initials.java`,
extracted the Java response, wrote the candidate into the workflow checkout,
and verified the changed file remained within selected scope. All of that
succeeded.

The generated candidate was semantically imperfect: it produced `H.J.2`
instead of the requested `H.J.2.` (the same defect Workflows 40, 42, 44 and
45 all previously surfaced in one form or another). Candidate verification
was never reached, so the existing verification/repair/behavioral-delta path
never got a chance to evaluate that defect on this run.

## Failure

The workflow then attempted to prepare the `InitialsTest.java` edit prompt.
Building that prompt's reference context requires the complete edited
`Initials.java` as read-only context, the complete task, the target-specific
plan text, and the complete `InitialsTest.java` target. The assembled prompt
exceeded the controller's 2000-byte input limit, and prompt construction
raised before the prompt artifact was written:

```json
{
  "id": 46,
  "status": "failed",
  "error": "Complete edit context exceeds 2000 bytes; nothing truncated"
}
```

Attempts: selection passed (1), planning passed (2), edit failed (3).
Semantic re-plan attempts: 0.

Attempt 3's artifacts contain only:

- `1-Initials.java.prompt.txt`
- `1-Initials.java.answer.txt`

There is no `2-InitialsTest.java.prompt.txt`. This is expected from the
implementation at the time: `edit_prompt()` performed its 2000-byte guard
before the prompt artifact for that file was ever written.

## Final outcome

`failed`. No candidate verification, no commit, no publication.

## Safety behavior observed

The prompt-budget boundary failed closed and did not truncate Java evidence
or bypass the 2000-byte limit. This is not an inference failure, not
semantic-replan behavior, and not primarily caused by Markdown fences in the
model's response - `extract_java()` handled the fenced production response
correctly and the modified production source was written to the checkout.
This is a prompt/context-budget architecture failure: whole-selected-
production-file reference context for a test edit has no bound on how large
a just-edited production candidate can be, only a pass/fail check on the
assembled prompt.

## What we learned

Workflow 40 hit the same generic error once already and was "fixed" by
shaving nine redundant bytes off the edit-prompt boilerplate so that
Workflow 40's specific fixture fit under the limit
([WORKFLOW-040.md](WORKFLOW-040.md)). Workflow 46 shows that fix was never
structural: the boilerplate byte count is fixed, but the production
candidate's size is not, and a candidate a little larger than Workflow 40's
recurs the identical failure. The real defect is architectural: test-edit
reference context needs a bound that degrades gracefully - preferring
complete, structurally relevant Java members over an all-or-nothing whole
file - not a byte-shaving patch against one fixture's measured size.

## Artifacts

Runtime artifacts on the worker, not committed:

- `jobs/workflow-46/attempt-1/`
- `jobs/workflow-46/attempt-2/`
- `jobs/workflow-46/attempt-3/1-Initials.java.prompt.txt`
- `jobs/workflow-46/attempt-3/1-Initials.java.answer.txt`

This document and the artifact paths above are preserved as historical
failure evidence. They are not modified, rerun, or reused by the fix.

## Remediation

[Milestone 7B.3](../milestones/MILESTONE-7B-3.md) replaces the whole-file-or-
fail reference-context construction in the Stage 3 edit loop with a bounded,
structurally safe strategy: the existing whole-file behavior when it fits,
falling back to complete top-level Java members (never a byte-sliced
fragment) prioritizing content this run's production edit actually changed,
and a deterministic, diagnostic-rich failure - naming the target, the
controller limit and the bytes involved - only when even that minimum cannot
fit.

## Follow-up

After Milestone 7B.3 is reviewed, merged, pulled and the worker restarted, a
**new** workflow ID should rerun the same Initials task against the same
Java-lab base (unless that base changes) to validate the fix at runtime. The
primary evidence to look for: a `2-InitialsTest.java.prompt.txt` artifact
now exists, the workflow reaches candidate verification, and - independent of
the context-budget fix - the existing verification/repair/behavioral-delta
pipeline gets its first live opportunity to evaluate the `H.J.2` vs `H.J.2.`
semantic defect that every prior Initials-task run has produced. Workflow 46
itself is not rerun or modified; the follow-up is a new workflow.
