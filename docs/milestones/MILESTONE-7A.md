# Milestone 7A: read-only repository inspection and planning

`repo_plan_workflow.py` lets the model look at an arbitrary Git repository and
propose a plan before any code is written. Profile `repo-plan-v1`. This
milestone makes no repository changes of any kind; nothing is committed,
nothing is pushed, and the workflow explicitly verifies the checkout is still
clean at the end.

The workflow runs in two model calls against an isolated, `origin`-less clone:

1. **File selection.** The model is shown the committed file tree (Git blobs
   only, symlinks and build/VCS directories excluded, `.java` / `.gradle` /
   `.kts` / `.properties` / `.json` / `.md` / `.txt` only) and asked to choose
   at most 3 files relevant to the task. It cannot propose a file that isn't
   in that tree.
2. **Planning.** The model is shown the full text of the files it selected
   (each clipped to 550 bytes, symlink and path-escape checked before
   reading) and asked to return a JSON plan: a summary, a reasoned file list,
   ordered implementation steps, and risks. Every file the plan references
   must be one of the files selected in step 1 — the model cannot plan around
   a file it never asked to see.

## Checks

- The task prompt is capped at 500 bytes; the committed-tree listing at 1200
  bytes; per-file context at 550 bytes. Both model prompts are hard-capped at
  the controller's 2000-byte input limit, and construction raises rather than
  silently truncating if a prompt would exceed it.
- File selection: JSON object, non-empty `files` list, at most 3 entries, no
  duplicates, every entry present in the committed inventory.
- Plan validation: non-empty `summary`, non-empty `steps` (all non-empty
  strings), a `files` list where every `path` is one of the selected files and
  carries a non-empty `reason`, and a `risks` list (may be empty, must be a
  list of strings).
- After planning, the workflow re-checks `git status --porcelain` on the
  checkout and fails the job outright if anything changed — this is the
  read-only guarantee, checked, not assumed.
- Symlinks are refused when reading file content, and a resolved path must
  stay inside the checkout root.

## What it does not do

No code is generated. No file is edited, staged, or committed. There is no
repair loop — a malformed selection or plan simply fails the job. The plan
is not carried into an execution step by this milestone; that connection is
Milestone 7B (`repo-execute-v1`), which re-selects files with its own,
stricter rules rather than consuming 7A's output directly.

## Try it

```sh
python3 -m nullcode.core.java_workflow submit-plan \
  --repo /srv/nullbrain/repos/<some-repo> --base main \
  --task 'Describe the change you want inspected, not implemented.'
python3 -m nullcode.core.java_workflow wait <id>
```

Artifacts land under `/srv/nullbrain/jobs/workflow-<id>/`: `repo-tree.txt`
(the inventory shown to the model), `candidate-files.json` (the selection),
`plan.json` (the final plan), and per-stage `attempt-1/` / `attempt-2/`
directories with the exact prompts and raw answers.

## Validation

`tests/test_repo_plan_workflow.py` exercises `run_job` end to end using the
same fake-inference pattern as `tests/test_workflow.py` — a real Git fixture,
a real checkout and clone, canned model answers, no real Ollama or Docker
call. It covers:

- a valid selection and plan succeeding, producing a correct `plan.json`, and
  leaving both the checkout and the source repository byte-for-byte clean
- a selection referencing a file outside the committed inventory being
  rejected
- a plan referencing a file the model never selected being rejected
- malformed (non-JSON) model output being rejected before any file is read
- the read-only guarantee being a real, checked property: the test
  deliberately has the fake model write a file into the checkout mid-workflow
  and confirms the job fails rather than silently succeeding

6 tests, run with `python3 -m unittest tests.test_repo_plan_workflow -v`.
Confirmed passing on the Pi (ARM64) as part of the full 93-test suite, not
just in isolation.
