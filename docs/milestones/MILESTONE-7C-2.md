# Milestone 7C-2: multi-file draft PR publishing

The publisher now accepts successful `repo-execute-v1` workflows. Execution
still ends in a local task commit; publishing remains an explicit CLI action.
This milestone does not implement 7C-1 scope proposals or consume a saved 7A
plan during execution.

## Usage

From an installed checkout, preview a completed workflow:

```sh
python3 -m nullcode.publish.publish_workflow WORKFLOW_ID --repo OWNER/REPOSITORY
```

Replace both placeholders with the intended workflow ID and GitHub repository.
The preview validates local evidence and makes no GitHub requests. After
reviewing it, the same command with `--publish` pushes the task branch and
creates a draft PR. The remote `main` must exactly match the workflow's pinned
base commit. Other remote base branches are not supported by this publisher.

## Evidence validation

`publish_workflow.prepare()` dispatches this profile to
`publish/repo_execute_publish.py`. The profile-specific validator:

- Reads root `repository.json`, then requires exactly one passing final
  workflow result whose repository metadata matches it. It does not select
  the greatest attempt number: successful 7B results remain in attempt 3
  even when attempts 4 or 5 hold repair records.
- Requires an unchanged, clean checkout and exactly one task commit with the
  recorded base as its sole parent.
- Re-inspects both committed trees against the approved Gradle profile.
  The diff must exactly match the two or three selected, pre-approved Java
  files and include production and test files. Protected content must match
  the base; tracked paths and configuration cannot change.
- Checks candidate and original-test regression compile, test, and cleanup
  results, pinned image IDs, and complete snapshot hashes. The regression
  snapshot must contain the candidate production code with selected tests
  restored from the original base.
- Requires passing JUnit evidence above the configured minimum for both
  runs, and a strict increase in **executed** cases (`tests - skipped`).
  Skipped cases cannot satisfy the publisher's added-case gate. This is
  stricter than 7B's raw case-count comparison; test count still does not
  establish meaningful feature coverage.
- Matches `plan.json` with the final stored plan. It checks each selected
  file's source hash and the full diff hash against its passing targeted
  review, and reruns the current targeted rules.
- Re-derives the approved test scope itself. The shared Gradle `inspect()`
  validates `editable_files` only, so the publisher applies the same
  `editable_test_files` rules the 7B producer's `prepare_spec` applies: 1 to 8
  unique committed `src/test/java/*.java` paths. Trusting the committed field
  would leave the publisher's approved set weaker than the producer's.

Committed text is read through `_text()`, which folds a lone CR as well as
CRLF. The producer hashed sources it read through Python text mode, which
applies full universal-newline translation, so reading committed bytes with
`git show` alone made a stray CR fail an otherwise honest review record.
Committed content stays pinned independently by the binary snapshot hashes,
so this normalization does not widen what may be published.

The PR body includes the plan summary, task, per-file review status,
candidate/original executed-test counts, image and commit identities, and
the scope of local validation. Targeted text checks are not comprehensive
correctness or security review, and local verification is not GitHub CI.

The shared delivery code is unchanged: explicit `--publish`, draft only,
no force-push, matching existing-draft reuse, remote task-ref verification,
and a second base check after push before PR creation. The existing lock
and second local `prepare()` check also remain in place.

## Tests and verification boundary

Verified on Windows on 2026-09-21 with `PYTHONPATH=src`:

| Check | Result |
| --- | --- |
| Baseline full Python suite at `4013fbd` | 95 passed, 127.874 seconds |
| Full Python suite with 7C-2 (`python -m unittest discover -s tests`) | 103 passed, 245.755 seconds |
| Publisher CLI `--help` | Passed |
| Python compilation of publisher modules and new tests | Passed |
| `git diff --check` | Passed |

Re-verified on Linux (x86-64, Python 3.11.15, Git 2.43) on 2026-09-21, after
the two hardening changes above:

| Check | Result |
| --- | --- |
| Full Python suite (`python3 -m unittest discover -s tests`) | 105 passed, 17.805 seconds |
| Publisher CLI `--help` | Passed |
| Python compilation of publisher modules and tests | Passed |
| `git diff --check` | Passed |

Each hardening change was confirmed to be load-bearing by reverting it alone
and observing its test fail: without the scope check the doctored
configurations publish, and without `_text()` the CR case is rejected with
`Review hashes or findings do not match`.

The wall-clock difference from the Windows run is sandbox overhead, not a
behavioral difference. Live Ollama, Docker/Gradle, GitHub delivery, and Pi
memory/temperature were **not** exercised on Linux either: no Ollama is
installed in that environment and no workflow ran against a live model. Rust
remains unchanged and untested in both sessions.

`tests/test_repo_execute_publish.py` adds ten tests with subcases. They
exercise the actual 7B producer and publisher using real temporary Git and
SQLite state with canned inference and verification. Coverage includes:

- Two-file repaired and three-file workflows.
- Missing, failed, timed-out, or altered verification evidence.
- Original-test snapshot integrity and executed-case increases.
- Per-file source/diff review hashes and findings.
- Altered metadata, plan, HEAD, dirty checkout, duplicate final records,
  and an extra unauthorized file in the task commit.
- Draft-only delivery and rejection when remote main advances during publication.
- Absent, empty, oversized, duplicated, non-test and uncommitted
  `editable_test_files` configurations, plus the unmodified one still publishing.
- A committed source containing a lone CR, which must match the producer's
  review hash rather than being rejected.

GitHub delivery is mocked. This milestone has not been deployed or exercised
against live Ollama, Docker/Gradle, or GitHub. Evidence is local, unsigned
workflow state: these checks detect inconsistencies, not a hostile operator
who can rewrite the database, artifacts, and repository together.
