# Accepted Java workflow (`accepted-java-v1`)

This document records the already-implemented acceptance-gated Java workflow. It is documentation of existing behavior, not a new milestone implementation.

## Purpose

`accepted-java-v1` edits exactly one approved production Java file against committed, human-reviewed acceptance tests and a committed acceptance contract.

The model may change the production target. It may not change the contract, acceptance tests, build files, or any other tracked file.

## Entry requirements

Submission is rejected unless all of the following are true:

- the repository is clean;
- the requested base resolves to an exact commit;
- the acceptance contract is committed and declares `profile: accepted-java-v1`;
- the contract target is in the committed `.nullcode.json` `editable_files` list;
- every acceptance test is a committed `src/test/java/*.java` file;
- the contract minimum test count is at least the repository minimum;
- the production target is at most 900 bytes;
- the operator explicitly supplied the human attestation represented by `--acceptance-reviewed`.

`--acceptance-reviewed` is a human decision and must never be defaulted, inferred, or set automatically.

## Immutable inputs and scope

At submission time the workflow hashes every tracked input other than the production target, including the acceptance contract, acceptance tests, and build configuration.

The queued workflow is therefore bound to:

- one exact base commit;
- one exact target production file;
- one exact acceptance contract;
- the hashes of every protected tracked file.

The isolated job checkout removes `origin` before editing. Scope enforcement rejects staged files, untracked files, protected-file changes, or any working-tree change outside the single production target.

## Execution and repair budget

The workflow performs up to three candidate attempts:

1. initial generation;
2. repair 1;
3. repair 2.

The fixed acceptance tests cannot be edited as part of repair.

Each candidate is sandbox-verified with the Gradle/JUnit verifier. A passing result must include:

- successful compile and test stages;
- zero JUnit failures;
- zero skipped tests;
- at least the contract minimum number of tests;
- successful container cleanup;
- a verification snapshot matching the checkout.

A failed result only receives another model attempt when the verifier marks it repairable.

## Compiler-specific repair guidance

The workflow contains one narrow deterministic compiler repair rule, documented separately in [JAVAC-REPAIR-RULE.md](JAVAC-REPAIR-RULE.md).

That rule can add advisory prompt guidance for one known javac error shape. It does not alter scope, increase the attempt budget, or bypass verification.

## Review and commit

After a candidate passes the acceptance suite:

1. deterministic targeted review runs;
2. scope and verification hashes are rechecked;
3. only the production target is staged;
4. one local commit is created;
5. the committed tree is compared to the verified snapshot;
6. the workflow becomes `succeeded` only if the checkout is clean and identical to verified evidence.

The model cannot push or merge. Publishing remains a separate explicit human action.

## Test coverage

The workflow is exercised by `tests/test_accepted_workflow.py` with real temporary Git repositories and SQLite workflow state while inference and Docker verification are simulated.

Coverage includes contract validation, protected-file hashing, one-file scope enforcement, repair behavior, tamper detection, verification evidence, targeted review, and the compiler repair rule.

## Security boundary

This profile is intentionally stricter than the general multi-file execution profile:

- one production target only;
- tests are fixed and human reviewed;
- protected content is hash-pinned;
- no model-written acceptance tests;
- no automatic publication;
- no merge capability.

Human review of the acceptance contract/tests remains part of the trust model.
