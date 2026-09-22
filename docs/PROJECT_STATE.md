# NullCode project state

**Updated:** 2026-09-21, after Milestone 7B.1 merged to `main`.
**Current main:** `84f0dc1` (merge of Milestone 7B.1).
**Key implementation commits:** `34fc203` (7C-2 publisher), `53e09bb` / `6c78080`
(7C-2 hardening/docs), `88c015b` (insufficient-test-count repair), `a48d419`
(behavioral-delta gate), and `3c7e724` (7B.1 evidence hardening + semantic
re-plan). Development branches are deleted after merge; commits are the durable
references.

This is the authoritative handoff document. Read it before changing anything.
The current update below supersedes the retained refactor-era snapshot in
sections 1–9. Historical test counts, branch/history statements, and next-step
proposals in those sections are not current. Section 10's constraints remain.

## Current update: 7B.1 — behavioral-delta evidence hardening

The behavioral-delta gate treated two unequal kinds of evidence as
interchangeable and made a no-delta verdict terminal on the first attempt.
`repo-execute-v1` now records an explicit `evidence_level` with every
classification — `behavioral` when the candidate suite compiled against
pinned-base production and failed there, `structural` when it could not be
compiled against the base at all — and carries that level into the artifact,
the workflow result and the draft-PR body. Structural evidence states plainly
that it does not prove runtime behavioral novelty.

The API compile-failure policy was chosen from a recorded experiment, not from
intuition: a legitimate new API and a deliberately trivial one produce
identical hybrid evidence, because a test-compilation failure aborts the suite
before any assertion runs. Rejecting structural evidence would therefore
reject every legitimate new-API task. Policy A (allow, labelled `structural`)
is selected; Policy B has no robust implementation without a Java/JUnit source
parser, which stays out of scope. `policy_decision()` is shared by the
workflow and the experiment.

A no-delta candidate now gets exactly one **semantic re-plan**, budgeted
separately from the unchanged two-attempt repair loop. The superseded
candidate is reset to pinned-base content and the replacement re-runs every
gate from planning onwards on its own evidence; nothing of the superseded
candidate's verification is reused. A repeated no-delta still terminates on
`rejected-no-behavioral-delta`. The diagnostic prompt is advisory and is
documented as such in both code and prose — enforcement stays deterministic.

Semantic re-plan telemetry and monotonic stage timings are persisted for every
terminal state so budget 1 and the counterfactual's cost can be revisited from
observed data. **No performance threshold was introduced**: the durations
available here measure orchestration only, not Gradle.

Validation was broadened to multi-file hybrids (2+1, 1+2 and 2+2), a 48-case
eight-file lab, legitimate and trivial new-API fixtures, and adversarial
test-side logic fixtures. The test-side logic risk is **not** mitigated: the
counterfactual proves an observed difference is caused by production content,
but cannot show an assertion is meaningful, and the deterministic review does
not read test helpers. That is recorded as a known limitation with evidence,
alongside a new advisory, non-gating diff statistic. No blanket prohibition on
test helpers was added.

No existing gate was weakened: the minimum-test floor, baseline regression,
added-coverage comparison, editable scope, per-candidate repair budget, hybrid
snapshot integrity, fail-closed infrastructure handling, draft-only
publication and human acceptance are all unchanged. The publisher additionally
refuses records whose novelty evidence is missing or inconsistent.

The suite is **171 tests**, up from 133. The implementation was validated on
Linux with canned inference/container verification, and then exercised on the
Raspberry Pi after merge with **171/171 tests passing**. Live Pi runs verified
all three critical novelty paths: bounded no-delta semantic re-plan/exhaustion,
behavioral evidence, and structural API evidence. Hybrid snapshot integrity and
the existing repair, scope, regression, publishing, and human-acceptance
boundaries remained intact. See [7B.1](milestones/MILESTONE-7B-1.md).

## Previous update: 7B hardening — behavioral-novelty validation

`repo-execute-v1` could reach `succeeded` without the candidate changing any
production behavior: every gate passed on a cosmetic production rewrite plus a
test the pinned base already satisfied (Workflow 26). A behavioral-delta
counterfactual stage now runs between the added-coverage comparison and
targeted review. It builds a hybrid workspace — pinned-base production with
only the approved candidate test sources overlaid, pinned to the verifier's
own snapshot hashes — and runs the candidate suite against it. A suite that
fully passes there is rejected on a new terminal state,
`rejected-no-behavioral-delta`; a test failure, or a test-compilation failure
caused by API absent from the base, is the distinguishing evidence that lets
the workflow continue; anything else fails closed as infrastructure failure.

No existing gate changed: the minimum-test floor, the baseline regression, the
editable scope, the repair budget and 7C-2 publishing are all untouched.
Publication is already gated on `status == 'succeeded'`, so nothing is
publishable from a rejected workflow. See
[7B hardening](milestones/MILESTONE-7B-HARDENING.md).

The suite is **133 tests**, up from 120, and passes on Linux (x86-64, Python
3.11.15, Git 2.43). Three mutations of the new gate were applied to throwaway
copies to confirm the tests fail without it. **The live smoke test has not
been run**: this environment has no Docker daemon and no Ollama, so no real
Gradle/JUnit hybrid container and no real inference has executed. Both the
genuine-change run and the Workflow 26 reproduction are still outstanding on
the Pi.

## Earlier update: 7C-2

Successful `repo-execute-v1` workflows can now use the existing explicit
publisher CLI for a local preview and optional draft PR. Profile-specific
validation checks final-result identity across repair attempts, candidate and
original-test snapshots, exact committed scope, protected files, per-file
review hashes, and an increase in executed test cases. Shared delivery behavior
is unchanged. See [Milestone 7C-2](milestones/MILESTONE-7C-2.md).

7C-1 scope proposals remain unimplemented; a design proposal for them is now
written up in [Proposal 7C-1](milestones/PROPOSAL-7C-1.md), which is a document
to argue with, not an approved design. This change does not deploy anything
to the Pi, publish automatically, or remove human scope/acceptance decisions.
The publisher still targets remote `main` with an exact base-commit match.

Two follow-up hardening changes were made after a Linux review of the commit.
The publisher now re-derives the approved `editable_test_files` scope with the
same rules the 7B producer applies, instead of trusting the committed field
that the shared Gradle `inspect()` does not validate. It also folds a lone CR
when reading committed text, matching the universal-newline translation the
producer's own review hashes were computed under.

Validation for this update is recorded in the milestone document. The baseline
at `4013fbd` passed all 95 Python tests on Windows; the complete suite with
7C-2 passed **103 tests** in 245.755 seconds there. With the hardening changes
the suite passes **105 tests** in 17.805 seconds on Linux (x86-64,
Python 3.11.15, Git 2.43). Publisher CLI help, Python compilation, and
`git diff --check` passed in both environments.

Live Ollama, Docker/Gradle, GitHub delivery, and Pi memory/temperature remain
unexercised: the Linux environment has no Ollama installed, so no workflow has
run against a live model. Rust is unchanged and untested in both sessions.
Pi validation is still outstanding.

---

## 1. What is known-good today

The table below is the retained **refactor-era baseline**. It is useful for
historical comparison but is no longer the current validation summary. Current
validation is recorded in the update sections above and in the milestone docs.

| Check | Result |
| --- | --- |
| Python suite (`python3 -m unittest discover -s tests`) | **93 passed**, 0 failed |
| Python suite under `pytest` | **93 passed** (subtests included) |
| Rust suite (`cargo test` in `rust/`) | **3 passed** |
| `cargo check` | clean |
| `docker compose -f compose/nullcode.compose.yml config` | valid; build context resolves to `rust/` |
| `docker compose -f compose/ollama.compose.yml config` | valid |
| All 14 package modules import | yes |
| All 7 CLI entry points respond to `--help` via `-m` | yes |
| `create_fixture` / `create_gradle_fixture` produce working repos | yes |
| `gradle_workflow.inspect()` accepts a freshly generated fixture | yes (template comparison passes from the new path) |
| Wheel build includes `gradle_profile/` and `acceptance/` package data | yes |

The test counts are identical before and after the reorganization.

## 2. Capabilities that work

- Local inference through a Rust/Axum controller in front of Ollama, with a
  persistent SQLite job queue and restart recovery.
- A single serialized Python workflow worker with its own SQLite workflow store,
  `flock`-based single-instance guarantee, and leftover-container cleanup.
- Java generation → sandboxed compile → sandboxed test → bounded repair.
- Deterministic review rules that block a commit when they find a finding.
- Local-repository workflows on isolated clones with `origin` removed, task
  branches, saved diffs and review evidence, and local-only commits.
- Offline Gradle/JUnit verification against approved build templates, with JUnit
  XML parsed for a minimum non-skipped passing test count.
- Read-only repository planning (`repo-plan-v1`) producing a validated JSON plan.
- Planned, bounded multi-file execution (`repo-execute-v1`), including
  differential behavioral-novelty validation: the candidate test suite must
  not fully pass against pinned-base production.
- Acceptance-gated production edits against committed, human-reviewed tests
  (`accepted-java-v1`), including hash-based tamper detection.
- Explicit, human-initiated GitHub **draft** PR delivery with a preview mode that
  makes no network calls.

## 3. Current modules

```
src/nullcode/
  core/      java_workflow.py  validate_java.py  review_java.py
  repo/      repo_workflow.py  repo_plan_workflow.py
             repo_execute_workflow.py  accepted_workflow.py
  gradle/    gradle_workflow.py  gradle_profile/
  publish/   publish_workflow.py  check_acceptance.py  prepare_acceptance.py
  fixtures/  create_fixture.py  create_gradle_fixture.py
  acceptance/  contract.json  TextStatsAcceptanceTest.java   (package data)
rust/        Cargo.toml  src/main.rs  Dockerfile  README.md
tests/       11 test modules, 171 unittest cases
scripts/     smoke.py
```

Dependency direction is documented and verified in
[`ARCHITECTURE.md` §2](ARCHITECTURE.md#2-python-package-layering).

## 4. Runtime and host assumptions

- Host `nullbrain`, a Raspberry Pi running Linux, service user `null` (1001:1001)
  in the `docker` group.
- Repository deployed at `/srv/nullbrain` (was `/srv/nullbrain/src/nullcode`).
- Controller on `127.0.0.1:8080`; Ollama on `127.0.0.1:11434`; no LAN exposure
  and no authentication — this is a localhost-only system.
- Workflow DB: `/srv/nullbrain/data/nullcode-workflows/workflows.sqlite3`
  (`NULLCODE_WORKFLOW_DIR`).
- Job artifacts: `/srv/nullbrain/jobs/workflow-<id>/` (`NULLCODE_JOBS_DIR`).
- Controller DB: `/srv/nullbrain/data/nullcode` bind-mounted to `/data`.
- Fixture repositories on the Pi live under `/srv/nullbrain/repos/`.
- Images required locally: `eclipse-temurin:21-jdk` and
  `nullcode-gradle:8.14.3-jdk21`.
- Python 3.11+, standard library only. No third-party Python dependencies.

### Deployment note from the repository reorganization

The commands below are retained as the migration procedure from the old flat
layout. The current repository does not prove whether an individual host still
needs this step; verify the installed unit before applying it:

```sh
sudo systemctl stop nullcode-worker
sudo install -m 0644 /srv/nullbrain/deploy/nullcode-worker.service \
    /etc/systemd/system/nullcode-worker.service
sudo systemctl daemon-reload
sudo systemctl start nullcode-worker
systemctl status nullcode-worker
```

The unit now uses `WorkingDirectory=/srv/nullbrain`,
`Environment=PYTHONPATH=/srv/nullbrain/src` and
`ExecStart=/usr/bin/python3 -u -m nullcode.core.java_workflow run`.
Compose invocations also moved: the files are `compose/nullcode.compose.yml`
and `compose/ollama.compose.yml`, and the controller build context is `rust/`.

## 5. Completed milestones

| # | Title | Doc |
| --- | --- | --- |
| 1 | Local inference queue (Rust/Axum + SQLite + Ollama) | [`rust/README.md`](../rust/README.md) |
| 2 | Automated Java verification and one repair | [`milestones/MILESTONE-2.md`](milestones/MILESTONE-2.md) |
| 3 | Repository-backed Java jobs | [`milestones/MILESTONE-3.md`](milestones/MILESTONE-3.md) |
| 4 | Targeted review before committing | [`milestones/MILESTONE-4.md`](milestones/MILESTONE-4.md) |
| 5 | Draft pull-request delivery | [`milestones/MILESTONE-5.md`](milestones/MILESTONE-5.md) |
| 6 | Configurable small Gradle/JUnit tasks | [`milestones/MILESTONE-6.md`](milestones/MILESTONE-6.md) |
| 7A | Read-only repository inspection and planning | [`milestones/MILESTONE-7A.md`](milestones/MILESTONE-7A.md) |
| 7B | Planned, bounded multi-file execution | [`milestones/MILESTONE-7B.md`](milestones/MILESTONE-7B.md) |
| 7B hardening | Behavioral-novelty validation | [`milestones/MILESTONE-7B-HARDENING.md`](milestones/MILESTONE-7B-HARDENING.md) |
| 7B.1 | Behavioral-delta evidence hardening | [`milestones/MILESTONE-7B-1.md`](milestones/MILESTONE-7B-1.md) |
| 7C-2 | Multi-file draft PR publishing | [`milestones/MILESTONE-7C-2.md`](milestones/MILESTONE-7C-2.md) |
| — | Acceptance-gated production edits (`accepted-java-v1`) | *no document — see `accepted_workflow.py`* |
| — | `javac`-driven repair rule (`javac-string-array-stream-loop-v1`) | *no document — see `compiler_repair_rule`* |

## 6. Known limitations and unresolved issues

1. **The acceptance workflow and the compiler repair rule still have no
   milestone document.** 7A and 7B are now documented in
   `docs/milestones/`, each backed by a dedicated test file
   (`test_repo_plan_workflow.py`, `test_repo_execute_workflow.py`) that
   exercises their orchestration with fake inference — not just a source
   reading. The acceptance workflow (`accepted-java-v1`) and the
   `javac-string-array-stream-loop-v1` repair rule remain undocumented,
   though both do have real test coverage via `test_accepted_workflow.py`.
2. **Early pre-Git states are not represented by commits.** The repository now
   has normal Git history from the initial backup forward, but the two retained
   checkpoint directories still represent states from before that history. See
   [§7](#7-checkpoint-snapshots).
3. **`Cargo.lock` is not tracked.** The root `.gitignore` `*.lock` pattern
   (intended for lock files generally) also matches `Cargo.lock`, while
   `rust/README.md` says the resolved lock file should be preserved. This is a
   pre-existing contradiction; it was **not** changed in the refactor.
4. **Most automated tests still simulate inference and Docker.** The Python
   suite uses real Git and SQLite but canned inference/container verification.
   Milestone 7B.1 has additionally been exercised live on the Pi, including the
   no-delta re-plan path and both novelty evidence paths; that live validation
   does not make every profile or failure mode end-to-end proven.
5. **Review rules are narrow.** Three text-based checks for the Numbers profile.
   Not a parser, not a static analyser, not a security review.
6. **Single worker, single job.** No concurrency; interrupted workflows become
   `interrupted` on restart and must be resubmitted.
7. **No authentication anywhere.** Localhost-only by design.
8. **Java only.** Gradle only; no Maven, no dependency resolution inside job
   containers.
9. **Structural novelty is not behavioral novelty.** A candidate whose tests
   cannot be compiled against pinned-base production is admitted on
   `evidence_level = structural`: the API surface demonstrably differs, but no
   assertion ran against the base, so a semantically empty new API can reach a
   draft PR. The policy and the fixture evidence behind it are in
   [7B.1](milestones/MILESTONE-7B-1.md) §2; the level is recorded everywhere
   so such admits are countable.
10. **Test-side logic is not mitigated.** Candidate test sources may contain
   helpers that reimplement production logic, making an assertion
   tautological. The counterfactual proves an observed difference is caused by
   production content — candidate tests are byte-identical in both worlds —
   but it cannot prove the assertion is meaningful, and the targeted review
   rules do not read test helpers. Human acceptance remains the control. See
   [7B.1](milestones/MILESTONE-7B-1.md) §6.
11. **Stage timing evidence is still too sparse for optimization policy.**
   `timing.json` is written for every workflow and real Pi workflow timings now
   exist, but the sample is too small and task-specific to justify a threshold
   or optimization target.

## 7. Checkpoint snapshots

`checkpoints/` holds two pre-install rollback snapshots that were copied into
the working tree before this repository had useful Git history:

| Directory | Represents |
| --- | --- |
| `accepted-backup-81e837678814497aa9384c59dde2df54/` | `java_workflow.py` as it was **before** the `accepted-java-v1` workflow was installed, plus `installed-files.json` listing that increment's files. |
| `stream-rule-backup-75cee6743c604cdf82d16cbbf9da5c4d/` | `accepted_workflow.py` and `test_accepted_workflow.py` as they were **before** the `javac-string-array-stream-loop-v1` repair rule was added. |

**They were moved out of the source tree but deliberately not deleted, and no
Git tags were created for those pre-install states.** The repository now has
normal history after the initial backup, but no commit represents either
checkpoint state; tagging a modern `HEAD` would still point at the wrong
content.
Deleting them would destroy the only record of those states.

They can be retired once the corresponding states are verifiably reachable from
Git — for example by importing the original per-milestone archives as commits,
or by accepting that the pre-install states are no longer needed. Until then,
leave them in place. `checkpoints/README.md` repeats this.

## 8. Verifying this state

```sh
# Python (no install needed)
PYTHONPATH=src python3 -m unittest discover -s tests   # expect: Ran 171 tests ... OK
pytest -q                                              # expect: 171 passed

# Rust controller
cd rust && cargo test && cargo check                   # expect: 3 passed

# Compose
docker compose -f compose/nullcode.compose.yml config --quiet
docker compose -f compose/ollama.compose.yml config --quiet

# Imports and entry points
PYTHONPATH=src python3 -m nullcode.core.java_workflow --help
PYTHONPATH=src python3 -m nullcode.publish.publish_workflow --help

# On the Pi only: real containers and inference
docker build -t nullcode-gradle:8.14.3-jdk21 src/nullcode/gradle/gradle_profile
PYTHONPATH=src python3 -m nullcode.fixtures.create_gradle_fixture /srv/nullbrain/repos/nullcode-java-lab
PYTHONPATH=src python3 -m nullcode.core.java_workflow submit
PYTHONPATH=src python3 -m nullcode.core.java_workflow wait <id>
```

## 9. Active direction and next milestone

**Active direction:** move from human-preselected edit scope toward controlled
task autonomy without weakening the existing deterministic gates.

**Next intended milestone: 7C-1 — model-proposed scope, human-granted scope.**
The current design lives in
[`milestones/PROPOSAL-7C-1.md`](milestones/PROPOSAL-7C-1.md) and is still a
proposal, not implemented behavior. The invariant is unchanged: a model may
propose a scope, but only a human can grant it by changing committed policy.

After 7C-1, the next planned autonomy step is **GitHub issue/task ingestion**:
consume a real issue as task input, run the same bounded planning/execution and
verification path, and remain draft-PR-only with human review/merge. That work
is not implemented yet.

Documentation debt remains worth closing in parallel: the acceptance workflow
and the `javac-string-array-stream-loop-v1` repair rule still lack dedicated
milestone documents, and the pre-Git checkpoints remain retained.

## 10. Constraints for anyone continuing this work

Full list in [`ARCHITECTURE.md` §8](ARCHITECTURE.md#8-boundaries-that-must-not-be-violated).
The short version:

- Do not weaken review or acceptance gates, or the conditions for "passed".
- Do not increase attempt budgets or let infrastructure errors count as repairs.
- Do not give the HTTP controller the Docker socket.
- Do not make publishing automatic, non-draft, or capable of merging.
- Do not set `--acceptance-reviewed` programmatically.
- Do not move runtime/generated state into the package or into Git.
- Do not hoist `core.java_workflow`'s lazy profile imports to module scope.
- Develop in small, verified milestones; run the suites before and after.
