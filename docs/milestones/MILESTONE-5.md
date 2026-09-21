# Milestone 5: draft pull-request delivery

`publish_workflow.py` is an explicit CLI delivery step for milestone-4 repository results. The worker does not automatically publish jobs. The default mode is a local-only preview; `--publish` sends the verified commit to the selected GitHub repository and creates a draft pull request.

## Checks

Publication requires a successful repository workflow, zero compilation/test/cleanup exit codes, the eight-test completion marker, passing targeted review, an unchanged clean checkout, exactly one commit on the verified base, and only the allowed Java file changed. Source/diff hashes must match the review evidence, and the saved source/test snapshots must match the commit. Current targeted rules are rerun locally.

GitHub main must exactly equal the recorded base commit. Stale bases are rejected rather than silently rebased. The task branch includes workflow ID and a commit prefix. An existing different task-branch head is rejected; pushes use no force option. A matching open draft PR is reused on retry. A closed, merged or non-draft PR for the same head requires manual review. A second base check happens after pushing and before PR creation; if main moved, the task branch remains but no PR is opened. This is not an atomic lock on GitHub main.

The publisher uses the signed-in GitHub CLI credentials on the host. It does not mount credentials into build containers or pass them to the model. The PR body is written to a file, includes local verification evidence and hashes, and explicitly distinguishes local results from GitHub CI. A receipt is saved as `publication.json` in the workflow directory. Main is never pushed by the publisher and it has no merge operation.

This still supports only the Numbers repository profile. The review rules are targeted checks, not a comprehensive review or a cryptographically trusted attestation. The publisher trusts the host's saved workflow records and checks consistency with the local commit.

## Install and test

```sh
tar -xzf ~/nullcode-milestone-5.tar.gz -C /srv/nullbrain/src
cd /srv/nullbrain/src/nullcode
python3 -m unittest -v test_publish.py
sudo apt update
sudo apt install -y gh
gh auth login --hostname github.com --git-protocol https --web
gh auth status --hostname github.com
```

Expected: 10 publisher tests pass. Their Git operations are real local fixture operations; GitHub API calls, push and PR creation are mocked. No service restart or Rust rebuild is required. Do not run gh login as root or paste access tokens into chat.

## Bootstrap the private test repository

Run once after authentication:

```sh
gh repo create nullcode-sandbox --private \
  --source=/srv/nullbrain/repos/nullcode-java-fixture \
  --remote=github --push
```

This creates a private repository under the authenticated account and uploads the original fixture main branch. It is the one-time bootstrap; the publisher subsequently pushes only task branches. If the name already exists, stop and inspect it instead of reusing an unknown repository or forcing a push.

## Preview and publish workflow 4

```sh
github_owner=$(gh api --hostname github.com user --jq .login)
python3 publish_workflow.py 4 --repo "$github_owner/nullcode-sandbox"
python3 publish_workflow.py 4 --repo "$github_owner/nullcode-sandbox" --publish
```

The preview prints the exact commit, target branch and PR description and makes no GitHub requests. The publish command creates the draft PR and prints its URL. If PR creation fails after a push, rerun the same publish command; it checks the remote branch and existing PR first. If the remote base changed, generate and verify a new workflow instead. Leave the draft unmerged for review.

References: https://cli.github.com/manual/gh_pr_create , https://cli.github.com/manual/gh_auth_login , https://cli.github.com/manual/gh_repo_create
