# Milestone 4: targeted review before committing

Repository jobs now require both successful build/test verification and a passing review before creating a local commit. Review artifacts include findings with source line numbers, the rule-set version, and SHA-256 hashes of the reviewed source and diff. The gate reviews the full candidate source (without truncation) and binds its result to the saved diff; it is not a general semantic diff reviewer.

## Current rules

- Impossible comparisons of plainly declared int variables against Integer.MIN_VALUE/MAX_VALUE.
- An unwanted static void main demonstration method.
- System.out/System.err print calls in the Numbers utility.

Comments and string/character literals are ignored. These are narrowly scoped text-based checks, not a Java parser, comprehensive static analyzer, independent model review, or security audit. They do not catch every equivalent expression, type-shadowing situation or Java construct. A passing review only means these rules found no issue. The model supplies corrections; it does not decide whether its own output passes this gate.

## Attempt limits and evidence

Each repository workflow permits one build/test/extraction repair and one review correction, with at most three candidate attempts in total. Corrected code must compile, pass all eight tests, and pass review again. Repeated review findings or exhausted build/test repairs fail the job without a new commit. Docker/infrastructure errors do not cause model repairs. Existing non-repository smoke jobs retain their previous behavior.

Each verified candidate saves `review.json` next to its `diff.patch` and `result.json`. The aggregate result distinguishes `verification_passed` from final `passed`. A first attempt may have successful compile/test results but `passed: false` and `review.status: changes_requested`. The final repository metadata links the passing review artifact. The prior workflow 3 commit is not rewritten or retroactively marked reviewed.

## Install and exercise

After current jobs finish, stop the worker, extract the package over `/srv/nullbrain/src`, and run all tests:

```sh
sudo systemctl stop nullcode-worker
tar -xzf ~/nullcode-milestone-4.tar.gz -C /srv/nullbrain/src
cd /srv/nullbrain/src/nullcode
python3 -m unittest -v test_workflow.py test_repo_workflow.py test_review.py
sudo systemctl start nullcode-worker
```

No Rust rebuild, database migration, new dependency, firewall change, or service-file replacement is required. Expected test count: 29. Tests use real local Git repositories but mocked inference/build results; the Pi exercise verifies actual inference and container execution.

```sh
python3 java_workflow.py submit-repo \
  --repo /srv/nullbrain/repos/nullcode-java-fixture --base main \
  --task 'Implement max(int[] values). Reject null and empty arrays with IllegalArgumentException. Handle negative values and integer boundaries. Preserve the tests.' \
  --review-demo
python3 java_workflow.py wait 4
```

Use the returned ID. The demo begins with the same kind of passing-but-redundant Java seen in workflow 3. Expected first attempt: eight tests pass, review requests removal of impossible range checks, no commit. The local model gets the findings and attempts a correction. Success requires new build/test results, a passing review and then a local commit. Failure is retained honestly if the model cannot correct it within the budget.

Review the final commit and result paths printed by `wait`. No GitHub push or PR occurs in this milestone.
