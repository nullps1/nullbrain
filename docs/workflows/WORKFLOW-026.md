# Workflow 26

A live `repo-execute-v1` run that cleared every gate that existed at the
time, up to and including local commit creation, without changing any
production behavior. The pinned base already did what the task claimed to
add. It is the live evidence behind the deterministic behavioral-delta
counterfactual ([7B hardening](../milestones/MILESTONE-7B-HARDENING.md)),
which was later refined by [7B.1](../milestones/MILESTONE-7B-1.md).

Sources: [MILESTONE-7B-HARDENING.md](../milestones/MILESTONE-7B-HARDENING.md)
("The defect (Workflow 26)") and the commit message of `a48d419`.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 26 |
| Profile | `repo-execute-v1` |
| Date | not recorded in repository documentation (before `a48d419`, 2026-09-21) |
| Environment | live run; host details not recorded in repository documentation |
| Target repository | a Gradle/JUnit fixture containing `Slugs` (name not recorded in repository documentation) |
| Base commit | `6749c9b` (pinned base) |
| Candidate commit | `4ed2d63` |
| Task | not recorded verbatim in repository documentation |
| Inference job IDs | not recorded in repository documentation |

## Selected scope

The `Slugs` production class and its test. Exact paths are not recorded in
repository documentation.

## Execution path

The individual state sequence is not recorded. Every gate that existed at the
time passed, in order: candidate compile, candidate tests, the minimum
executed-test floor, the original baseline regression, the added-test-count
comparison, targeted review and local commit creation.

## Model decisions

The model claimed it would "add support for trimming leading and trailing
hyphens in slugified strings". The pinned base already contained:

```java
return normalized.replaceAll("^-*|-*$", "");
```

The candidate production file was behaviorally identical to the base and had
only lost its indentation. The model added this test:

```java
@Test
void leadingTrailingHyphens() {
    assertEquals("hello-world", Slugs.slugify("--Hello-World--"));
}
```

That test passes against the pinned base.

## Verification results

| Check | Result |
| --- | --- |
| Candidate compile | passed |
| Candidate tests | passed |
| Minimum executed-test floor | passed |
| Original baseline regression | passed |
| Added-test-count comparison | passed |
| Targeted review | passed |
| Behavioral-delta check | did not exist at the time |

Exact test counts are not recorded in repository documentation.

## Final outcome

The workflow **cleared every gate and created a local commit** (candidate
`4ed2d63`), although the change was semantically empty. Whether it was
published is not recorded in repository documentation. No merge is recorded.

The `rejected-no-behavioral-delta` terminal state did not exist when this
workflow ran. It was introduced afterward, in response to it.

## Safety behavior observed

- Every existing gate behaved as designed. The defect was a **missing** gate,
  not a failing one.
- Publication remained explicit and human-initiated, so the empty success did
  not publish itself.

## Artifacts

Not recorded in repository documentation. By the runtime convention the run's
artifacts live under `jobs/workflow-26/` on the host, outside Git.
`behavioral-delta.json` did not exist for this run.

## What we learned

The pipeline proved that candidate tests pass. It never proved that candidate
tests distinguish candidate production from pinned-base production. Model
intent is not evidence: valid JSON, a coherent plan, compiling code and a
green suite are necessary but not sufficient.

Later work (`a48d419`, after this run) introduced:

- a **behavioral-delta counterfactual** stage between the added-coverage
  comparison and targeted review. It runs the candidate suite against a hybrid
  workspace of pinned-base production plus only the approved candidate test
  sources, pinned to the verifier's own snapshot hashes;
- the terminal state `rejected-no-behavioral-delta`, distinct from `failed`,
  so Workflow 26-style outcomes are independently queryable;
- `behavioral-delta.json` and `behavioral-delta-verification/` artifacts;
- a Workflow 26 regression fixture in
  `tests/test_repo_execute_workflow.BehavioralDeltaTests`.

Later still, [7B.1](../milestones/MILESTONE-7B-1.md) (`3c7e724`) refined the
evidence semantics. It separates `behavioral` from `structural` evidence
through `evidence_level`, and gives a no-delta candidate exactly one bounded
semantic re-plan before that terminal state. Its fixture D1 is a Workflow
26-style no-delta case.

## Follow-up

- [7B hardening](../milestones/MILESTONE-7B-HARDENING.md): the gate built from
  this evidence.
- [7B.1](../milestones/MILESTONE-7B-1.md): evidence levels and the bounded
  semantic re-plan. Its introduction records live Workflows 27 (correctly
  rejected) and 28 (correctly accepted) under the new gate. Those runs have no
  dedicated records.
