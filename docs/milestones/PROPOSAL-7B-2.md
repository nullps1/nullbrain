# Proposal 7B.2: typed repair-target routing

> **Implemented as [Milestone 7B.2](MILESTONE-7B-2.md).** This proposal is kept
> unchanged below as the design record. The implementation settled its open
> questions (§12) in the milestone document's §9. Where the two differ, the
> milestone record describes current behavior.

**Status: proposal only. Nothing described here is implemented.** This document
exists so the 7B.2 design can be argued about before any code is written. It
makes no claim of verification and is not an approval of the design. It was
written against `main` at `c707140`.

7B.2 is a narrow **hardening** increment inside `repo-execute-v1`. It is not an
autonomy step. [7C-1](PROPOSAL-7C-1.md) remains the next autonomy milestone and
is planned to follow this work.

## 1. Problem statement

When candidate verification fails and the failure is repairable,
`repo-execute-v1` asks the model which already-selected file to repair
(`repair_selection_prompt(...)`), validates the reply
(`validate_repair_selection(...)`), and then asks for a complete replacement of
that one file (`repair_edit_prompt(...)`). The selection step already asks the
model to decide "whether the failure comes from the implementation or from an
incorrect test expectation", but that decision is carried only in free-text
prose. The deterministic validator never sees it.

The result is that the model can diagnose one fault domain and route the repair
to the other. Nothing deterministic notices. The contradiction is caught only
later, and only by accident: the repair model is asked to fix a file it has just
said is correct, and returns it unchanged.

## 2. Workflow 32 evidence

Workflow 32 ran live on the Raspberry Pi after the Patient Zero compatibility
tests (PR #7) had merged to `main`.

| Field | Value |
| --- | --- |
| Profile | `repo-execute-v1` |
| Task | "Add a small tested behavior improvement consistent with the existing project API." |
| Selected scope | `src/main/java/lab/TextStats.java`, `src/test/java/lab/TextStatsTest.java` |
| Selection | succeeded |
| Plan | succeeded: "Add a method to count punctuation marks in the text." |
| Outcome | **failed, safely.** No commit, no publication. |

The generated production method `TextStats.countPunctuation(String)` counted
characters found in:

```
.,!?;:'"()[]{}<>/\-_+=&*^%$#@~`
```

The generated test contained three consistent assertions:

```java
assertEquals(3, TextStats.countPunctuation("!!!"));
assertEquals(1, TextStats.countPunctuation("one!two"));
assertEquals(0, TextStats.countPunctuation("onetwo"));
```

and one inconsistent one:

```java
assertEquals(
    4,
    TextStats.countPunctuation(
        ".,!?;:'\"()[]{}<>/\\-_+=&*^%$#@~`"
    )
);
```

Candidate verification correctly failed:

```
JUnit: countPunctuation(): expected: <4> but was: <31>
```

31 is the number of punctuation characters in that input, so the production
code was right and the test expectation was wrong.

The repair-selection model returned:

```json
{
  "file": "src/main/java/lab/TextStats.java",
  "reason": "The test expectation is incorrect. The method should count all punctuation marks, not just a subset."
}
```

The reply contradicts itself: the reason names a **test** fault, but the
target is the **production** file. `validate_repair_selection(...)` accepted
it. The workflow built a repair prompt for `TextStats.java`, the model returned
that file unchanged, and the existing no-op guard failed closed with:

```
Repair 1 returned unchanged source
```

Every existing safety property held: scope was not widened, no gate was
bypassed, nothing was committed or published. What was lost was the only
repair that could have succeeded — a one-line test fix — and the workflow
spent the repair on a file its own diagnosis had cleared.

Workflow 32 is one observation. It is enough to show the gap exists; it is not
a measurement of how often it happens.

## 3. The exact current gap

`validate_repair_selection(data, selected)` in
`src/nullcode/repo/repo_execute_workflow.py` checks, today:

1. the reply is a JSON object;
2. `data["file"]` is in the candidate list it is given (the already-approved
   selection, or only the selected test files for an insufficient-test-count
   failure);
3. `data["reason"]` is a non-empty string.

It does **not** check that the chosen file's category agrees with the
diagnosis, because the diagnosis is not a field. The category exists only in
prose, and prose is not something the validator can or should parse.

Everything downstream of the validator is unchanged and still enforces:

- the repair must change the target file (`Repair N returned unchanged source`);
- the repaired tree must change exactly the originally selected files;
- the repaired candidate is fully re-verified, then passes baseline regression,
  the added-coverage comparison, the behavioral-delta counterfactual and
  targeted review, exactly like an unrepaired one.

So the gap is in routing quality, not in safety. 7B.2 is about making a
self-contradictory routing reply fail at the point it is made, with an honest
reason, instead of one model call later with a misleading one.

## 4. Proposed typed repair-routing schema

Replace the reply shape

```json
{"file": "...", "reason": "..."}
```

with

```json
{
  "fault_domain": "test",
  "file": "src/test/java/lab/TextStatsTest.java",
  "reason": "The expected punctuation count is inconsistent with the input."
}
```

**Closed vocabulary**, initially exactly two values:

| `fault_domain` | Meaning | Permitted `file` |
| --- | --- | --- |
| `production` | The implementation is wrong; the tests describe the intended behavior. | one of the repair candidates under `src/main/java/` that is in the selected production files |
| `test` | A test expectation or test code is wrong; production is correct. | one of the repair candidates that is in the selected test files |

Domain membership is decided by the same sets the workflow already computes —
the approved `editable_files` / `editable_test_files` lists intersected with
the selection (`selected_tests = [name for name in selected if name in tests]`)
— never by a path heuristic invented for this purpose.

Values such as `both`, `unknown`, `infrastructure` or `build` are deliberately
**not** in the initial vocabulary. A repair edits exactly one file, so `both`
has no single-file meaning; `unknown` has no safe action; infrastructure and
build failures are already non-repairable and never reach this prompt. Any
value outside the vocabulary fails closed.

The prompt would present the candidates grouped by domain (production files,
then test files) and ask for `fault_domain` before `file`. Both are advisory
prompt changes. The prompt's instruction to reason literally about task
semantics and not to trust a JUnit "expected" value blindly stays.

`repair-selection.json` would record `fault_domain` alongside the existing
`repair_number`, `file` and `reason`, and the repair entry in `result.json`
would carry it too, so routing decisions become countable across runs.

## 5. Deterministic invariants

The validator (a replacement for, or extension of,
`validate_repair_selection(...)`) would enforce, in order, and reject with a
specific error on the first violation:

1. The reply is a JSON object.
2. `fault_domain` is present, is a string, and is exactly `"production"` or
   `"test"` (no case folding, no trimming into validity, no synonyms).
3. `file` is present, is a string, and is one of the repair candidates offered
   for this round. This is today's check and is kept verbatim.
4. `fault_domain == "production"` ⇒ `file` is a selected production file.
5. `fault_domain == "test"` ⇒ `file` is a selected test file.
6. `reason` is a non-empty string.
7. For an insufficient-executed-test-count failure, where only selected test
   files are offered today, `fault_domain` must be `"test"`. A `"production"`
   reply fails closed — it cannot name a valid file anyway, and the error should
   say why.

What the validator must **not** do:

- **Never infer or auto-correct.** A contradictory reply is not repaired by
  picking the file that matches the domain, or the domain that matches the
  file. Either rewrite would be the workflow guessing which half of a
  self-contradictory model answer to believe.
- **Never parse `reason`.** No keyword matching on words like "test" or
  "expectation". That is brittle, trivially evaded, and would turn advisory
  prose into a pseudo-control. `reason` stays evidence for humans.
- **Never widen scope.** Every permitted file is already in the offered
  candidate list; the domain check can only remove choices.

### What this proves, honestly

The model produces `fault_domain` as well as `file`. The validator can check
only that the two **agree**; it cannot check that either is **right**. A reply
of `{"fault_domain": "production", "file": "<production file>", "reason":
"the test expectation is wrong"}` is internally consistent in its typed fields
and will pass, because the contradiction is in prose. In that case behavior is
exactly today's: the repair is attempted, and the existing unchanged-source
guard, re-verification and later gates remain the enforcement.

7B.2 therefore narrows the failure class. It does not eliminate it. It turns
an implicit category into an explicit, validated and recorded one, and
catches the self-contradiction whenever the model states its domain honestly —
as Workflow 32's reasoning did.

## 6. The repair-target reselection question

The question: if the first routing reply is contradictory, may the workflow
ask **once more** for a routing decision, inside the same repair attempt,
without it counting as an implementation repair?

### What such a design would have to look like

If it were adopted, it would need all of the following:

- a separate constant, e.g. `REPAIR_ROUTING_RESELECTION_BUDGET = 1`, defined
  apart from the two-repair loop, the same way `SEMANTIC_REPLAN_BUDGET` is;
- it runs inside the same `repair_number` and the same `attempt-<n>` directory,
  with its own artifacts (`selection-2-prompt.txt`, `selection-2-answer.txt`),
  never overwriting the first reply;
- it is a routing call only: no source is written, no verification runs, and
  it is never counted, recorded or phrased as a repair;
- the offered candidate list is identical to the first call's;
- a second contradictory or malformed reply fails closed; there is no third;
- the prompt must fit the existing 2000-byte limit without raising it.

That shape would not increase the code-repair budget, would not create a third
implementation repair, would not widen scope, and would not touch any
verification or review gate.

### Why it is not recommended now

**Recommendation: no reselection in 7B.2. A contradictory routing reply fails
closed immediately.** The reasons:

1. **Reselection can launder the signal it is meant to act on.** The only new
   information a reselection prompt can supply is "your previous answer was
   contradictory". The cheapest way for a model to satisfy that is to change
   `fault_domain` to match the file it already picked. The validator cannot
   tell that from a genuine correction, so a *detected* contradiction would be
   converted into an *undetectable* one — exactly the Workflow 32 path, now
   with a consistent-looking record. Immediate failure preserves the evidence.
2. **Telling the model which half is wrong is inference by another name.** A
   prompt that says "you said test, so pick a test file" is the workflow
   auto-correcting through the model. The proposal forbids silent correction;
   routing it through a second inference call does not make it less silent.
3. **The evidence base is one workflow.** There is no measurement of how often
   typed replies contradict, or of whether a second ask would help. Budget 1
   for the semantic re-plan was introduced with telemetry for the same reason;
   here the telemetry does not exist yet.
4. **It adds a new state, a new budget and new artifacts** to a loop whose
   budgets are part of the safety model, for a benefit that is unmeasured.
5. **Immediate failure costs little and is honest.** The workflow already
   fails in Workflow 32's case; 7B.2 only makes it fail one model call earlier,
   with an accurate reason, and records the contradiction.

Reselection should be reconsidered only as its own later increment, once
recorded `fault_domain` data from real runs shows contradictory routing is
common enough to matter, and only with an answer to point 1 — for example a
design where the second reply must be accepted or rejected on grounds the
first reply cannot influence. Absent that, the safe answer remains immediate
failure.

## 7. Attempt-budget implications

None, by construction:

- The ordinary repair loop stays `for repair_number in (1, 2)`: at most two
  implementation repairs per candidate.
- `SEMANTIC_REPLAN_BUDGET` stays 1; a re-planned candidate still gets its own
  two repairs, and typed routing applies to both generations identically.
- Routing validation adds no model call. It changes the reply shape of an
  existing call and validates it more strictly.
- A routing failure consumes the repair round it occurs in, exactly as an
  invalid or unapproved repair selection does today: the workflow ends. It
  does not fall through to the next `repair_number`.
- Infrastructure failures still never consume a repair.

## 8. Failure behavior

A routing violation raises `ValueError` from the validator, which the existing
`run_job` exception handler turns into:

- workflow status `failed` with the validator's message;
- an attempt row with `phase="error"` at the repair attempt directory;
- no repair prompt built, no repair inference call, no file written, no
  verification, no commit, nothing publishable;
- `timing.json` and `semantic-replan.json` still written, as for every
  terminal state.

Before raising, the workflow should persist the rejected reply as evidence —
the raw `selection-answer.txt` is already written before validation — and
ideally a `repair-routing.json` stating the offered candidates by domain, the
returned `fault_domain` and `file`, and the violated invariant.

Proposed error messages, specific enough to count:

| Case | Message |
| --- | --- |
| missing / non-string / unknown domain | `Repair N routing has invalid fault_domain: <value>` |
| file outside candidates | `Repair selected an unapproved file: <path>` (unchanged) |
| test domain, production file | `Repair N routing is contradictory: fault_domain 'test' but <path> is not a selected test file` |
| production domain, test file | `Repair N routing is contradictory: fault_domain 'production' but <path> is not a selected production file` |
| production domain on an insufficient-count failure | `Repair N routing is contradictory: insufficient test count requires fault_domain 'test'` |

Whether a routing failure deserves its own terminal status (like
`rejected-no-behavioral-delta`) rather than `failed` is an open question
(§12); the default is `failed`.

## 9. Required regression tests

All with the existing `tests/test_repo_execute_workflow.py` harness: real Git
fixture and checkouts, canned inference, canned verifier.

1. **Workflow 32 fixture.** Candidate production correct, candidate test with
   one wrong expectation (the punctuation-count shape). Routing reply
   `{"fault_domain": "test", "file": <production>}` fails closed with the
   contradiction message; exactly one routing call was made; no repair prompt
   was built; no commit, no `repository.json`.
2. **Mirror case.** `{"fault_domain": "production", "file": <test>}` fails
   closed.
3. **Consistent test routing succeeds.** The Workflow 32 fixture with
   `{"fault_domain": "test", "file": <test>}` and a corrected test proceeds to
   the repair edit, re-verifies, and reaches `succeeded` through every
   downstream gate.
4. **Consistent production routing** still repairs production, as today.
5. **Malformed domains**: missing, `null`, non-string, empty, `"Test"`,
   `" test"`, `"both"`, `"unknown"` all fail closed without a repair call.
6. **Legacy shape** `{"file": ..., "reason": ...}` with no domain fails closed
   (no silent default).
7. **Unapproved file** still fails with the existing `unapproved file` error,
   whatever the domain.
8. **Insufficient-count failure**: only test files still offered; `test` domain
   with a test file proceeds; `production` domain fails closed.
9. **Three-file selections** (2 production + 1 test, 1 production + 2 tests):
   each domain accepts exactly its own files.
10. **Re-planned candidate**: typed routing applies identically to attempts 9
    and 10 after a semantic re-plan.
11. **Budget unchanged**: a consistent routing followed by two failed repairs
    still ends after exactly two repairs; no routing path produces a third
    repair edit or a second routing call.
12. **Artifacts**: `repair-selection.json` and the repair entry in
    `result.json` record `fault_domain`.
13. **Prompt budget**: the new repair-selection prompt stays within 2000 bytes
    for the Patient Zero fixture's selections, and still raises rather than
    truncating at the extremes (see §12 on headroom).
14. **Advisory prompt enforces nothing**: a reply that ignores the prompt's
    grouping is rejected by the validator, not by prompt cooperation — the
    same pattern as
    `test_the_advisory_prompt_enforces_nothing_deterministic_does`.

Existing tests that assert on the current prompt text or reply shape (for
example the `Selected files: ` line in
`test_insufficient_count_repair_is_offered_only_selected_test_files`, and the
canned `REPAIR_*` answers) will need deliberate updates. Each such update must
preserve what the test protects, and should be called out in the
implementation's milestone record.

## 10. Required mutation tests

Applied to throwaway copies of `src/` and `tests/`, as in 7B hardening and
7B.1. Each must be caught by at least one test:

| Mutation | Must be caught by |
| --- | --- |
| Domain/file agreement check removed | Workflow 32 fixture, mirror case |
| Contradiction auto-corrected to the file's domain | Workflow 32 fixture (asserts failure, not success) |
| Contradiction auto-corrected to the domain's file | Workflow 32 fixture (asserts no repair edit prompt) |
| Missing `fault_domain` defaulted to the file's domain | legacy-shape test |
| Unknown domain accepted | malformed-domain tests |
| Domain sets derived from path prefixes instead of the approved lists | a fixture where the two disagree, if one can be built within current scope rules; otherwise record why not |
| A second routing call permitted after a contradiction | budget-unchanged test (counts inference calls) |
| Repair loop widened to three rounds | budget-unchanged test |
| `fault_domain` not recorded in artifacts | artifact test |

## 11. What this does NOT change

- the two-repair budget, the semantic re-plan budget, or any attempt numbering;
- the 2000-byte prompt limit, the 700-byte diagnostic cap, the 900-byte source
  limit, or the 3-file selection cap;
- file selection, planning, editing, or editable scope rules;
- the unchanged-source guard, scope check after repair, candidate
  verification, minimum executed-test floor, baseline regression,
  added-coverage comparison, behavioral-delta counterfactual, evidence levels,
  or targeted review;
- `accepted-java-v1`, which has its own single-target repair and no routing
  choice;
- the 7C-2 publisher, draft-only publication, `succeeded`-only eligibility, or
  human acceptance;
- `.nullcode.json` authority, which 7C-1 also leaves intact.

## 12. Open questions

1. **Prompt headroom.** The current repair-selection prompt's fixed text is
   571 bytes. With a 500-byte task, a 700-byte diagnostic, a 220-byte plan
   summary and three paths, the prompt can already exceed 2000 bytes and raise.
   Adding the schema and domain grouping costs roughly 100–150 more bytes. Is
   that acceptable, or should the added wording be offset by tightening
   existing prose? Raising the limit is not an option.
2. **Terminal status.** Should a contradictory routing reply end as `failed`,
   or get a distinct terminal status so it is queryable like
   `rejected-no-behavioral-delta`? A distinct status is more countable; `failed`
   is the smaller change.
3. **Should the model supply `file` at all** when the chosen domain has exactly
   one candidate? Requiring it keeps the agreement check meaningful and avoids
   inference; omitting it would be a form of derivation the proposal otherwise
   rejects. Current recommendation: always require it.
4. **Unchanged-source signal.** In Workflow 32 the unchanged repair was itself
   evidence that the target was wrong. Should that be recorded as a routing
   signal in telemetry? It must not become a retry path — that would be a
   third code-generation call in a repair round.
5. **Reselection, later.** What recorded evidence would justify revisiting §6,
   and what reselection design avoids laundering the contradiction?
6. **Vocabulary growth.** Is there a case for a third domain once data exists
   (for example a repair that needs the plan changed rather than a file)? That
   would be a re-plan, which already has its own separate budget.

## 13. Acceptance criteria for implementation

An implementation is acceptable only if:

- every invariant in §5 is enforced deterministically in code, with no parsing
  of `reason`;
- no contradictory, malformed or legacy-shaped reply reaches a repair edit
  prompt;
- no auto-correction path exists, in code or in prompt wording;
- the two-repair budget and every other budget and limit in §11 are unchanged,
  and a test pins that;
- every regression test in §9 exists and passes, and every mutation in §10 is
  caught, with results recorded;
- the full Python suite passes under `unittest` with no new failures, and the
  pre-existing pytest collection error is neither hidden nor made worse;
- the prompt changes are documented in code as advisory, matching how 7B.1
  labels its diagnostic prompt;
- a milestone record `MILESTONE-7B-2.md` is written at the time, and
  `PROJECT_STATE.md`, `CHANGELOG.md` and the milestone index are updated per
  the documentation-trail convention in
  [`../PROJECT_STATE.md`](../PROJECT_STATE.md#11-documentation-trail-convention).

## 14. Live validation plan after implementation

On the Pi, after merge, with the Patient Zero lab:

1. Run the full Python suite; record the unittest and pytest results separately.
2. Re-run Workflow 32's task against the same lab state. Because model output
   is not deterministic, record what happens rather than expecting a specific
   path:
   - a contradictory typed reply must fail closed with the routing message,
     no repair edit call, and no commit;
   - a consistent `test` reply must proceed to a test repair and either succeed
     through every gate or fail on an existing gate;
   - a consistent-but-wrong `production` reply must hit today's behavior
     (unchanged-source guard or later gates).
3. Run at least one task whose natural failure is a production bug, to confirm
   `production` routing still repairs production end-to-end.
4. Confirm `repair-selection.json` records `fault_domain` for every repair.
5. Record the workflow IDs, routing outcomes and final states in the milestone
   record, including failures. A single live pass proves the mechanism runs;
   it does not prove routing accuracy.

## 15. Relationship to 7C-1

7B.2 is a hardening step on the existing execution path. It gives the model no
new authority, adds no scope, and changes no budget. It should land first
because 7C-1 will put more varied, model-proposed scopes through this same
repair loop, and routing mistakes are cheaper to fix before that variety
arrives.

[7C-1 — model-proposed scope, human-granted scope](PROPOSAL-7C-1.md) remains
the **next autonomy milestone** after this hardening increment. Nothing in 7B.2
changes the 7C-1 proposal or its invariant that a model proposal is never an
edit grant.
