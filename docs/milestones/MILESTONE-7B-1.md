# Milestone 7B.1: behavioral-delta evidence hardening

[7B hardening](MILESTONE-7B-HARDENING.md) added the behavioral-delta
counterfactual: the candidate test suite is run against pinned-base
production, and the candidate continues only if that run does **not** fully
pass. That gate works — Workflow 27 was correctly rejected, Workflow 28
correctly accepted — but it treated two very different kinds of evidence as
interchangeable, and it made "no behavioral delta" terminal on the first
attempt.

This increment:

1. separates **behavioral** from **structural** novelty evidence,
2. chooses the API compile-failure policy from fixture evidence rather than
   intuition,
3. gives a no-delta candidate exactly **one** separately bounded, fully
   re-verified semantic re-plan,
4. measures what the counterfactual and the re-plan actually cost,
5. broadens validation past the single-file Slugs smoke fixtures, and
6. investigates the test-side logic risk and records honestly what is and is
   not mitigated.

No existing safety gate was weakened. The changes are in
`src/nullcode/repo/repo_execute_workflow.py`, plus a narrow publisher change
in `src/nullcode/publish/repo_execute_publish.py`.

## 1. Evidence strength

The two distinguishing classifications do not prove the same thing:

| Classification | `evidence_level` | What actually happened |
| --- | --- | --- |
| `distinguishing-test-failure` | `behavioral` | The candidate suite compiled against pinned-base production and a case **failed** there. Observed runtime behavior differs. |
| `distinguishing-api-compile-failure` | `structural` | The candidate suite could not be compiled against the base at all. The API surface differs; **no assertion ever ran**, so no runtime behavior was observed. |
| `no-behavioral-delta` | `none` | Nothing distinguishes candidate from base. |
| `infrastructure-failure` | `none` | The hybrid environment broke; not evidence either way. |

`evidence_level()` maps an unrecognized classification to `none`: an outcome
the classifier does not positively explain is never promoted to evidence.

Every place the classification is reported now carries the level with it —
`behavioral-delta.json`, the final workflow result, and the draft-PR body —
together with a one-sentence `evidence_summary` that says what the evidence
proves and, for structural evidence, what it does not:

> Structural evidence only: the candidate suite could not be compiled against
> pinned-base production, so no assertion executed against the base. This
> proves the API surface differs. It does NOT prove that runtime behavior
> differs.

## 2. API compile-failure policy: measured, then chosen

### The experiment

`tests/test_behavioral_delta_policy.py` is the experiment, not a description
of its conclusion. It runs two fixtures through the real workflow:

* **Fixture 1 — legitimate new API.** `Slugs.slugifyUnicode` normalizes
  accented input; the candidate test asserts its output. The base does not
  declare the method, so the hybrid cannot compile the test sources.
* **Fixture 2 — trivial new API.** `Slugs.marker()` returns `""`; the
  candidate test asserts exactly that. The base again cannot compile the test
  sources.

### The finding

The evidence the two produce is **identical**: same classification, same
evidence level, same diagnostic, same compile exit code, same (absent) JUnit
evidence. The test asserts that equality field by field, because it is the
whole basis of the decision. A test-compilation failure aborts the suite
before any assertion runs, so the counterfactual cannot separate a valuable
new API from an empty one, and neither can the JUnit reports, the executed-case
counts or the deterministic review.

### Decision matrix

| Policy | Legitimate new API | Trivial new API | Legitimate rejected | Weak admitted |
| --- | --- | --- | --- | --- |
| **A** allow structural | continue | continue | no | yes |
| **B** require more evidence | unavailable | unavailable | — | — |
| **C** structural insufficient | reject | reject | **yes** | no |

* **Policy B is not implementable here.** Separating the two fixtures requires
  knowing what the new method *does*, which means parsing Java/JUnit sources —
  explicitly out of scope for this milestone, and brittle rather than robust
  even if it were in scope. One candidate check was considered and discarded
  as redundant: cross-checking the missing symbol names against the production
  diff adds nothing, because candidate verification has already proved the
  candidate suite compiles against candidate production, so a symbol missing
  from the base is necessarily one the candidate provides.
* **Policy C's cost is a false negative per false positive avoided.** It
  rejects *every* new-API task, which is an ordinary, common shape for this
  profile ("add method X and test it"), in order to exclude the weak ones.
* **Policy A's cost is that a structurally novel but semantically weak change
  can reach `succeeded`.** It still has to clear the minimum executed-test
  floor, the baseline regression, the added-coverage comparison, the
  deterministic review, draft-only publication and human acceptance.

### Decision

**Policy A is selected**, with the weakness recorded rather than hidden:
`evidence_level = structural` travels with the classification into every
artifact and into the draft-PR body, so nothing downstream can mistake it for
demonstrated runtime novelty. `API_COMPILE_POLICY` names the choice in one
place and `policy_decision()` is shared by the workflow and the experiment, so
the shipped behavior and the evidence used to choose it cannot drift apart.

If structural admits turn out to be a real problem in practice, the recorded
evidence level makes them countable, and the next step would be a separate
increment that obtains behavioral evidence for new APIs — not a source parser.

## 3. One bounded semantic re-plan

A no-delta candidate is not a broken implementation. It compiled, its tests
passed, it did not regress the original suite and it added coverage. What was
empty is the **task interpretation**: the model chose behavior the repository
already provides. That is a different failure from the ones the ordinary
repair loop handles, so it gets its own budget:

```
behavioral-delta -> no-behavioral-delta
   -> behavioral-delta-diagnosing   (deterministic evidence + one model call)
   -> behavioral-delta-replanning   (new plan, same approved scope)
   -> new candidate                 -> every gate again, from planning onwards
```

* `SEMANTIC_REPLAN_BUDGET = 1`, defined separately from the repair budget.
  Neither can spend the other's allowance.
* The ordinary repair budget is **unchanged at two attempts per candidate**.
  A re-planned candidate is a new candidate and gets its own two, exactly as
  the first one did; the workflow is bounded at two candidate generations, so
  at most two re-plans' worth of repairs (2 × 2) can ever run. Nothing raises
  the per-candidate budget.
* A repeated no-delta terminates fail-closed on the same terminal state as
  before, `rejected-no-behavioral-delta`. So does an invalid diagnosis reply,
  a plan naming an unselected file, a failed candidate verification, a failed
  baseline regression, or broken hybrid infrastructure.

### The superseded candidate is discarded, not continued

Before the diagnosis prompt is even built, every selected file is written back
to pinned-base content and the result is checked against the hashes taken
before the first edit (`reset_to_base`). A partial reset raises rather than
producing a hybrid of two candidates. The re-planned candidate therefore:

* plans from base sources, not from the superseded candidate's sources,
* is compiled and tested on its own evidence,
* runs its own baseline regression and added-coverage comparison,
* runs its own counterfactual with its own snapshot-hash manifest,
* and is reviewed and committed only on that evidence.

Only immutable, already-established facts carry over: the clone, the pinned
base commit, the approved file scope and the file selection.

### The diagnostic prompt is advisory

The prompt tells the model the candidate's tests also pass against pinned-base
production, and asks it to pick a behavior the base does not already satisfy.
It also asks it not to widen file scope, not to weaken tests and not to touch
build configuration.

**None of that is a safety control**, and the code says so in the same words:

> The diagnostic prompt is ADVISORY ONLY. Nothing in it is a safety control: a
> re-planned candidate is a new candidate and is re-verified from scratch by
> the same deterministic gates as the first one […] No safety property depends
> on the model obeying prose.

`test_the_advisory_prompt_enforces_nothing_deterministic_does` pins this: a
re-plan that ignores the advice and names an unselected file is rejected by
plan validation, not by the model's cooperation.

### State history

The history shows re-interpretation, never repair:

| Attempt | Phase |
| --- | --- |
| 3 | `superseded-no-behavioral-delta` |
| 6 | `behavioral-delta-diagnosing` → `behavioral-delta-replanning` |
| 7 | re-planning (passed) |
| 8 | re-planned candidate: editing, verification, counterfactual, commit |
| 9, 10 | that candidate's own two possible repairs |

Attempt numbers stride by five per candidate generation, so a re-planned run
never overwrites the superseded candidate's evidence.

### Telemetry

Budget 1 is an initial policy, not settled truth, so `semantic-replan.json` is
written for **every** terminal state:

```json
{
  "semantic_replan_budget": 1,
  "semantic_replan_attempts": 1,
  "semantic_replan": {
    "attempt": 1,
    "reason": "no-behavioral-delta",
    "started_at": "2026-09-21T21:10:08+00:00",
    "duration_ms": 18234,
    "superseded_attempt": 3,
    "diagnosis": "...",
    "behavior": "...",
    "prompt_is_advisory": true,
    "resulting_classification": "distinguishing-test-failure",
    "resulting_evidence_level": "behavioral",
    "final_result": "succeeded",
    "reached_succeeded": true
  }
}
```

That answers, from accumulated runs rather than from intuition: how often does
one re-plan rescue a workflow, how often does it fail again, and how much
latency does it add?

## 4. Timing

`StageTimer` and `VerificationTimer` measure with `time.monotonic_ns()`, which
cannot jump backwards when the wall clock is adjusted. `VerificationTimer`
derives its split from the phase callback the shared Gradle verifier already
drives — everything before `compiling` is preparation, `compiling` to `testing`
is compilation, the rest is test execution — so no other profile's verifier
contract changes.

`timing.json` is written for every terminal state:

```json
{
  "clock": "time.monotonic_ns",
  "timing_ms": {
    "candidate_verification": 23120,
    "baseline_verification": 22781,
    "behavioral_delta_prepare": 143,
    "behavioral_delta_compile": 10930,
    "behavioral_delta_test": 12831,
    "behavioral_delta_total": 23904,
    "semantic_replan": 18234,
    "workflow_total": 104223
  }
}
```

The counterfactual's own split is also embedded in `behavioral-delta.json`,
and `timing_ms` appears in the final workflow result.

**Timing was measured but no optimization threshold was set, because current
evidence is insufficient to justify one.** The numbers available in this
environment measure orchestration only: there is no Docker daemon here, so no
real Gradle compile or test has been timed. Real per-stage durations have to
come from the Pi. If they turn out to be concerning, the right response is a
separate optimization increment, recommended from that data — not a quiet
change inside this patch.

## 5. Broader validation

| Fixture | Shape | Outcome |
| --- | --- | --- |
| D1 | Workflow 26-style no-delta | re-plan runs; rescued run succeeds, repeated no-delta terminates `rejected-no-behavioral-delta` |
| D2 | assertion-level change | `distinguishing-test-failure`, `evidence_level=behavioral`, succeeds |
| D3 | legitimate new API | `distinguishing-api-compile-failure`, `evidence_level=structural`, succeeds under Policy A |
| D4 | trivial new API (adversarial) | identical evidence to D3 — the basis of the policy decision |
| D5 | 2 production + 1 test, 1 production + 2 tests, and 2 + 2 | every selected production file pinned back to base, every candidate test overlaid, expected and actual hybrid hashes equal, no candidate production leakage |
| D6 | 8 production files, 8 test files, 48-case suite | same gates, full-tree manifest, timing and artifact size recorded |

D5's 2 + 2 case is exercised against the real construction —
`pinned_base_production()` and `hybrid_manifest()` on a real checkout — rather
than through `run_job`, because the approved selection is capped at three
files and **that cap was not raised**. The workflow-level multi-file cases run
the full three-file selections the cap does allow, in both shapes.

D6 recorded, on the canned verifier: 22 tracked files, 48 cases, 43 artifact
files, ~45 KB of artifacts per workflow. Artifact size scales with the number
of verification rounds, not with suite size.

## 6. Test-side logic: what is bounded and what is not

Candidate test sources are deliberately overlaid into the counterfactual, and
they can contain helpers, fixtures and real computation. The adversarial
fixture is a test that computes its own expected value with a helper that
reimplements the production algorithm:

```java
private static String expectedSlug(String text) {
    String lowercased = text.toLowerCase(Locale.ROOT);
    String normalized = lowercased.replaceAll("[^a-z0-9]+", "-");
    return normalized.replaceAll("^-*|-*$", "");
}
@Test void hyphens() {
    assertEquals(expectedSlug("--Hello-World--"), Slugs.slugify("--Hello-World--"));
}
```

**What the gate does bound.** The candidate test sources are byte-identical in
the candidate run and in the hybrid run — the manifest pins their SHA-256 —
so the *only* thing that differs between the two worlds is production content.
Any outcome difference is therefore caused by production code, helpers or no
helpers. `test_candidate_tests_are_byte_identical_in_both_worlds` pins this.

**What it does not bound.** It cannot show that the assertion is *meaningful*.
The fixture above is tautological — it asserts that production agrees with a
copy of production — and it passes every gate, including the counterfactual,
because the copied logic differs from the base. The deterministic review does
not catch it either: those rules are targeted text checks for the Numbers
profile (impossible int range checks, demo `main`, console output), and none
of them reads a test helper. `test_the_deterministic_review_does_not_catch_it`
asserts exactly that, so the limitation cannot quietly regress into a claim
the system does not support.

**No deterministic mitigation was added**, and none was faked. Every candidate
check either needed Java semantics (out of scope, and brittle) or would have
banned legitimate helpers and fixtures. What was added instead is an
**advisory, non-gating observation** recorded with the review evidence:

```json
"test_side_logic": {
  "advisory": true,
  "gating": false,
  "added_lines": {"production": 1, "test": 3},
  "test_added_line_share": 0.75,
  "note": "Advisory diff statistics only. Test helpers and fixtures are legitimate and nothing is rejected on these numbers..."
}
```

It is pure text accounting over the diff the workflow already produces — no
Java parsing, no thresholds, no branch anywhere in the workflow. Its purpose is
to let a human reviewer see at a glance when new logic is concentrated on the
test side.

> **Known limitation.** `repo-execute-v1` cannot prove that all meaningful
> behavior exercised by candidate tests lives in production code, and cannot
> detect a test whose expected value is produced by logic copied out of the
> candidate's own production change. The counterfactual proves the *difference*
> is caused by production; it does not prove the *assertion* is worth making.
> Human acceptance remains the control for that.

## 7. Publisher

Unchanged in every respect that matters: publication eligibility is still
`status == 'succeeded'`, delivery is still draft-only, human acceptance is
still required, automatic merge is still absent, and repository identity
validation is untouched.

Two narrow additions:

* `_behavioral_delta()` refuses to publish a record whose novelty evidence is
  missing, is not a distinguishing classification, disagrees with its own
  evidence level, or whose recorded hybrid snapshot is not the state the
  producer said it was. This only removes eligibility; it grants none.
* The preview/PR body states the classification, the evidence level and the
  honest summary, and says when a candidate replaced an earlier one that
  demonstrated no behavioral delta.

## 8. Artifacts

Under `/srv/nullbrain/jobs/workflow-<id>/`:

| Path | Contents |
| --- | --- |
| `attempt-3/behavioral-delta.json` | classification, `evidence_level`, `evidence_summary`, policy and decision, diagnostic, `distinguishing`, `semantic_replan_attempt`, `semantic_replan_outcome`, `timing_ms`, manifest, full hybrid record |
| `attempt-6/` | `diagnostic.txt`, diagnosis prompt and answer, `semantic-diagnosis.json` |
| `attempt-7/`, `attempt-8/`, `attempt-9..10/` | the re-planned candidate's planning, editing/verification and repairs, each with its own `behavioral-delta.json` |
| `semantic-replan.json` | re-plan budget, attempts used, outcome and duration |
| `timing.json` | monotonic stage timings for the whole run |

## 9. Validation

Environment: Linux x86-64, Python 3.11.15, Git 2.43.0. **No Docker daemon and
no Ollama**, so no real Gradle/JUnit container and no real inference ran; the
suite uses real Git fixtures, real checkouts, canned model answers and a canned
verifier, as every earlier milestone's suite does.

| | Tests | Duration |
| --- | --- | --- |
| Before (`80e47cb`) | 133 | 21.7 s |
| After | 171 | 35.9 s |

`python3 -m unittest discover -s tests`, `PYTHONPATH=src`, OK both times.

New coverage: `tests/test_behavioral_delta_policy.py` (the policy experiment)
and `tests/test_behavioral_delta_evidence.py` (evidence levels, the semantic
re-plan, timing, multi-file and larger-suite shapes, and the test-side logic
risk), plus publisher evidence-metadata tests in
`tests/test_repo_execute_publish.py`.

### Mutation testing

Seven deliberate mutations were applied to throwaway copies of the source to
confirm the suite catches regressions rather than describing intent. Results
are recorded in §10 below.

## 10. Mutation results

| Mutation | Effect on the suite |
| --- | --- |
| API compile failure recorded as `behavioral` evidence | 7 failures |
| `evidence_level()` always returns `behavioral` | 7 failures |
| Hybrid production revert disabled (candidate production leaks in) | 57 failures, 2 errors |
| Re-planned candidate skips the baseline regression | 1 failure |
| A second semantic re-plan allowed despite budget 1 | 6 failures |
| Re-planned candidate reuses the previous candidate's verification | 16 failures, 1 error |
| `no-behavioral-delta` verdict suppressed (classified as a test failure) | 24 failures |

Every mutation was caught. Two are worth a note:

* The baseline-regression bypass is caught by a single test —
  `test_replanned_candidate_reruns_every_gate_on_its_own_evidence`, the test
  written for exactly that property. One failure is thin coverage, but it is
  the deliberate one rather than an accidental side effect.
* Disabling the hybrid revert fails 57 tests because the manifest check fires
  on every behavioral-delta path, which is the intended blast radius for the
  invariant that keeps candidate production out of the counterfactual.

Mutations were applied to throwaway copies of `src/` and `tests/` in a
temporary directory. Nothing in the repository history was mutated.


## 11. What remains unproven

* **No live Pi smoke test has been run from this environment**, for this
  increment or for 7B hardening before it. No real Gradle/JUnit hybrid
  container and no real inference have executed. The outstanding live runs
  are: a genuine assertion-level change, a Workflow 26-style no-delta
  reproduction (now expected to attempt one semantic re-plan first), and a
  new-API run to observe the structural-evidence path end to end.
* **Real timing is unmeasured.** Every duration recorded here measures
  orchestration against a canned verifier. No optimization decision should be
  made from them.
* **Semantic re-plan effectiveness is unmeasured.** Budget 1 is a conservative
  starting point; the telemetry exists precisely so it can be revised from
  observed rescue rates and latency instead of left permanent by inertia.
* **Test-side logic** remains a documented limitation, not a mitigated risk.
* Repository shapes beyond the Gradle/JUnit lab profile — other build systems,
  larger files, more than three changed files — remain unproven. One passing
  fixture is not proof for all repository shapes.
