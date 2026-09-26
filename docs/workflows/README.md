# Workflow records

Durable records of notable live workflow executions.

The two kinds of record are kept apart:

- **Milestone documents** ([`../milestones/`](../milestones/)) describe
  capabilities and increments: what was designed and implemented, why, how it
  was tested, and what it does not change.
- **Workflow documents** (this directory) record what actually happened when a
  specific workflow ran: its scope, execution path, model decisions,
  verification results, outcome and the safety gates that mattered.

A milestone often cites a workflow as its motivation or its live validation.
The workflow record holds the run's details, and the milestone links to it.

Runtime job artifacts (`jobs/workflow-<id>/`, the workflow database, logs)
remain **outside Git**. A workflow document may list artifact paths so they
can be found on the host, but those paths are references only; the artifacts
themselves are never committed.

## When a workflow gets a document

Not every workflow needs one. Write a record when a run:

- motivates an architectural change;
- validates a milestone;
- exposes a new safety failure mode;
- produces evidence needed for future decisions; or
- materially changes the project's understanding.

Earlier workflows are not documented retroactively unless one of those
applies. A record states only what the run, its artifacts or the repository
history actually show. A fact nobody recorded is marked "not recorded in
repository documentation", not reconstructed.

Workflow records follow the documentation-trail convention in
[`../PROJECT_STATE.md` §11](../PROJECT_STATE.md#11-documentation-trail-convention).

## Index

| Workflow | Purpose / significance |
| --- | --- |
| [25](WORKFLOW-025.md) | insufficient-test-count repair narrowing |
| [26](WORKFLOW-026.md) | behavioral-delta hardening evidence |
| [32](WORKFLOW-032.md) | repair-routing contradiction that motivated 7B.2 |
| [33](WORKFLOW-033.md) | first live 7B.2 test-domain validation |
| [34](WORKFLOW-034.md) | second live 7B.2 test-domain route; repair prompt budget failed closed |
| [35](WORKFLOW-035.md) | ASCII-only follow-up stopped by the added-coverage gate |
| [36](WORKFLOW-036.md) | first live 7C-1 submission exposed a stale deployed worker and legacy fallthrough |
| [37](WORKFLOW-037.md) | live 7C-1 selection rejected an oversized natural test file at the 900-byte gate |
| [38](WORKFLOW-038.md) | correct 1+1 edit pair selected; duplicate context nomination failed closed and motivated prompt hardening |
| [39](WORKFLOW-039.md) | stale Patient Zero authority grant omitted Initials fixtures; hallucinated unapproved path failed closed |

File names are zero-padded to three digits (`WORKFLOW-025.md`) so they sort
correctly; prose uses the plain number ("Workflow 25").

## Template

Copy this for a new record. Keep what happened during the run separate from
what was implemented afterward, and say "Later work introduced…" for the
latter.

```markdown
# Workflow N

One-paragraph summary: what ran, how it ended, why it matters.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | N |
| Profile | |
| Date | |
| Environment | |
| Target repository | |
| Base branch / commit | |
| Task | |
| Inference job IDs | |

## Selected scope

- production files
- test files
- any narrowed repair scope

## Execution path

state → state → …

## Model decisions

Planning, routing, diagnosis and repair decisions that mattered.

## Verification results

Compile/test results, test counts, failures, behavioral-delta result, repair
results, prompt-budget failures.

## Final outcome

Exact terminal state (`succeeded`, `failed`, `rejected-no-behavioral-delta`,
…) and message. Whether any commit, publication or merge occurred.

## Safety behavior observed

The gates that materially mattered: fail-closed behavior, scope preservation,
attempt budget, prompt limit, unchanged-source rejection, behavioral-delta
gate, typed routing, no publication.

## Artifacts

Runtime paths on the host, for reference only (not committed):

- `jobs/workflow-N/attempt-<n>/…`

## What we learned

The architectural conclusion or evidence the run produced.

## Follow-up

Links to milestones, proposals, later workflows, fixes or the next validation
step.
```
