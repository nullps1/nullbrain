# Workflow 35

A live `repo-execute-v1` follow-up to [Workflow 34](WORKFLOW-034.md). The task tightened the `countDigits(String)` contract to ASCII digits only and explicitly required Unicode-digit coverage. The generated candidate compiled and all 46 candidate tests passed. The original-suite baseline also passed all 46 tests. The workflow then failed closed at the added-coverage gate because the editable test file changed but the JUnit case count did not increase: **46 vs 46**.

Workflow 35 never entered repair routing, so it does **not** validate production-domain routing.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 35 |
| Profile | `repo-execute-v1` |
| Date | 2026-09-25 |
| Environment | Raspberry Pi Nullbrain worker, live Ollama inference, Docker/Gradle verification |
| Target repository | Patient Zero Java lab (`nullcode-java-lab`) |
| Task | "Add a countDigits(String) method to TextStats. It must count only ASCII digit characters '0' through '9'. Unicode digits such as Arabic-Indic digits must not be counted. Return 0 when there are no ASCII digits and throw IllegalArgumentException for null. Add tests covering ASCII digits, mixed text, no digits, null, and Unicode digits." |
| Inference job IDs | selection **135**, plan **136**, edit phase included **138** |

The exact local Nullbrain and lab commit identifiers are not recorded in the workflow output preserved here.

## Selected scope

- Production: `src/main/java/lab/TextStats.java`
- Test: `src/test/java/lab/TextStatsTest.java`

## Execution path

```
inspecting
→ planning
→ editing
→ compiling
→ testing
→ baseline-compiling
→ baseline-testing
→ failed
```

No diagnosing or repair phase occurred.

## Model decisions

The plan explicitly recognized the tightened requirement:

- add `countDigits` to `TextStats.java`;
- add tests for ASCII digits, mixed text, no digits, null, and Unicode digits;
- distinguish ASCII from Unicode digits.

The generated candidate changed both the selected production and test files.

## Verification results

### Candidate verification

- compile: passed
- JUnit: **46 tests, 0 failures, 0 skipped**
- candidate verification: passed

### Original-suite baseline regression

Nullbrain restored the original selected test file while keeping candidate production and ran the original suite:

- compile: passed
- JUnit: **46 tests, 0 failures, 0 skipped**
- baseline regression: passed

### Added-coverage gate

The editable test file changed, but the executed JUnit case count did not increase over the original suite:

```
Editable tests were changed but the JUnit case count did not increase over the original suite (46 vs 46)
```

Terminal message:

```
Editable tests were changed but no new cases were added
```

The behavioral-delta stage was not reached.

## Final outcome

**Failed safely** at the coverage-check stage.

- candidate tests were green
- original tests were green against candidate production
- changed editable tests did not increase the executed case count
- no repair routing occurred
- no commit
- no publication
- no merge

## Safety behavior observed

- **Added-coverage enforcement:** changing tests is not enough; the executed JUnit case count must increase over the original suite.
- **Baseline regression:** original tests still passed against candidate production before the coverage rejection.
- **Fail closed before later gates:** the workflow stopped at the first unmet deterministic condition; behavioral-delta review and publishing were never reached.
- **No authority expansion:** selected scope remained the same two files.
- **No publication:** the failed candidate was not commit- or publish-eligible.

## Artifacts

Runtime paths on the Pi, for reference only. **Not committed.**

- `jobs/workflow-35/attempt-1/`
- `jobs/workflow-35/attempt-2/`
- `jobs/workflow-35/attempt-3/candidate-verification/`
- `jobs/workflow-35/attempt-3/baseline-test-verification/`
- `jobs/workflow-35/attempt-3/result.json`

## What we learned

Workflow 35 did not produce the production-domain repair evidence it was intended to seek because the candidate was green at candidate verification and never needed a repair. Instead, it exercised a different deterministic safety boundary: a model cannot satisfy an "add tests" task merely by changing or replacing existing test cases while keeping the same executed-case count.

The run therefore adds useful live evidence for the coverage gate, but leaves these 7B.2 validation gaps unchanged:

- live production-domain repair routing remains unproven;
- live rejection of a contradictory typed route remains unproven;
- a successful end-to-end repaired candidate under 7B.2 remains unproven.

## Follow-up

- [Milestone 7B.2](../milestones/MILESTONE-7B-2.md): record Workflow 35 as coverage-gate evidence, not routing evidence.
- Future production-domain validation should arise from a naturally failing production implementation; do not weaken or bypass the coverage gate to force the route.
