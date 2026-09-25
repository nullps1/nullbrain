"""Render, never apply, the .nullcode.json change that would grant a reviewed
repo-scope-v1 proposal (Milestone 7C-1).

--scope-reviewed is a human attestation that the proposal was reviewed for
rendering a grant diff. It is not edit authority. This command reads the
workflow's own artifacts and its isolated checkout at the pinned base commit;
it never opens, writes, stages or commits the target repository, and it starts
no other workflow. The grant is a human editing and committing .nullcode.json.
"""

import argparse
import difflib
import json

from nullcode.core.java_workflow import JOBS, Store
from nullcode.gradle.gradle_workflow import inspect as inspect_gradle
from nullcode.repo.repo_plan_workflow import committed_inventory
from nullcode.repo.repo_scope_workflow import (
    CONTEXT,
    PRODUCTION,
    PROFILE,
    TESTS,
    sha256_bytes,
    validate_edit_scope,
)
from nullcode.repo.repo_workflow import git


ATTESTATION = (
    "--scope-reviewed: a human reviewed this proposal for rendering a grant "
    "diff. This is NOT edit authority. Nothing was written to the target "
    "repository. Authority comes only from a human editing .nullcode.json "
    "and committing it."
)


def _paths(proposal, key):
    entries = proposal.get(key)

    if not isinstance(entries, list) or not all(
        isinstance(item, dict) and isinstance(item.get("path"), str) for item in entries
    ):
        raise ValueError(f"Scope proposal artifact has malformed {key}")

    return [item["path"] for item in entries]


def _diff_lines(text):
    lines = text.splitlines(True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


def render_grant(job, artifacts, scope_reviewed):
    """Deterministically compute the grant diff and save it as artifacts.

    ``scope_reviewed`` has no default and must be exactly True.
    """
    if scope_reviewed is not True:
        raise ValueError(
            "Review the scope proposal yourself and pass --scope-reviewed to render a grant diff"
        )

    spec = json.loads(job.get("repo_spec") or "null")

    if not isinstance(spec, dict) or spec.get("profile") != PROFILE:
        raise ValueError("Only repo-scope-v1 workflows carry a scope proposal")

    if job.get("status") != "succeeded":
        raise ValueError("Only a succeeded scope proposal can be reviewed")

    root = (artifacts / f"workflow-{job['id']}").resolve()
    checkout = root / "repo"
    base = spec["base_commit"]

    proposal = json.loads((root / "scope-proposal.json").read_text(encoding="utf-8"))

    if (
        not isinstance(proposal, dict)
        or proposal.get("profile") != PROFILE
        or proposal.get("workflow_id") != job["id"]
        or proposal.get("base_commit") != base
        or proposal.get("edit_authority") is not False
    ):
        raise ValueError("Scope proposal artifact does not match its workflow")

    # Re-derive everything from the committed configuration at the pinned
    # base. The artifact's paths are re-validated, not trusted.
    raw = git(checkout, "show", base + ":.nullcode.json", binary=True)

    if sha256_bytes(raw) != proposal.get("committed_config_sha256"):
        raise ValueError("Committed .nullcode.json does not match the proposal's base")

    paths, config = inspect_gradle(checkout, base)
    inventory = committed_inventory(checkout, base)
    production, tests = _paths(proposal, PRODUCTION), _paths(proposal, TESTS)

    for name in production + tests:
        if name not in inventory:
            raise ValueError(f"Scope proposal artifact names an uninventoried path: {name}")

    proposed = validate_edit_scope(production, tests, checkout, base, paths, config)

    current_text = raw.decode("utf-8")
    # Keep the committed file's trailing-newline convention so the diff shows
    # only the grant.
    proposed_text = json.dumps(proposed, indent=2) + ("\n" if current_text.endswith("\n") else "")
    patch = "".join(difflib.unified_diff(
        _diff_lines(current_text),
        _diff_lines(proposed_text),
        "a/.nullcode.json",
        "b/.nullcode.json",
    ))

    added_production = [n for n in proposed["editable_files"] if n not in config["editable_files"]]
    added_tests = [
        n for n in proposed["editable_test_files"]
        if n not in config.get("editable_test_files", [])
    ]

    record = {
        "profile": PROFILE,
        "workflow_id": job["id"],
        "repository": spec["repo"],
        "base_commit": base,
        "scope_reviewed": True,
        "attestation": ATTESTATION,
        "edit_authority": False,
        "applied": False,
        "committed_config_sha256": sha256_bytes(raw),
        "proposed_config_sha256": sha256_bytes(proposed_text.encode()),
        "configuration_change_required": bool(added_production or added_tests),
        "added_editable_files": added_production,
        "added_editable_test_files": added_tests,
        "proposed_edit_scope": {PRODUCTION: production, TESTS: tests},
        "context_files": _paths(proposal, CONTEXT),
        "summary": proposal.get("summary"),
        "risks": proposal.get("risks"),
        "grant_process": [
            "Confirm your branch's committed .nullcode.json has the SHA-256 "
            "recorded as committed_config_sha256.",
            "Apply proposed-nullcode.patch by hand, review it, and commit it yourself.",
            "Only that human-authored commit grants anything; submit "
            "repo-execute-v1 separately against it.",
        ],
    }

    (root / "proposed-nullcode-config.json").write_text(proposed_text, encoding="utf-8")
    (root / "proposed-nullcode.patch").write_text(patch, encoding="utf-8")
    (root / "scope-review.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    return record, patch


def main(argv=None, store=None, artifacts=JOBS):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow_id", type=int)
    parser.add_argument(
        "--scope-reviewed",
        action="store_true",
        required=True,
        help="Human attestation that you reviewed the proposal. It only renders "
             "a grant diff; it is not edit authority.",
    )
    args = parser.parse_args(argv)

    store = store or Store()
    record, patch = render_grant(store.show(args.workflow_id), artifacts, args.scope_reviewed)

    print(json.dumps({key: record[key] for key in (
        "workflow_id", "repository", "base_commit", "committed_config_sha256",
        "added_editable_files", "added_editable_test_files",
    )}, indent=2))
    print("\nProposed .nullcode.json change (NOT applied):\n" + (patch or "(no change required)\n"))
    print(ATTESTATION)
    print("Saved under " + str((artifacts / f"workflow-{args.workflow_id}").resolve()) + ": "
          "scope-review.json, proposed-nullcode-config.json, proposed-nullcode.patch")


if __name__ == "__main__":
    main()
