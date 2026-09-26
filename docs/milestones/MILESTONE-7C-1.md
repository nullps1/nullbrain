# Milestone 7C-1: model-proposed scope, human-granted scope

A new read-only profile, `repo-scope-v1`, lets the model propose which
committed files a task would have to change. A separate command renders the
`.nullcode.json` change that would grant that proposal, for a human to apply
and commit. Nothing in this milestone grants scope.

It implements [Proposal 7C-1](PROPOSAL-7C-1.md), which is kept as the design
record. §11 records how its open questions were settled.

Implementation commits: `2720e64` (shared-predicate refactor) and `971b8cd`
(profile, review, tests) on branch `claude/milestone-7c1-review-he62e8`; see
§15.

## 1. Objective and the one invariant

Before 7C-1, a task whose real scope nobody anticipated could not run: a
person had to guess the `editable_files` / `editable_test_files` lists by
hand. 7C-1 adds the missing step, **the model proposes a scope; a human grants
it**, without moving authority.

> A model proposal is a suggestion. It is never edit authority.

The only source of execution authority remains the committed `.nullcode.json`
at the pinned base commit. `repo-execute-v1` and the 7C-2 publisher were not
changed to read proposals, and tests (§9) show they do not.

## 2. Trust model

| Step | Who | What it can change |
| --- | --- | --- |
| `submit-scope` / `repo-scope-v1` | the model, bounded | nothing: writes only its own workflow artifacts under `jobs/` |
| `repo_scope_review --scope-reviewed` | a human attests review | nothing: renders a diff into the same workflow directory |
| edit and commit `.nullcode.json` | **a human** | the grant. This commit is the authority |
| `submit-execute` | separately, by a human | unchanged 7B, reading committed configuration only |

There is no automatic transition between any two rows. `repo-scope-v1` never
submits a workflow; the review never opens the target repository; nothing
executes, commits or publishes a proposal.

`--scope-reviewed` means only "a human reviewed this proposal for rendering a
grant diff". It is recorded in `scope-review.json` with `edit_authority:
false` and `applied: false`, and the CLI prints that it is not edit
authority. Like `--acceptance-reviewed`, it is required, never defaulted,
never inferred and never set by another workflow.

## 3. Workflow (`repo-scope-v1`)

Module `src/nullcode/repo/repo_scope_workflow.py`; CLI
`python3 -m nullcode.core.java_workflow submit-scope --repo … --base … --task …`.

`prepare_spec` pins the base commit, bounds the task to 1–500 bytes (7A/7B's
limit, reused), and requires `gradle_workflow.inspect()` to accept the
repository at that commit. The proposal is for `repo-execute-v1`, so the
repository must already be an approved Gradle project. `editable_test_files`
may be absent, because a grant can create it; if present it must be a list of
strings.

`run_job`:

1. clones `--no-hardlinks --no-checkout` into `jobs/workflow-N/repo`, detaches
   at the base commit and removes `origin` (7B precedent);
2. re-runs `inspect()` on the clone at the base commit;
3. builds the inventory with 7A's `committed_inventory()`, unchanged: Git
   blobs only, no symlinks, no `build`/`out`/`target`/`.gradle`/`.git`/
   `node_modules` path components, 7A's extension allowlist, listing clipped at
   1200 bytes as in 7A;
4. **call 1**: the task and inventory in; candidate classes out;
5. validates call 1 deterministically (§5) before any nominated content enters
   a prompt;
6. **call 2**: complete committed contents of only the accepted paths in; the
   final proposal out;
7. validates call 2 (§5), including that it only narrows call 1;
8. checks that the checkout is still clean and still at the base commit;
9. writes the proposal artifact and succeeds.

Nominated contents are read from the pinned commit with `git show`, never from
a working tree. The workflow has no verifier parameter. It never runs Gradle,
Docker, `javac` or generated code, and it has no repair loop and no re-plan.

### State machine

Workflow status, in order: `queued` → `generating` (the worker's existing
claim) → `inspecting` → `selecting-scope` → `validating-selection` →
`proposing-scope` → `validating-proposal` → `succeeded`. Any validation,
inference, prompt-budget, Git or read-only failure goes straight to `failed`,
with the message as the workflow error. No new terminal status was added.

Attempt rows: attempt 1 moves `selecting-scope` → `validating-selection` →
`passed`; attempt 2 moves `proposing-scope` → `validating-proposal` →
`passed`. A failure marks the current attempt `error`. Each attempt records
its inference job ID through the existing `store.attempt(inference_job=…)`
callback.

The worker dispatch chain moved into `java_workflow.run_repository_job()`,
with imports still lazy, and gained an explicit `repo-scope-v1` branch. This
matters: the chain's final `else` runs the legacy Numbers profile, which edits
and commits. A missing branch would not fail safe, so a dispatch test pins it.

## 4. Schemas

**Call 1: candidate selection**

```json
{
  "production_files": ["src/main/java/…/X.java"],
  "test_files": ["src/test/java/…/XTest.java"],
  "context_files": ["any/committed/inventory/path"],
  "reason": "short reason"
}
```

**Call 2: final scope proposal**

```json
{
  "production_files": [{"path": "…", "reason": "…"}],
  "test_files": [{"path": "…", "reason": "…"}],
  "context_files": [{"path": "…", "reason": "…"}],
  "risks": ["…"],
  "summary": "…"
}
```

All listed keys are required. `context_files` and `risks` may be empty lists.
Unrecognized keys are ignored: they are never read and never reach a
validated artifact. A test shows that `"editable_files"`, `"grant": true` and
`"write_config": true` have no effect.

**Persisted proposal** (`scope-proposal.json`): the validated call-2 fields
plus `profile`, `workflow_id`, `task`, `base_commit`,
`committed_config_sha256` (SHA-256 of the committed `.nullcode.json` bytes at
the base), `"edit_authority": false` and a note saying it grants nothing.

## 5. Validation order

Every rule raises `ValueError`; the first failure ends the workflow.

**Call 1** (`validate_selection`):

1. the reply is a JSON object;
2. `production_files`, `test_files` and `context_files` are lists of non-empty
   strings;
3. `reason` is a non-empty string;
4. no path appears twice, within or across the three classes;
5. every path is in the committed inventory shown to the model. This rejects
   nonexistent, uncommitted, excluded-directory, absolute and `..` paths;
6. the edit-scope rules below, on `production_files` and `test_files`;
7. the context rules below.

**Call 2** (`validate_proposal`):

1. the reply is a JSON object;
2. each class is a list of `{"path": non-empty string, "reason": non-empty
   string}` objects;
3. `risks` is a list of strings; `summary` is a non-empty string;
4. no path appears twice, within or across classes;
5. **subset per class**: each path must have been nominated by call 1 **in the
   same class**. A context path in an edit class is rejected as a promotion
   (`promotes context-only … a context file is never edit authority`). A path
   in another class is rejected as a move, and a path call 1 never nominated
   is rejected as an introduction. Omitting paths is allowed;
6. the edit-scope rules again, on the final lists (narrowing can drop below the
   7B shape);
7. the context rules again.

**Edit-scope rules** (`validate_edit_scope`, shared by call 1, call 2 and the
review):

1. no edit path's file name is `.nullcode.json`, `build.gradle`,
   `settings.gradle` or `gradle.properties`, at any depth. The three build
   files are also pinned byte-for-byte by `inspect()`; this is the scope-level
   refusal;
2. production paths satisfy `gradle_workflow.is_production_java`, and test
   paths satisfy `repo_execute_workflow.is_test_java`. These are the exact
   predicates `inspect()` and 7B's `prepare_spec` use;
3. at least one production file and at least one test file;
4. at most `MAX_SELECTED_FILES` (3, imported from 7B) edit files;
5. every edit source is at most 900 bytes at the base commit, checked by 7B's
   own `check_source_limit`;
6. the configuration that granting would produce (`grant_config`: append new
   paths, keep everything else) has at most 8 production and 8 test entries;
7. that configuration passes 7B's own validators: `validate_editable_files`,
   `validate_editable_test_files` and `check_source_limit` over **every**
   granted file, including those granted earlier; and 7B's own
   `validate_selection` accepts exactly the proposed file set against it.

Rule 7 is the reason for the refactor in §6. A valid proposal is exactly a
selection that current `repo-execute-v1` could make once the grant is
committed. It does not mean that every future human grant must add a matched
pair; a human may commit any configuration 7B accepts.

**Context rules** (`validate_context`): at most `MAX_CONTEXT_FILES` (1)
context files; each in the committed inventory; each at most
`MAX_CONTEXT_FILE_BYTES` (900) at the base commit. Context is read-only
evidence and is never part of a grant. A protected file such as
`.nullcode.json` may be read as context.

## 6. Shared predicates (refactor)

Two blocks of existing inline logic became pure functions, so 7C-1 calls the
same code instead of restating it:

| Function | Was inline in | Behavior |
| --- | --- | --- |
| `gradle_workflow.is_production_java`, `validate_editable_files` | `inspect()` | identical checks and messages |
| `repo_execute_workflow.is_test_java`, `validate_editable_test_files`, `committed_source_bytes`, `check_source_limit` | `prepare_spec()` | identical checks and messages |

`inspect()` and `prepare_spec()` now call them. The pre-existing suite passed
unchanged after the refactor (227 OK) before any 7C-1 code was added.
`SharedPredicateTests` pins the exact messages and proves 7B still routes
through the shared functions. `test_resulting_configuration_passes_seven_b_itself`
proves `repo-scope-v1` holds the same function objects. The 7C-2 publisher's
own independent re-derivation of `editable_test_files` was deliberately left
untouched.

## 7. Prompt budget

The controller limit stays 2000 bytes. Both prompts raise `… needs N/2000
bytes; nothing truncated` rather than clip, and the canned test generator
asserts ≤2000 on every call. Evidence is **complete**: whole committed files,
with Java reduced only by 7B's `compact_prompt_java`, which drops leading
indentation and blank lines and leaves text blocks alone. Every non-blank
source line is present; a test checks that. No task, source, file-count,
diagnostic or prompt limit changed.

Measured on the Patient Zero fixture (`tests/test_patient_zero_compat.py`,
17-entry / 558-byte inventory):

| Prompt | Shape | Bytes |
| --- | --- | --- |
| Call 1 fixed text (empty task, empty inventory) | — | 492 |
| Call 1 | Workflow 32 task (81 B) | 1131 |
| Call 1 | compatibility task (148 B) | 1198 |
| Call 1 | 500-byte task | 1550 |
| Call 1 | 500-byte task, 33-entry inventory (1198 B, fixture + 8 fillers) | **2190, fails closed** |
| Call 2 fixed text (empty task, lists, evidence) | — | 458 |
| Call 2, approved 1+1, no context (15 pairs) | Workflow 32 task | 1223–1808, all fit |
| Call 2, approved 1+1, no context (15 pairs) | compatibility task | 1290–1875, all fit |
| Call 2, approved 1+1, no context | 500-byte task | 1642–2227 |
| Call 2, 1+1 incl. unapproved sources ≤900 B (24 pairs) | Workflow 32 task | 23/24 fit; worst `ReportFormatter` + `SlugsTest` **2112, fails closed** |
| Call 2, approved 1+1 + 1 context ≤900 B (195) | Workflow 32 task | 109 fit (56%), up to 2654 |
| Call 2, any eligible 1+1 + 1 context (312) | Workflow 32 task | 133 fit (43%) |
| Call 2, any eligible 1+1 + 2 context (1872) | Workflow 32 task | 191 fit (10%) |
| Call 2, approved 1+2 (15) | Workflow 32 task | 6 fit, up to 2296 |
| Call 2, approved 2+1 (30) | Workflow 32 task | 17 fit, up to 2410 |

**Context cap decision: `MAX_CONTEXT_FILES = 1`.** Two context files fit in
only about one realistic shape in ten, so offering two would mostly produce
fail-closed runs. One fits about half the time. Zero would remove the only
evidence channel beyond the edit files themselves. Clipping context to fit
was rejected: a partial file is misleading evidence. `MAX_CONTEXT_FILE_BYTES`
reuses the 900-byte source limit.

Three-file edit shapes often exceed the limit at call 2 and fail closed. That
matches 7B, where 2+1 shapes already fail closed at edit time
(`MILESTONE-7B-2.md` §7). The limit is not raised.

Tests pin the exact 2000/2001 boundary for both prompts, the realistic
shapes, and the fail-closed worst cases at workflow level. A fail-closed
call-2 prompt makes no second inference call and writes no
`proposal-prompt.txt`.

## 8. Artifacts

Under `jobs/workflow-N/` (runtime state, outside Git):

```
repo/                          isolated clone, origin removed, detached at base
repo-tree.txt                  inventory shown to call 1
attempt-1/
  selection-prompt.txt         only if the prompt fit
  selection-answer.txt         raw reply, kept even when rejected
  selection.json               validated call 1 only
  result.json                  {"passed": true, …} or {"passed": false, "error": …}
attempt-2/
  proposal-prompt.txt
  proposal-answer.txt          raw reply, kept even when rejected
  scope-proposal.json          validated proposal only
  result.json
scope-proposal.json            root copy; written only on success
# written only by the review command:
scope-review.json              attestation, hashes, added paths, edit_authority=false, applied=false
proposed-nullcode-config.json  the configuration a human could commit
proposed-nullcode.patch        unified diff against the committed file
```

A failed run never leaves `scope-proposal.json` or `selection.json` for a
rejected stage. No `repository.json` is ever written.

## 9. Human grant process

```sh
python3 -m nullcode.core.java_workflow submit-scope --repo /srv/nullbrain/repos/<repo> --base main --task '…'
python3 -m nullcode.core.java_workflow wait <id>
# read jobs/workflow-<id>/scope-proposal.json yourself, then:
python3 -m nullcode.repo.repo_scope_review <id> --scope-reviewed
```

The review command:

- refuses without `--scope-reviewed` (argparse exit 2). `render_grant()` has
  no default for the flag and accepts only the literal `True`;
- accepts only a **succeeded** `repo-scope-v1` workflow;
- re-derives, and does not trust, the artifact. It checks that the artifact
  matches its workflow, that `edit_authority` is `false`, and that the
  committed `.nullcode.json` hash at the base matches the proposal. It
  re-checks every edit path against the inventory and re-runs every
  edit-scope rule against the committed configuration;
- reads only the workflow's own clone (`git show`, `ls-tree`, `cat-file`). It
  never runs Git in the target repository;
- writes the three review artifacts and nothing else, and prints the diff with
  the attestation text.

Then **the human** confirms that their branch's `.nullcode.json` matches
`committed_config_sha256`, applies the change by hand, reviews it and commits
it. Only after that commit does `submit-execute` see the new scope.
`test_only_a_human_commit_of_the_configuration_grants_scope` walks this path.
The rendered configuration is rejected by 7B while uncommitted and accepted
once a human commits it.

## 10. Tests

Two new modules: 77 tests and 136 subtests (pytest's subtest count went from
263 to 399). Same harness style as 7A/7B:
real Git fixtures (the Patient Zero shape), real isolated clones and SQLite,
canned inference, no Docker/Gradle/Ollama.

`tests/test_repo_scope_workflow.py` (61 tests):

| Area | Coverage |
| --- | --- |
| Workflow | success leaves source repo byte-identical (including `.git`), clean checkout at base, no `origin`, no `repository.json`; exact status sequence; inference IDs per attempt; call 2 may narrow/drop context; malformed call 1 fails before any nominated content is read (content readers patched to raise); invalid call 1 makes no second call; rejected call 2 keeps raw evidence only; inference failure has no retry; mid-run checkout mutation fails the read-only check; Git audit (only `clone`, one detached `checkout`, `remote remove origin`, `ls-tree`, `show`, `cat-file`, `status`, `rev-parse`; never `add`/`commit`/`push`; nothing run inside the source repo); Gradle/Docker/`javac` patched to raise and the run still succeeds; wrong profile; unknown keys ignored and never persisted |
| `prepare_spec` | task 0/500/501 bytes; committed symlink refused; symlink at a pinned base fails the run before inference; malformed committed `editable_test_files`; absent `editable_test_files` can be created by a grant |
| Call 1 | non-object; missing/mistyped lists (6 shapes × 3 classes); reason; nonexistent, uncommitted, absolute and `..` paths; committed files under `build/` and `out/`; each protected file in each edit class; protected names at depth; protected file as context allowed; five duplicate shapes; wrong production/test class; zero production; zero test; 4 edit files; 3 accepted; >900-byte source; context outside inventory, two context files, oversized context |
| Size boundaries | exactly 900 bytes accepted and 901 rejected, for edit and context files |
| Ceilings | 8 production + a new one rejected `(9)`; re-proposing a granted file accepted; 7 + 1 accepted; the same for tests; `grant_config` is pure and append-only; each shared 7B validator is really consulted |
| Call 2 | shape (7 entry shapes × 3 classes, risks, summary); introduced edit/test/context path; context → production and context → test promotion; production → context and production → test moves; narrowing 2+1 → 1+1 accepted; narrowing to zero production/test rejected; duplicates |
| Prompt budget | exact 2000/2001 boundary for both prompts; evidence complete; all 15 approved 1+1 shapes fit; realistic call 1 fits; over-budget call 2 and worst-case call 1 fail closed in the workflow with no extra inference; caps pinned |
| Review | flag required (five non-`True` values, no default, CLI exit); renders and writes only three artifacts; target repo and checkout byte-identical; only read-only Git in the clone; new test scope rendered; already-granted scope needs no change; seven tampered-artifact shapes rejected with nothing written; failed and non-scope workflows refused |

`tests/test_scope_authority.py` (16 tests):

| Area | Coverage |
| --- | --- |
| Imports | a fresh interpreter importing every execution, publishing and acceptance module loads no `*scope*` module; an AST scan shows only the worker dispatch and the review import `repo_scope_workflow`, and nothing imports the review; the only `render_grant` call passes `args.scope_reviewed` |
| 7B | after a proposal and a rendered grant, 7B still rejects the proposed file as unapproved; proposal artifacts in the source working tree, or committed as ordinary files, change nothing (7B only hashes tracked bytes and never reads them as text); only a human commit of `.nullcode.json` makes the same selection pass; no workflow is ever submitted automatically and no commit appears |
| 7C-2 | `prepare()` refuses succeeded and failed scope workflows with exactly `Scope proposal workflows are not publishable`, before any network, delivery or Gradle path; the publish CLI with `--publish` refuses and writes no `publication.json`; a real successful 7B workflow prepares identically with widening proposal artifacts beside it, and the publisher never reads them |
| Shared predicates | exact messages of the extracted helpers; 7B/Gradle route through them |
| Dispatch/CLI | the worker sends `repo-scope-v1` to the scope workflow and never to the legacy or execute runner; `submit-scope` queues exactly `{profile, repo, base, base_commit, task}`; the review CLI exits 2 without the flag, and its help says it is not edit authority |

## 11. Open questions settled

| Proposal question | Decision |
| --- | --- |
| 1. `--write` staging mode? | **No.** Print and save only. A staged, uncommitted edit looks the same on disk as a grant nobody reviewed. |
| 2. Should a granted scope expire? | **No expiry, lease or TTL.** Accumulated scope is a known limitation (§13). |
| 3. Record the proposal in the workflow store? | **Yes, as inert artifacts.** The attempt rows carry the validated result; files live under `jobs/`. No execution or publishing module imports, reads or accepts them (§10). |
| 4. One model call enough? | **Two**, as 7A: select, validate, then show complete contents of only accepted paths. The 2000-byte limit is unchanged; overflow fails closed. |
| 5. Fixture and mutant checks? | **Yes.** Adversarial fixtures (§10) and mutations (§12). |

Places where the implementation deliberately differs from the proposal text:

- Path classes are the real predicates (`src/main/java/` or `src/test/java/`
  prefix, `.java` suffix, any depth), not the single-segment glob the proposal
  wrote.
- The protected-name refusal matches file names at any depth. The build files
  remain pinned by `inspect()`; 7C-1 did not move that protection.
- The human grant step is split. `--scope-reviewed` only **renders**. The
  grant itself is the human's commit, which is the proposal's "most
  conservative" option.
- Call 2 cannot change a path's class, including demoting an edit candidate to
  context. It may only drop paths.

## 12. Mutation results

Each mutation was applied to a throwaway copy of `src/` and `tests/`, and the
full unittest suite was run against it. Nothing in the repository was
mutated. "Failures" counts failing test and subtest results. The listed tests
are the distinct methods that failed. **All 35 were caught.**

| # | Mutation | Result | Caught by |
| --- | --- | --- | --- |
| 1 | Call-2 subset enforcement removed | 4 failures | `…cannot_introduce_a_path`, `…cannot_move_a_path_between_classes`, `…cannot_promote_context_to_edit`, `…rejected_proposal_leaves_only_failure_evidence` |
| 2 | Context → edit promotion allowed | 2 | `…cannot_promote_context_to_edit`, `…rejected_proposal_leaves_only_failure_evidence` |
| 3a | `.nullcode.json` dropped from protected names | 5 | `…protected_paths_can_never_be_edit_scope`, `…protected_names_rejected_at_any_depth`, `…invalid_selection_ends_the_workflow_without_a_second_call`, `…tampered_proposal_is_re_derived_not_trusted` |
| 3b | Protected-name check removed | 12 | the same four |
| 4a | `--scope-reviewed` not required, defaults true | 2 | `…flag_is_required_and_never_defaulted`, `…review_cli_requires_the_attestation` |
| 4b | `render_grant(scope_reviewed=True)` default | 1 | `…flag_is_required_and_never_defaulted` |
| 4c | Attestation accepts any truthy value | 4 | `…flag_is_required_and_never_defaulted` |
| 5 | Publisher `repo-scope-v1` branch removed (fallthrough) | 3 | `…scope_workflows_are_never_publishable`, `…publish_cli_refuses_before_any_delivery` |
| 6a | Review writes the proposed config into the target repo | 1 | `…review_renders_the_grant_and_writes_nothing_else` |
| 6b | Review writes the proposed config into the checkout | 1 | the same |
| 6c | Workflow writes the granted config into the target repo | 2 | `…valid_proposal_succeeds_and_changes_nothing`, `…review_renders_…` |
| 7a | `repo_execute_workflow` imports the scope module at module scope | 9 import errors | the resulting cycle breaks every module importing 7B |
| 7a2 | …and lazily inside `run_job` (no cycle) | 1 | `…only_the_worker_dispatch_and_the_review_import_the_scope_workflow` |
| 7b | 7B adopts `proposed-nullcode-config.json` from job artifacts | 3 | `…proposal_does_not_widen_execution_scope`, `…proposal_artifacts_inside_the_repository_are_not_authority`, `…only_a_human_commit_…` |
| 7c | 7B adopts one from the source working tree | 1 | `…proposal_artifacts_inside_the_repository_are_not_authority` |
| 7d | 7C-2 publisher reads a proposed config | 1 | `…seven_c_two_ignores_proposal_artifacts` |
| 7e | Publisher lazily imports the review module | 1 | `…only_the_worker_dispatch_and_the_review_import_…` |
| 8a | 3-file selection cap → 4 | 1 | `…more_than_three_edit_files` |
| 8b | Shared 8-production ceiling → 9 | 1 | `…editable_files_rules` |
| 8c | Shared 8-test ceiling → 9 | 2 | `…editable_test_files_rules`, pre-existing `…nine_unique_test_files_are_rejected` |
| 8d | Scope's grant ceiling → 9 | 2 | `…production_ceiling`, `…test_ceiling` |
| 8e | 900-byte source limit → 1000 | 6 | both exact-900 boundary tests, `…oversized_source`, `…context_bounds`, `…context_cap_is_one`, `…tampered_proposal_…` |
| 8f | Scope prompt limit 2000 → 2100 | 5 | both exact 2000/2001 tests, both workflow fail-closed tests, `…context_cap_is_one` |
| 8g | Context-file cap 1 → 2 | 2 | `…context_bounds`, `…context_cap_is_one` |
| 8h | Context byte limit → 1200 | 3 | `…context_bounds`, `…context_cap_is_one`, `…exact_context_limit` |
| 9 | Read-only `git status` check removed | 1 | `…checkout_mutation_fails_the_read_only_check` |
| 10 | Call-1 inventory check removed | 5 failures, 4 errors | `…nonexistent_and_uncommitted_paths`, `…excluded_directories_are_never_inventory`, `…wrong_path_class`, `…context_bounds` |
| 11 | Duplicate rejection removed | 7 | `…duplicates`, `…duplicates_in_the_final_proposal`, `…cannot_move_a_path_between_classes` |
| 12 | Call-2 edit-scope re-validation removed | 1 | `…may_narrow_but_not_below_the_seven_b_shape` |
| 13 | Review trusts the artifact (no re-validation) | 7 | `…tampered_proposal_is_re_derived_not_trusted` |
| 14 | Review skips the committed-config hash | 4 | the same |
| 15 | Worker dispatch branch removed (legacy fallthrough) | 1 | `…worker_dispatches_scope_jobs_to_the_scope_workflow_only` |
| 16 | `origin` kept in the scope clone | 2 | `…git_is_only_used_read_only`, `…valid_proposal_succeeds_…` |
| 17 | Both-classes requirement removed from scope | 2 | `…both_classes_required`, `…may_narrow_but_not_below_…` |
| 18 | Production class predicate removed from scope | 2 | `…wrong_path_class` |

Mutation 7a is caught by an import cycle, not by an assertion. 7a2 is the
honest version of it: a lazy import that avoids the cycle. The AST import
boundary test catches it.

## 13. Known limitations

- **Scope only accumulates.** A grant appends; nothing removes entries. The
  8 + 8 ceilings bound it, and a proposal that would exceed them is rejected,
  but pruning is a human task.
- **Consistent but wrong proposals are accepted**, as with 7B.2 routing. The
  validators prove that a proposal is legal and only narrows call 1. They
  cannot prove that the files are the right ones. The human review is the
  control.
- **The inventory is clipped at 1200 bytes** (7A's behavior, reused unchanged).
  In larger repositories, files beyond the clip cannot be nominated. The
  listing is not annotated with sizes, so the model can nominate a file over
  900 bytes and be rejected for it.
- **Prompt budget.** Three-file and context-bearing shapes often fail closed
  at call 2 (§7).
- **7A's inventory exclusions are stricter than 7B's predicates.** A committed
  `src/main/java/…/build/X.java` is legal 7B scope but cannot be proposed,
  because 7A's inventory excludes any `build` path component. A human can
  still grant it by hand.
- **Stale base.** A proposal is pinned to its base commit. The review prints
  the committed configuration's hash, but it does not compare it against the
  target repository's current branch, so that check is the human's.
- **Repository text is untrusted model input.** Committed file contents
  shown to call 2 can steer the model, as any prompt injection could. They can
  only change which legal proposal comes back. Every rule in §5 is
  deterministic and independent of prompt text, and nothing is granted without
  the human commit.
- **Local evidence.** As in 7C-2, artifacts are unsigned local state. The
  review re-validates them, but a hostile operator who can rewrite the
  database, artifacts and clone together is out of scope.
- **Live validation is outstanding.** Nothing here has run against live
  Ollama or on the Pi. All results above come from canned inference.

## 14. What 7C-1 does not do

- It does not let a model, or any workflow, edit `.nullcode.json`.
- It does not stage, commit or push anything, anywhere.
- It does not hand proposals to `repo-execute-v1`, and it does not submit
  workflows.
- It does not change 7B: no budget, limit, gate, selection rule, repair or
  re-plan behavior. The only 7B change is the pure-function extraction in §6.
- It does not change the 7C-2 publisher beyond refusing `repo-scope-v1`.
- It does not make `repo-scope-v1` publishable, runnable in a sandbox, or
  network-capable.
- It does not raise the 2000-byte, 900-byte, 3-file or 8 + 8 limits.
- It does not touch Rust, Compose, deployment or the controller.

## 15. Validation and commits

Linux x86-64, Python 3.11.15, Git 2.43, no Docker daemon, no Ollama. Canned
inference, as for every earlier milestone's development validation.

| Check | Before (`b9416be`) | After |
| --- | --- | --- |
| `python3 -m unittest discover -s tests` | 227 tests, OK | **304 tests, OK** |
| `pytest -q` | 227 passed, 1 error, 263 subtests | **304 passed, 1 error, 399 subtests** |
| after the §6 refactor alone | — | 227 tests, OK |
| `tests/test_repo_scope_workflow.py` | — | 61, OK |
| `tests/test_scope_authority.py` | — | 16, OK |
| repo-plan, repo-execute, repair-routing, publisher, Patient Zero modules | OK | unchanged counts, OK (within the full run) |
| `py_compile` of `src/` and `tests/`; import of all 21 package modules | — | OK |
| `--help`: `java_workflow`, `submit-scope`, `publish_workflow`, `repo_scope_review` | — | OK |
| `git diff --check` | — | clean |

The single pytest error is the pre-existing collection artifact
(`test_side_logic_observation`, PROJECT_STATE §6 item 13). It is neither
fixed nor worsened. Rust, Compose and deployment files are untouched and were
not retested.

Commits on branch `claude/milestone-7c1-review-he62e8`, based on `main` at
`b9416be`:

- `2720e64`: refactor, extracting the 7B scope predicates into shared pure
  helpers (§6);
- `971b8cd`: `repo-scope-v1`, the scope review, worker dispatch, publisher
  refusal and tests;
- the documentation commit that follows them.

## 16. Live Pi validation progress — Workflows 36–38

Live validation began on 2026-09-26 after PR #12 merged.

- **Workflow 36** submitted a valid `repo-scope-v1` spec against Patient Zero at
  `7a95d1437eb2b4b18f1ee4e6f6436fd5ff4c5bae`, but a stale long-running worker
  process dispatched it through the legacy repository workflow. It failed before
  inference with `Repository needs src/main/java/Numbers.java and
  src/test/java/NumbersTest.java`. Restarting the worker loaded the merged
  dispatch code. No proposal, grant, execution or publication occurred.
- **Workflow 37** reached live scope inference (job 139). The model selected
  `IdentifierFormatter.java` and its natural
  `IdentifierFormatterTest.java`; deterministic validation rejected the test
  because it exceeds the unchanged 900-byte source limit. There was no call 2
  and no grant.
- Patient Zero PR #4 added a dedicated, healthy ungranted fixture pair:
  `Initials.java` (496 bytes) and `InitialsTest.java` (529 bytes). The Pi
  verified the updated lab with `gradle clean test --offline` successfully.
- **Workflow 38** reached live scope inference (job 140) against Patient Zero at
  `feb39e83c710a1b4c9c07a3a74bf4c260104b022`. It selected the exact intended
  production/test pair, but also repeated `Initials.java` in
  `context_files`. The deterministic duplicate-path rule rejected call 1:
  `Scope selection names src/main/java/lab/text/Initials.java more than once
  (production_files, context_files)`. The selection prompt was 1846 bytes and
  the raw answer 302 bytes. No call 2, grant, execution or publication occurred.

Workflow 38 exposed a prompt/validator contract gap rather than an authority
failure: duplicate paths were already rejected deterministically, but call 1 did
not explicitly tell the model that a path may appear in only one list. The
follow-up patch adds only that instruction and a regression assertion. It does
**not** deduplicate model output, retry inference, alter validators, widen scope,
or change the 900-byte, 2000-byte, 3-file, one-context, or 8 + 8 limits.

See [Workflow 36](../workflows/WORKFLOW-036.md),
[Workflow 37](../workflows/WORKFLOW-037.md), and
[Workflow 38](../workflows/WORKFLOW-038.md).

## 17. Live validation still to do on the Pi

1. Run the suite under `unittest` and `pytest` and record the counts
   separately.
2. `submit-scope` against the Patient Zero lab with a task needing a file that
   is not yet granted. Record both replies, the validated proposal or the
   exact rejection, and the prompt sizes.
3. Run `repo_scope_review <id> --scope-reviewed` and confirm that the lab
   checkout is untouched.
4. Commit the grant by hand, then run `submit-execute` against it and record
   the outcome, whatever it is.
5. Record everything in a workflow record and in `PROJECT_STATE.md`.
