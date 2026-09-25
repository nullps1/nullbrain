# Milestone 7B hardening: behavioral-novelty validation

> **Extended by [7B.1](MILESTONE-7B-1.md).** The counterfactual, the hybrid
> construction and the terminal state below are unchanged, but the two
> distinguishing classifications are no longer recorded as equal-quality
> evidence (`evidence_level`), a no-delta candidate now gets one bounded
> semantic re-plan before that terminal state, and stage timing and re-plan
> telemetry are persisted. The test count at the bottom of this document is
> historical.

`repo-execute-v1` could reach `succeeded` without the candidate introducing
any real production behavior change. Every gate in
[Milestone 7B](MILESTONE-7B.md) passed on a task that rewrote production code
cosmetically and added a test for behavior the pinned base already supported.

This change adds a **behavioral-delta counterfactual** stage that supplies the
missing evidence, and a distinct terminal state for candidates that fail it.

## The defect (Workflow 26)

The full run record is [Workflow 26](../workflows/WORKFLOW-026.md).

The model claimed it would "add support for trimming leading and trailing
hyphens in slugified strings". The pinned base (`6749c9b`) already contained:

```java
return normalized.replaceAll("^-*|-*$", "");
```

The candidate (`4ed2d63`) returned a file that was behaviorally identical to
the base and had only lost its indentation, plus this test:

```java
@Test
void leadingTrailingHyphens() {
    assertEquals("hello-world", Slugs.slugify("--Hello-World--"));
}
```

That test passes against the pinned base. The workflow still cleared candidate
compilation, candidate tests, the minimum executed-test floor, the original
baseline regression, the added-test-count comparison, targeted review, and
local commit creation.

The pipeline validated *candidate tests pass*. It never validated *candidate
tests distinguish candidate production from pinned-base production*. Model
intent is not evidence; valid JSON, a coherent plan, compiling code and a
green suite are necessary but not sufficient.

## The invariant

```
candidate production + candidate tests = PASS       (already established)
pinned-base production + candidate tests = must NOT fully PASS
```

If the exact candidate suite passes in both worlds, nothing shows the
production edit was needed at all.

## Where it runs

The stage sits inside `repo-execute-v1`, not the publisher: a semantically
empty task must not reach `succeeded` or create a local commit in the first
place. Pipeline order is now:

1. Candidate compile
2. Candidate tests
3. Minimum executed-test floor
4. Original baseline regression
5. Added-test / added-coverage comparison
6. **Hybrid counterfactual construction, run and classification** (new)
7. Targeted review
8. Commit
9. Publish preview / publish (unchanged, still explicit and draft-only)

No existing gate was weakened, replaced, reordered or bypassed.

## The hybrid counterfactual state

Constructed deterministically from the pinned base commit, with **only** the
approved candidate *test* sources overlaid:

- selected production files are written back to their base-commit content
- selected test files keep their candidate content
- every other tracked path is already at base content on the task branch

Candidate production is never present. This is enforced against the
verifier's own snapshot, not by inspection: the recorded
`hybrid_snapshot_sha256` must equal the base hashes taken before any edit,
updated with the overlaid candidate test hashes. A mismatch raises rather
than classifying.

Only the hybrid run is new work — candidate sources, the pinned base commit,
the candidate verification result, the baseline verification result and the
approved file scope are all reused.

## Classification

The candidate continues only on a *distinguishing* classification.

| Hybrid outcome | Classification | Effect |
| --- | --- | --- |
| Fully passes | `no-behavioral-delta` | **Reject**, terminal state `rejected-no-behavioral-delta` |
| Compiles, one or more tests fail | `distinguishing-test-failure` | Continue |
| Test compilation fails on API absent from base | `distinguishing-api-compile-failure` | Continue |
| Anything else | `infrastructure-failure` | Fail closed via the workflow's normal failure semantics |

`distinguishing-api-compile-failure` is decided from the compiler output
alone, never from the mere fact that the hybrid did not build. It requires
all of:

- Gradle reported `:compileTestJava FAILED` and **not** `:compileJava FAILED`
  (pinned-base production is known to compile — the baseline regression stage
  built it moments earlier)
- every javac error location sits inside a candidate test file the hybrid
  actually overlaid
- at least one error is a source-compatibility diagnostic (`cannot find
  symbol`, `cannot be applied`, `incompatible types`, …)

Anything the evidence does not positively explain — Docker startup failure,
timeout, container cleanup failure, missing or malformed JUnit evidence, a
Gradle failure with no reported JUnit failure, an unparsable log, an error in
a file the candidate never touched — is `infrastructure-failure`. A broken
hybrid environment is never novelty evidence, and is never reported as a
no-op change.

Individual newly added `@Test` methods are deliberately **not** parsed out of
the source diff. Running the whole candidate suite against base production
gives the required invariant directly, without a brittle JUnit source parser.
The optional formatting-only pre-check was not implemented: textual
normalization cannot replace the counterfactual run, since two textually
different implementations can still be behaviorally equivalent.

## Terminal state

`rejected-no-behavioral-delta` is a real terminal state, added to
`java_workflow.TERMINAL_STATUSES` so the store records `finished_at` for it.
It is therefore distinguishable in the workflow record, in `show`, in `wait`,
in `list`, and in the stored attempt (`phase` is the state, and the attempt
result carries `stage: behavioral-delta`). It is not folded into the generic
`failed` state, so Workflow 26-style outcomes are independently queryable.

Publication is already gated on `status == 'succeeded'`, so nothing is
publishable from a rejected workflow; a regression test pins that.

## Artifacts

Under `/srv/nullbrain/jobs/workflow-<id>/attempt-3/`:

- `behavioral-delta.json` — classification, diagnostic, base commit, branch,
  build image id, compile/test exit codes, JUnit summary, the complete hybrid
  verification record, and the manifest (overlaid candidate test files with
  their SHA-256, the production files pinned back to base, and both the
  expected and actual hybrid snapshot hashes)
- `behavioral-delta-verification/` — `verification.json`, `junit.xml`,
  `compile.log`, `tests.log`, and the snapshot the container was built from

The hybrid state is reproducible from these artifacts.

## Validation

`tests/test_repo_execute_workflow.BehavioralDeltaTests` covers the stage with
the same harness the rest of 7B uses (real Git fixture, real checkouts, canned
model answers, canned verifier):

- **Workflow 26 fixture** — base already implements the claimed behavior, the
  candidate is the de-indented identical file, the added test also passes
  against base. Terminates as `rejected-no-behavioral-delta`, with no commit,
  no `repository.json`, and a queryable attempt record.
- **Genuine change fixture** — base lacks the trimming, the candidate adds it,
  the candidate suite fails against base. The gate lets it through and the
  workflow commits; failure diagnostics are preserved as evidence.
- **New-API fixture** — the candidate adds `slugifyUnicode` and its test calls
  it; the hybrid cannot compile the test sources. Classified as
  `distinguishing-api-compile-failure`, not infrastructure failure.
- **Infrastructure fixtures** — docker startup failure, compile timeout, test
  timeout, container cleanup failure, missing JUnit evidence, malformed JUnit
  evidence, a failed Gradle task with no JUnit failure, and a base-production
  compile failure. All fail closed, none are reported as no-delta, none commit.
- **Hybrid construction** — the hybrid verifier sees pinned-base production
  and candidate tests (the exact inverse of the Stage 5 baseline regression),
  the candidate file is restored and committed afterwards, and a verifier
  snapshot showing candidate production in the hybrid is rejected outright.
- Unit coverage for `hybrid_overlay_files`, `javac_errors`,
  `distinguishing_compile_failure` (including production-compile failure, a
  missing `:compileTestJava FAILED`, an error outside the overlaid candidate
  tests, an unparsable log, an unrelated javac error, timeouts, non-javac exit
  codes, and an empty overlay set) and `classify_behavioral_delta`.
- `tests/test_repo_execute_publish.py` pins that a rejected workflow cannot be
  published.

Three mutations were applied to throwaway copies of the source to confirm the
tests catch regressions rather than merely describing intent: making the
classifier never return `no-behavioral-delta` (4 failures), skipping the
production revert so candidate production leaks into the hybrid (21 failures),
and treating any hybrid compile failure as distinguishing evidence
(8 failures).

Suite: **133 tests**, up from 120.

## Still outstanding

The live smoke test in this milestone's plan has **not** been run. The
validation environment has no Docker daemon and no Ollama, so no real
Gradle/JUnit hybrid container and no real inference has executed. The
following must still be done on the Pi:

1. A `repo-execute-v1` run on a task that genuinely requires a production
   behavior change, confirming the new stage appears in the progression and
   the workflow reaches `succeeded`.
2. A Workflow 26-style reproduction, confirming it terminates as
   `rejected-no-behavioral-delta` and produces no publishable success.
