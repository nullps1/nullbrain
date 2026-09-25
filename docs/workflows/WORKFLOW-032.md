# Workflow 32

A live `repo-execute-v1` run against the Patient Zero Java lab. It **failed
safely** after the repair-selection reply blamed the test but routed the
repair to the production file. The repair came back unchanged, and the
unchanged-source guard ended the workflow. No commit, no publication. The
run showed that a free-form `reason` plus a `file` was not enough to route a
repair, and it directly motivated
[Milestone 7B.2](../milestones/MILESTONE-7B-2.md), typed repair-target
routing.

Sources: [PROPOSAL-7B-2.md](../milestones/PROPOSAL-7B-2.md) §2,
[MILESTONE-7B-2.md](../milestones/MILESTONE-7B-2.md) §2 and
[PROJECT_STATE.md](../PROJECT_STATE.md) ("Patient Zero compatibility and
Workflow 32").

## Metadata

| Field | Value |
| --- | --- |
| Workflow ID | 32 |
| Profile | `repo-execute-v1` |
| Date | not recorded in repository documentation (after PR #7 merged as `c707140`; recorded in PR #8) |
| Environment | Raspberry Pi Nullbrain worker, live Ollama inference, Docker/Gradle verification |
| Target repository | Patient Zero Java lab (`nullcode-java-lab`) |
| Base branch / commit | not recorded in repository documentation |
| Nullbrain revision | `main` after PR #7 (`c707140`), before 7B.2 |
| Task | "Add a small tested behavior improvement consistent with the existing project API." |
| Inference job IDs | selection **117**, plan **118**, production edit **119**, test edit **120**, repair attempt **122** (the repair-selection job ID is not recorded) |

## Selected scope

- Production: `src/main/java/lab/TextStats.java`
- Test: `src/test/java/lab/TextStatsTest.java`

No repair narrowing applied: this was an ordinary JUnit failure, so both
selected files were offered as repair candidates.

## Execution path

```
inspecting
→ planning
→ editing
→ compiling
→ testing
→ diagnosing-repair-1
→ repairing-1
→ failed
```

## Model decisions

**Plan** (succeeded): "Add a method to count punctuation marks in the text."

**Production edit.** `TextStats.countPunctuation(String)` counted characters
found in:

```
.,!?;:'"()[]{}<>/\-_+=&*^%$#@~`
```

**Test edit.** Three consistent assertions:

```java
assertEquals(3, TextStats.countPunctuation("!!!"));
assertEquals(1, TextStats.countPunctuation("one!two"));
assertEquals(0, TextStats.countPunctuation("onetwo"));
```

and one inconsistent one, which expected **4** for an input containing all
**31** configured punctuation characters:

```java
assertEquals(
    4,
    TextStats.countPunctuation(
        ".,!?;:'\"()[]{}<>/\\-_+=&*^%$#@~`"
    )
);
```

**Repair selection.** The reply contradicted itself:

```json
{
  "file": "src/main/java/lab/TextStats.java",
  "reason": "The test expectation is incorrect. The method should count all punctuation marks, not just a subset."
}
```

The reason blamed the **test**, but the target was the **production** file.
Before 7B.2, `validate_repair_selection(...)` checked only JSON shape,
approved scope and a non-empty reason, so it accepted the reply.

**Repair 1** (inference job 122) was asked to fix `TextStats.java`, which the
model's own diagnosis had just cleared. It returned the file unchanged.

## Verification results

| Check | Result |
| --- | --- |
| Candidate compile | passed |
| Candidate tests | **failed**: `JUnit: countPunctuation(): expected: <4> but was: <31>` |
| Repair 1 | returned unchanged source; not re-verified |

The production code was right and the test expectation was wrong: the input
held 31 punctuation characters. Total test and failure counts are not
recorded in repository documentation.

## Final outcome

**Failed safely**, with:

```
Repair 1 returned unchanged source
```

No commit, no publication, no merge. The second repair allowed by the budget
was not reached: an unchanged-source repair is terminal.

## Safety behavior observed

- **Verification** caught the bad expectation.
- **Scope preservation.** The repair target was one of the two selected
  files, and scope was not widened.
- **Unchanged-source rejection** ended the workflow instead of re-verifying a
  no-op or spending further repairs.
- **Fail-closed / no publication.** Nothing was committed or published.

Every existing gate held. What was lost was the only repair that could have
succeeded, a one-line test fix, which was spent on the wrong file.

## Artifacts

Specific paths are not recorded in repository documentation. By the runtime
convention the run's artifacts live under `jobs/workflow-32/` on the host,
outside Git. `repair-routing.json` did not exist for this run; it was
introduced by 7B.2.

## What we learned

A free-form `reason` plus a `file` is not enough to route a repair. The
diagnosis lived only in prose the validator never read, so nothing
deterministic could notice that the diagnosis and the target disagreed. The
contradiction surfaced one model call later, and only indirectly, as an
unchanged file.

Workflow 32 is one observation. It shows that the gap exists; it does not
measure how often it happens.

Later work ([7B.2](../milestones/MILESTONE-7B-2.md), `3cd3ed1`, after this
run) introduced a typed `fault_domain` (`production` | `test`) that must agree
with the routed file. A contradictory reply now fails closed at routing,
before any repair-edit prompt, with an accurate message. No auto-correction,
no reselection. Workflow 32 and its mirror are encoded as regression tests in
`tests/test_repair_routing.py`. 7B.2 checks that the domain and the file
**agree**. It cannot check that they are **right**: a consistently wrong
reply is still accepted.

## Follow-up

- [PROPOSAL-7B-2.md](../milestones/PROPOSAL-7B-2.md): the design written from
  this run.
- [MILESTONE-7B-2.md](../milestones/MILESTONE-7B-2.md): the implementation.
- [Workflow 33](WORKFLOW-033.md): the first live run of the same task under
  7B.2.
