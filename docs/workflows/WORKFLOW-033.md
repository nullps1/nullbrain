# Workflow 33

The first live Raspberry Pi validation of
[Milestone 7B.2](../milestones/MILESTONE-7B-2.md), typed repair-target
routing. It re-ran Workflow 32's task against the Patient Zero Java lab. Both
repair routes were typed, consistent and accepted in the `test` domain.
Re-verification caught a bad first repair, and the independent 2000-byte
controller prompt limit stopped the second repair before its edit call. The
workflow **failed safely**: no scope expansion, no commit, no publication.

Workflow 33 **did not succeed**. It validates 7B.2's live routing and safety
behavior, not a successful end-to-end repair.

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 33 |
| Profile | `repo-execute-v1` |
| Date | not recorded in repository documentation (after 7B.2 merged to `main` as `2d6c6c0`) |
| Environment | Raspberry Pi Nullbrain worker, live Ollama inference, Docker/Gradle verification |
| Target repository | Patient Zero Java lab (`nullcode-java-lab`) |
| Base branch / commit | not recorded in repository documentation |
| Nullbrain revision | not recorded in repository documentation; the run exercised 7B.2 routing (`3cd3ed1`) |
| Task | "Add a small tested behavior improvement consistent with the existing project API." |
| Inference job IDs | selection **123**, plan **124**; the initial edit phase included **126**; repair 1 completed through **128**; repair 2 routing **129** |

Inference job 125 and the per-call breakdown of the edit and repair-1 phases
are not recorded in repository documentation.

## Selected scope

- Production: `src/main/java/lab/TextStats.java`
- Test: `src/test/java/lab/TextStatsTest.java`

The same scope as [Workflow 32](WORKFLOW-032.md). Both repairs stayed within
it.

## Execution path

```
inspecting
→ planning
→ editing
→ compiling
→ testing
→ diagnosing-repair-1
→ repairing-1
→ repair-1-compiling
→ repair-1-testing
→ diagnosing-repair-2
→ failed
```

## Model decisions

The plan text and the content of the initial production edit are not recorded
in repository documentation.

**Repair 1 routing** (accepted):

```json
{
  "repair_number": 1,
  "fault_domain": "test",
  "file": "src/test/java/lab/TextStatsTest.java",
  "reason": "JUnit tests expect more tokens to be counted as words."
}
```

The domain and the file agree, so the validator accepted the route.

**Repair 1 edit.** The model rewrote the selected test file. The repaired test
contradicted itself on blank whitespace:

```java
assertEquals(0, TextStats.countWords("   "));
```

and later:

```java
assertEquals(1, TextStats.countWords("   "));
```

**Repair 2 routing** (accepted):

```json
{
  "repair_number": 2,
  "fault_domain": "test",
  "file": "src/test/java/lab/TextStatsTest.java",
  "reason": "JUnit test expectations are incorrect for empty and punctuation/numeric tokens."
}
```

**Repair 2 edit** did **not** run (see below).

## Verification results

| Stage | Tests | Failures |
| --- | --- | --- |
| Initial candidate verification | 48 | 2 |
| After repair 1 (re-verification) | 49 | 3 |
| After repair 2 | not run | — |

Re-verification correctly caught the bad repair: the contradictory
whitespace assertions cannot both pass.

**Prompt budget.** After repair 2's route was accepted, building the repair-2
edit prompt exceeded the controller limit, and the workflow failed closed
before any inference call:

```
Complete repair context needs 2270/2000 bytes; nothing truncated
```

The behavioral-delta stage was not reached.

## Final outcome

**Failed safely**, on the prompt-budget gate during repair 2.

- no scope expansion
- no commit
- no publication
- no merge

## Safety behavior observed

- **Typed routing.** Both routes carried a valid `fault_domain` that agreed
  with the file, and both were accepted. No contradiction occurred, so the
  rejection path was not exercised live.
- **Repair re-verification.** Repair 1's output was compiled and tested again,
  and the regression (49 tests, 3 failures) was detected.
- **Attempt budget.** The workflow reached repair 2 and no further; the
  two-repair budget stayed bounded.
- **Prompt limit.** A valid, accepted route did not bypass the 2000-byte
  controller prompt limit. The oversized context was rejected, not truncated.
- **Scope preservation.** Every repair targeted an originally selected file.
- **No publication.** Nothing was committed or published.

## Artifacts

Runtime paths on the Pi, for reference only. **Not committed.**

- `jobs/workflow-33/attempt-4/repair-routing.json`
- `jobs/workflow-33/attempt-4/repair-selection.json`
- `jobs/workflow-33/attempt-4/repair-answer.txt`
- `jobs/workflow-33/attempt-5/repair-routing.json`
- `jobs/workflow-33/attempt-5/repair-selection.json`

`attempt-4` is repair 1 and `attempt-5` is repair 2. There is no
`repair-answer.txt` under `attempt-5`, because the repair-2 edit call was
never made.

## What we learned

Workflow 33 proves live:

- typed repair routing works with real inference;
- test-domain routing works;
- `repair-routing.json` is persisted;
- `repair-selection.json` is persisted, for accepted routes;
- repair re-verification works;
- the two-repair budget remains bounded;
- a valid route does not bypass the 2000-byte prompt limit;
- bad repair content is caught;
- scope remains limited to the originally selected files.

Still unproven:

- **live production-domain routing.** No live run has yet routed a repair to
  `production` under 7B.2;
- a live contradictory reply being rejected at routing (the Workflow 32 shape
  is covered by regression tests, not yet by a live run);
- a successful end-to-end repair under 7B.2.

The prompt-budget failure comes from a limit that predates 7B.2 and that 7B.2
left unchanged. The milestone record (§7, §13) described the repair-edit
budget as a risk only for some 2+1 shapes, and pinned that routing grants
nothing that bypasses it. Workflow 33 shows that a 1+1 selection can exceed
it too, here at repair 2.

## Follow-up

- [MILESTONE-7B-2.md](../milestones/MILESTONE-7B-2.md) §14–15: the live
  validation plan and its status.
- [Workflow 32](WORKFLOW-032.md): the run that motivated 7B.2.
- Next validation step: a live task whose natural failure is a production
  bug, to exercise `production` routing end to end (7B.2 §14, step 3).
