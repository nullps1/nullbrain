# Milestone 7B.3: bounded structural edit-context budgeting

A narrow architecture fix inside `repo-execute-v1`'s edit stage. Building the
reference context for a test-file edit prompt no longer has only two
outcomes - the complete selected production file(s), or a hard failure. When
the complete form does not fit the controller's 2000-byte input limit, the
workflow now falls back to a bounded, structurally complete production
reference instead of dying between the production edit and the test edit.

Motivating failure: [Workflow 46](../workflows/WORKFLOW-046.md).

## 1. Problem

Multi-file execution edits production sources before test sources (Stage 3 of
`run_job`). Building a test file's edit prompt includes every selected
production file, complete, as read-only reference context
(`related_context()`). That is correct whenever it fits: a Java method must
never be clipped mid-expression. It is not bounded: if a production candidate
this run just edited grew past its committed size, the test-edit prompt built
immediately afterward has no way to know that in advance, and the only
existing outcome when the assembled prompt exceeds 2000 bytes is

```text
Complete edit context exceeds 2000 bytes; nothing truncated
```

raised by `edit_prompt()` **before** the prompt artifact for that file is ever
written.

## 2. Evidence: Workflow 46

Workflow 46 (`repo-execute-v1`, base commit
`d2a356a59cfb339cf935ba1dc2009e2b85ba267b`, files
`src/main/java/lab/text/Initials.java` and
`src/test/java/lab/text/InitialsTest.java`) reproduced this on the live
Raspberry Pi worker:

- selection passed (inference attempt 1);
- planning passed (attempt 2);
- production generation, extraction and application succeeded: attempt 3
  wrote `Initials.java` with a new `dotted(String)` method (the candidate was
  semantically imperfect - `H.J.2` instead of `H.J.2.` - but that defect was
  never reached; see §7);
- the test-edit prompt for `InitialsTest.java` was never persisted - attempt
  3's artifacts contain only `1-Initials.java.prompt.txt` and
  `1-Initials.java.answer.txt`, no `2-InitialsTest.java.prompt.txt`;
- candidate verification was never reached;
- semantic-replan attempts remained zero;
- the stored workflow error was exactly the generic message above.

This is the same failure class Workflow 40 hit once already
([WORKFLOW-040.md](../workflows/WORKFLOW-040.md)), fixed there by shaving nine
redundant bytes from the edit-prompt boilerplate so that specific committed
shape fit under the limit. Workflow 46 shows that fix was never structural:
any candidate whose generated `Initials.java` was a little larger than the
Workflow 40 fixture reopens the identical failure. Shaving fixed bytes off
boilerplate cannot bound something that grows with model output.

## 3. Root cause

Test editing used the **complete** selected production file(s) as reference
context, in addition to the complete test target, the complete task, and the
target-specific plan text. None of that is bounded against the file a
candidate production edit can actually produce (`extract_java()` accepts up
to 4096 bytes). A production file that fits comfortably as committed can
still push the following test-edit prompt over the controller limit once
edited, and the previous implementation's only response to that was to fail
before the second prompt artifact existed.

## 4. Fix: bounded structural production reference

`select_edit_reference_context(checkout, target, selected, tests,
base_commit, budget)` (`src/nullcode/repo/repo_execute_workflow.py`) replaces
the direct `related_context()` call in the Stage 3 edit loop for every
selected file. It tries the existing whole-file behavior first and returns it
unchanged whenever it fits - byte-identical to `related_context()` alone, so
every prompt that already fit keeps fitting exactly as before.

Only when the whole-file context does not fit does it fall back to
`bounded_production_reference()`:

- a selected production file whose checkout content still equals its
  `base_commit` content is included **complete**. It is not the cause of any
  growth, so there is nothing to gain and real context to lose by shrinking
  it (this is also what keeps the Patient Zero compatibility suite's
  deliberately-over-budget, unedited two-production-file scope decision
  failing exactly as it always has - see §5);
- a selected production file this run **did** edit relative to
  `base_commit` is reduced through `select_java_members()`, sharing whatever
  budget the unedited files leave behind evenly across the remaining edited
  files.

`select_java_members()` never byte-slices Java source. `java_type_skeleton()`
splits a file into its header (through the primary type's opening `{`), its
ordered top-level members (fields, constructors, methods, nested types - each
a complete brace-balanced or semicolon-terminated unit of the original source
text, comments and annotations included), and its footer (the closing `}` and
anything after). The split walks `review_java.code_only()`'s comment/string-
masked view of the source so a brace or semicolon inside a string literal or
comment is never mistaken for a structural boundary; concatenating header,
every member in order, and footer reproduces the original source exactly. A
file containing a Java text block (`"""..."""`, which `code_only()` cannot
safely mask) is never split at all.

Members absent from the base version (added, or changed) are tried before
members that still match it, in original file order within each group,
greedily filling the available budget. A member is only ever wholly included
or wholly omitted; an omission adds one comment line noting that other
members were left out, never a silent truncation. If even the header and
footer (with the omission note, when one is needed) cannot fit an edited
file's share of the budget, reduction fails for that file and
`select_edit_reference_context()` raises (see §6) - it never returns a
partial member or a string it has not verified fits.

### Budget accounting

`edit_reference_budget(task, plan, target, source, limit=EDIT_CONTEXT_LIMIT)`
is the single place that turns "controller limit" into "bytes left for
production reference context". It shares `assemble_edit_prompt()` - the exact
prompt-assembly function `edit_prompt()` itself uses - called once with an
empty reference block, so the byte difference between that shell and the
limit is exact, not estimated (string-concatenation byte length is always the
sum of the parts' own encoded lengths). It reports:

- `limit` - the controller input limit (2000, unchanged);
- `task_bytes`, `plan_bytes`, `target_bytes` - what the task, the
  target-specific plan text and the target file itself contribute;
- `fixed_overhead_bytes` - everything an edit prompt needs besides
  production reference content;
- `reference_budget_bytes` - what is left for that content (can be negative,
  meaning task+plan+target alone already exceed the limit).

`run_job` computes this once per selected file and passes
`reference_budget_bytes` into `select_edit_reference_context()`, so the
budget a prompt is built against and the budget its diagnostics report are
always the same number.

## 5. Safety properties preserved

- The controller's 2000-byte input limit is unchanged and is never bypassed;
  `edit_prompt()` still performs its own final size check as a backstop.
- The complete target file is always available to the edit model; only
  **reference** context for the other selected file(s) is ever reduced.
- No Java reference source is ever byte-sliced mid-method, mid-declaration or
  mid-expression. Reduction only removes or keeps whole top-level members.
- Selected-file scope enforcement, the real-edit check, candidate
  verification, the baseline regression, the added-coverage comparison, the
  behavioral-delta counterfactual, repair, and semantic re-plan are all
  unchanged - this fix touches only how the Stage 3 edit loop builds
  `related` before calling the existing `edit_prompt()`.
- The repair stage's own `related_context()` / `repair_edit_prompt()` call is
  **not** touched by this milestone. `tests/test_repair_routing.py`'s
  `MultiFileRoutingTests.test_two_production_one_test` deliberately pins a
  2-production-file **repair**-context overflow to keep failing closed
  ("Routing grants nothing that bypasses that limit") - the evidence shows
  that scenario is a genuine over-budget selection, not the growth failure
  Workflow 46 hit at the initial edit stage, and this milestone leaves it
  exactly as it was. A future milestone may extend the same bounded strategy
  to repair if a live workflow motivates it.
- Production files are still edited before test files; only the production
  file(s) already edited earlier in the same Stage 3 loop are ever reduced.

## 6. Failure evidence

When even the minimum structurally complete context cannot fit,
`select_edit_reference_context()` raises with the target path, the reference
budget, the whole-file byte count that did not fit, and (in `detail`, JSON)
which file's reduction attempt failed and at what attempted budget - never
raw source content. `run_job` writes this as
`<n>-<file>.context-budget.json` in the attempt directory before re-raising,
so a context-budget failure now leaves a diagnostic artifact naming what was
tried, even though (exactly as in Workflow 46) no prompt artifact exists for
that file. When reduction succeeds, `run_job` also writes
`<n>-<file>.context-strategy.json` recording which files were reduced and how
many bytes each used - kept only for the non-default, structurally-reduced
case, not for every prompt, to avoid noise.

## 7. Tests

`tests/test_edit_context_budget.py`:

- `WorkflowFortySixGrowthTests` reproduces the Workflow 46 shape end to end
  through the real `run_job` edit loop (a synthetic fixture, not Initials/
  InitialsTest): a production edit that grows the file past what the
  unedited whole-file reference context would need, followed by a real
  test-edit prompt that fits, through to `succeeded`. Confirms reduction (not
  a coincidentally generous budget) is what made it fit.
- `MinimumContextCannotFitTests` constructs a task and test file sized so
  that even the reduced minimum (a class header and closing brace) cannot fit
  the reference budget, and asserts a deterministic `failed` status whose
  error names the target, the controller limit and the byte counts involved,
  a `context-budget.json` artifact, and the absence of a `.prompt.txt` for
  that file - mirroring Workflow 46's own missing second artifact.
- `StructuralSafetyTests` proves `java_type_skeleton()` round-trips a source
  file exactly, is not confused by a brace or semicolon inside a string
  literal or comment, never includes a partial member under a tight budget
  (brace count stays balanced; an omitted member leaves no trace of its
  signature), and refuses to split a file containing a Java text block.
- `ExistingSmallContextUnaffectedTests` asserts the whole-file strategy is
  byte-identical to `related_context()` alone whenever it fits, and that
  `edit_reference_budget()` reports every documented field.

Existing suites are unchanged and continue to pass, including
`tests/test_repo_execute_workflow.py::test_workflow_40_initials_test_edit_prompt_fits_without_truncation`
and `tests/test_patient_zero_compat.py`'s and
`tests/test_repair_routing.py`'s over-budget fail-closed cases (§5).

## 8. Limitations

- A production file this run left byte-identical to `base_commit` is never
  reduced, even if it is one of several selected files and is itself large
  enough to be part of an overflow. This is intentional (§5): reducing
  healthy, unedited reference context is not what this fix exists to do, and
  changing it would reopen the deliberately-pinned 2-production-file
  fail-closed test in `test_repair_routing.py`. A selection wide enough that
  its **unedited** production context alone exceeds the budget still fails
  closed exactly as before.
- Reduction is member-granular, not semantic: it does not know which member
  the task actually needs, only which ones differ from `base_commit`. When a
  budget can fit only one of several equally-"changed" members, the one kept
  is whichever the greedy fill reaches first (changed members before
  unchanged, original file order within each group, largest-fits-first is
  not attempted) - not necessarily the one most relevant to the task.
- The repair stage (§5) is out of scope for this milestone.
- If a model returns a no-op "edit" for a production file (byte-identical to
  base) partway through Stage 3, that file is treated as unedited for
  reduction purposes even though the loop has not yet reached the later
  "every selected file must contain a real edit" check. The context this
  produces is still safe (whole-file-or-fail, never sliced); it is simply not
  reduced.

## 9. Runtime validation

Workflow 46 remains preserved, historical failure evidence; its artifacts and
[its workflow record](../workflows/WORKFLOW-046.md) are not modified,
rerun, or reused by this patch. Unit and integration tests (§7) validate the
fix deterministically without any live Ollama/Nullbrain worker run.

This milestone is **not** validated at runtime until a fresh workflow (a new
workflow ID, not a rerun of 46) is executed against the deployed fix and
observed to build and persist a complete test-edit prompt for a production
file that grew past its committed size, proceeding to candidate verification.
Until that fresh run is recorded, treat only the deterministic test evidence
in §7 as settled; the live edit-context-budget failure is fixed in code and
covered by tests, not yet independently reproduced as fixed on the worker.
