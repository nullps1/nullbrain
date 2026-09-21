# Milestone 3: repository-backed Java jobs

The existing single worker now accepts `submit-repo`. It records the selected commit at submission, clones the local repository without hardlinks into the job directory, creates `agent/workflow-ID`, and removes the origin remote. Only the configured Java source file is edited. The committed test file is preserved and used by the existing restricted Docker verifier. Passing changes are committed locally and a Git patch is saved for review. No push or PR is performed.

## Scope

This first profile is `numbers-jdk21-v1`: `src/main/java/Numbers.java`, `src/test/java/NumbersTest.java`, no packages or external dependencies, and the established eight-check completion marker. Builds use the fixed javac/java commands from milestone 2. It is not yet a Gradle/Maven or arbitrary-repository runner. Only committed content from the chosen base is used; uncommitted changes in the source checkout are left alone. The model receives the task and a bounded excerpt of the current source, with diagnostics on its one repair attempt. It does not yet browse the repository itself.

Repositories are limited to 32 regular, non-executable tracked files, 32 KiB per file, and 256 KiB total. Symlinks and submodules are rejected. Git hooks and signing are disabled for job operations. The original repository is read-only from the workflow's perspective. Java runs against staged source/test snapshots without access to the Git checkout, Docker socket, or credentials.

## Upgrade

Finish any existing workflows first (`python3 java_workflow.py list`). Stop the worker before replacing files. Back up the workflow database using SQLite's backup API. Extract the package into `/srv/nullbrain/src`. The Rust container stays running and needs no rebuild. Run:

```sh
cd /srv/nullbrain/src/nullcode
python3 -m unittest -v test_workflow.py test_repo_workflow.py
sudo systemctl start nullcode-worker
```

All 19 tests should pass. This adds one nullable `repo_spec` column to the workflow database at startup; existing history is retained. Local tests use real Git repositories and fake inference/container results. Actual model and container verification must run on the Pi.

## First repository workflow

```sh
sudo install -d -o null -g null -m 0750 /srv/nullbrain/repos
python3 create_fixture.py /srv/nullbrain/repos/nullcode-java-fixture
python3 java_workflow.py submit-repo \
  --repo /srv/nullbrain/repos/nullcode-java-fixture --base main \
  --task 'Implement max(int[] values). Reject null and empty arrays with IllegalArgumentException. Handle negative values and integer boundaries. Preserve the tests.'
python3 java_workflow.py wait 3
```

Replace 3 with the returned workflow ID. The fixture creator refuses to overwrite an existing path. The expected outcome is a verified source change, local commit, task branch, and saved patch. Model success remains something to measure, not assume.

Review the paths printed in the result:

- `/srv/nullbrain/jobs/workflow-ID/repo`: isolated repository and branch.
- `/srv/nullbrain/jobs/workflow-ID/repository.json`: original repository, base commit, branch, passing commit and patch path.
- `/srv/nullbrain/jobs/workflow-ID/attempt-N/diff.patch`: proposed edit for that attempt.
- Each attempt also retains the answer and verification results.

For workflow 3:

```sh
git -C /srv/nullbrain/jobs/workflow-3/repo show --stat --oneline HEAD
git -C /srv/nullbrain/jobs/workflow-3/repo show --format=fuller HEAD
git -C /srv/nullbrain/repos/nullcode-java-fixture status --short
```

The last command should print nothing: the fixture's original main branch stays untouched. A failed workflow produces no passing commit and preserves its attempts for inspection.
