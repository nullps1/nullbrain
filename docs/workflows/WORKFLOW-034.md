# Workflow 34

A live `repo-execute-v1` run against the Patient Zero Java lab, intended to exercise Milestone 7B.2's production-domain routing. The generated implementation of `countDigits(String)` was correct for the failing case, while the generated test expected 2 digits in `" 123 "`, which contains 3. Typed routing correctly selected the `test` domain. The repair edit never ran because the complete repair context required 2300 bytes against the unchanged 2000-byte controller limit. The workflow **failed safely**: no scope expansion, no commit, no publication.

Workflow 34 did **not** exercise production-domain routing.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 34 |
| Profile | `repo-execute-v1` |
| Date | 2026-09-25 |
| Environment | Raspberry Pi Nullbrain worker, live Ollama inference, Docker/Gradle verification |
| Target repository | Patient Zero Java lab (`nullcode-java-lab`) |
| Lab base branch / commit | `main` / `7a95d1437eb2b4b18f1ee4e6f6436fd5ff4c5bae` |
| Task | "Add a countDigits(String) method to TextStats. It must return the number of numeric digit characters 0-9 in the input, return 0 when there are no digits, and throw IllegalArgumentException for null. Add tests for those behaviors." |
| Inference job IDs | selection **130**, plan **131**, edit phase included **133**, repair routing **134** |

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
→ diagnosing-repair-1
→ failed
```

## Model decisions

The generated production method was:

```java
public static int countDigits(String text) {
    if (text == null) throw new IllegalArgumentException("null text");
    int count = 0;
    for (char c : text.toCharArray()) {
        if (Character.isDigit(c)) {
            count++;
        }
    }
    return count;
}
```

The generated test included:

```java
@Test
void countDigitsLeadingTrailing() {
    assertEquals(2, TextStats.countDigits(" 123 "));
}
```

Verification reported:

```
countDigitsLeadingTrailing(): expected: <2> but was: <3>
```

The model then returned this typed routing decision:

```json
{
  "fault_domain": "test",
  "file": "src/test/java/lab/TextStatsTest.java",
  "reason": "The test expected 2 digits but found 3 in the input string."
}
```

The route was internally consistent and accepted.

## Verification results

The failing case was a bad test expectation: `" 123 "` contains three ASCII digits, and the generated production method returned 3.

After routing was accepted, the workflow attempted to construct the repair-edit prompt and failed closed before repair inference:

```
Complete repair context needs 2300/2000 bytes; nothing truncated
```

No repair source was generated and no repair re-verification ran.

## Final outcome

**Failed safely** on the independent controller prompt-budget gate.

- no scope expansion
- no commit
- no publication
- no merge
- production-domain routing was not exercised

## Safety behavior observed

- **Typed routing:** a real live failure was classified as `test` and routed to the selected test file.
- **Evidence persistence:** `repair-routing.json` and `repair-selection.json` were written before the later prompt-budget failure.
- **Prompt limit:** an accepted route did not bypass the 2000-byte controller limit; the oversized context was rejected rather than truncated.
- **Scope preservation:** the route stayed inside the originally selected files.
- **No publication:** failure left nothing publishable.

## Artifacts

Runtime paths on the Pi, for reference only. **Not committed.**

- `jobs/workflow-34/attempt-4/repair-routing.json`
- `jobs/workflow-34/attempt-4/repair-selection.json`
- `jobs/workflow-34/attempt-4/diagnostic.txt`
- `jobs/workflow-34/attempt-3/1-TextStats.java.answer.txt`
- `jobs/workflow-34/attempt-3/2-TextStatsTest.java.answer.txt`

## What we learned

Workflow 34 independently confirms live test-domain routing under 7B.2. It also shows that the complete repair-edit context can exceed the 2000-byte limit even for a 1-production + 1-test selection.

The generated implementation used `Character.isDigit(c)`, which accepts Unicode digit characters in addition to ASCII `0` through `9`. The task text named `0-9`, but the generated tests did not expose that distinction. That observation motivated Workflow 35's more explicit ASCII-only specification.

This latent specification mismatch was found by human review of the generated artifact; Workflow 34 itself failed earlier on the incorrect `" 123 "` expectation and therefore did not diagnose or repair it.

## Follow-up

- [Workflow 35](WORKFLOW-035.md): explicitly requires ASCII-only digits and a Unicode-digit test.
- [Milestone 7B.2](../milestones/MILESTONE-7B-2.md): live validation status.
