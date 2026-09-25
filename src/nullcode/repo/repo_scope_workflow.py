"""Milestone 7C-1: model-proposed scope, human-granted scope (repo-scope-v1).

A read-only workflow. The model proposes which committed files a task would
have to change; nothing here grants anything. Edit authority stays exactly
where it was: the committed ``.nullcode.json`` at the pinned base commit,
changed only by a human-authored commit.

The workflow never writes, stages or commits in the target repository, never
runs Gradle/JUnit or generated code, and hands nothing to repo-execute-v1.
Its artifacts are evidence for a person to read. repo-execute-v1 and the 7C-2
publisher do not import this module and cannot consume what it writes.
"""

import hashlib
import json
import pathlib

from nullcode.core.java_workflow import JOBS, generate
from nullcode.gradle.gradle_workflow import inspect as inspect_gradle
from nullcode.gradle.gradle_workflow import is_production_java, validate_editable_files
from nullcode.repo.repo_execute_workflow import (
    MAX_SELECTED_FILES,
    MAX_SOURCE_BYTES,
    check_source_limit,
    committed_source_bytes,
    compact_prompt_java,
    is_test_java,
    validate_editable_test_files,
)
from nullcode.repo.repo_execute_workflow import validate_selection as validate_execute_selection
from nullcode.repo.repo_plan_workflow import MAX_TASK_BYTES, committed_inventory, extract_json
from nullcode.repo.repo_workflow import git


PROFILE = "repo-scope-v1"

# The controller's input limit. Unchanged; prompts over it raise, never clip.
PROMPT_LIMIT = 2000

# Context files are read-only evidence shown to call 2 in full. One keeps a
# realistic 1+1 edit shape inside PROMPT_LIMIT (see MILESTONE-7C-1.md §7).
MAX_CONTEXT_FILES = 1
MAX_CONTEXT_FILE_BYTES = MAX_SOURCE_BYTES

# Never proposable as edit targets. build.gradle, settings.gradle and
# gradle.properties are separately pinned byte-for-byte by inspect(); this is
# the scope-level refusal, matched on the file name at any depth.
PROTECTED_NAMES = (".nullcode.json", "build.gradle", "settings.gradle", "gradle.properties")

PRODUCTION = "production_files"
TESTS = "test_files"
CONTEXT = "context_files"
EDIT_CLASSES = (PRODUCTION, TESTS)
CLASSES = (PRODUCTION, TESTS, CONTEXT)

INERT_NOTE = (
    "Evidence only. This proposal grants nothing and no workflow consumes it. "
    "Edit authority is the committed .nullcode.json at the base commit; a "
    "grant is a human-authored commit changing that file."
)


def prepare_spec(repo, base, task):
    repo = pathlib.Path(repo).resolve(strict=True)

    if not task.strip() or len(task.encode()) > MAX_TASK_BYTES:
        raise ValueError(f"Task must contain 1 to {MAX_TASK_BYTES} bytes")

    commit = git(repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}")

    # The proposal is for repo-execute-v1, so the repository must already be
    # an approved Gradle project with valid committed configuration.
    _paths, config = inspect_gradle(repo, commit)
    committed_scope(config)

    return {
        "profile": PROFILE,
        "repo": str(repo),
        "base": base,
        "base_commit": commit,
        "task": task.strip(),
    }


def committed_scope(config):
    """Committed (production, tests). editable_test_files may be absent: a grant
    can create it. If present it must at least be a list of strings; the full
    7B rules are applied to the configuration a grant would produce."""
    tests = config.get("editable_test_files", [])

    if not isinstance(tests, list) or not all(isinstance(name, str) for name in tests):
        raise ValueError("Committed editable_test_files must be a list of paths")

    return list(config["editable_files"]), list(tests)


def grant_config(config, production, tests):
    """The configuration a human would commit to grant this scope. Pure: it
    appends new paths, keeps every existing key and entry, and writes nothing."""
    granted_production, granted_tests = committed_scope(config)
    proposed = dict(config)
    proposed["editable_files"] = granted_production + [
        name for name in production if name not in granted_production
    ]
    proposed["editable_test_files"] = granted_tests + [
        name for name in tests if name not in granted_tests
    ]
    return proposed


def _path_list(data, key, label):
    value = data.get(key)

    if not isinstance(value, list) or not all(
        isinstance(name, str) and name for name in value
    ):
        raise ValueError(f"{label} {key} must be a list of path strings")

    return value


def _reject_duplicates(classes, label):
    seen = {}

    for key, names in classes.items():
        for name in names:
            if name in seen:
                raise ValueError(
                    f"{label} names {name} more than once ({seen[name]}, {key})"
                )
            seen[name] = key


def validate_edit_scope(production, tests, checkout, base, paths, config):
    """Every edit-scope rule, in order. Shared by call 1, call 2 and the review.

    Returns the configuration granting this scope would produce, after
    checking it with the same validators repo-execute-v1 applies and checking
    that 7B's own selection validator would accept exactly this file set.
    """
    for name in production + tests:
        if pathlib.PurePosixPath(name).name in PROTECTED_NAMES:
            raise ValueError(f"Scope may never propose editing protected path: {name}")

    for name in production:
        if not is_production_java(name):
            raise ValueError(f"Not a Java production source (src/main/java/*.java): {name}")

    for name in tests:
        if not is_test_java(name):
            raise ValueError(f"Not a Java test source (src/test/java/*.java): {name}")

    if not production:
        raise ValueError("Scope requires at least one production Java file")

    if not tests:
        raise ValueError("Scope requires at least one Java test file")

    if len(production) + len(tests) > MAX_SELECTED_FILES:
        raise ValueError(f"Scope exceeds {MAX_SELECTED_FILES} edit files")

    check_source_limit(checkout, base, production + tests)

    proposed = grant_config(config, production, tests)

    for key, label in (("editable_files", "production"), ("editable_test_files", "test")):
        if len(proposed[key]) > 8:
            raise ValueError(
                f"Granting this scope would exceed 8 editable {label} files "
                f"({len(proposed[key])})"
            )

    try:
        validate_editable_files(proposed["editable_files"], paths)
        validate_editable_test_files(proposed["editable_test_files"], paths)
        check_source_limit(
            checkout, base,
            proposed["editable_files"] + proposed["editable_test_files"],
        )
        validate_execute_selection(
            {"files": production + tests},
            proposed["editable_files"],
            proposed["editable_test_files"],
        )
    except ValueError as error:
        raise ValueError(
            "Granting this scope would produce a configuration or selection "
            f"repo-execute-v1 rejects: {error}"
        ) from error

    return proposed


def validate_context(context, inventory, checkout, base):
    if len(context) > MAX_CONTEXT_FILES:
        raise ValueError(f"Scope names more than {MAX_CONTEXT_FILES} context file(s)")

    for name in context:
        if name not in inventory:
            raise ValueError(f"Context file is not in the committed inventory: {name}")

        size = committed_source_bytes(checkout, base, name)

        if size > MAX_CONTEXT_FILE_BYTES:
            raise ValueError(
                f"Context file {name} is {size} bytes; the limit is {MAX_CONTEXT_FILE_BYTES}"
            )


def selection_prompt(task, inventory):
    prompt = (
        "Scope a small Java task. Do not write code. From the listed committed "
        "files only, name the files the task must CHANGE: production files "
        "under src/main/java/ and test files under src/test/java/, at least one "
        f"of each, at most {MAX_SELECTED_FILES} in total. You may name up to "
        f"{MAX_CONTEXT_FILES} other listed file to READ for context. Never "
        "name build files or .nullcode.json to change. Return JSON only: "
        '{"production_files":["path"],"test_files":["path"],'
        '"context_files":["path"],"reason":"short reason"}.\n'
        f"Task: {task}\n"
        "Committed files:\n"
        + "\n".join(inventory)
    )

    size = len(prompt.encode())

    if size > PROMPT_LIMIT:
        raise ValueError(
            f"Scope selection prompt needs {size}/{PROMPT_LIMIT} bytes; nothing truncated"
        )

    return prompt


def validate_selection(data, inventory, checkout, base, paths, config):
    """Call 1, checked before any nominated source is read.

    Rejects, in order: a non-object; a missing or mistyped class list or
    reason; a path named twice anywhere; a path outside the committed
    inventory; every edit-scope rule; an out-of-bounds context selection.
    Unrecognized keys are ignored and never reach the validated record.
    """
    if not isinstance(data, dict):
        raise ValueError("Scope selection must be a JSON object")

    classes = {key: _path_list(data, key, "Scope selection") for key in CLASSES}
    reason = data.get("reason")

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Scope selection requires a reason")

    _reject_duplicates(classes, "Scope selection")

    for key, names in classes.items():
        for name in names:
            if name not in inventory:
                raise ValueError(
                    f"Scope selection names a path outside the committed inventory: {name}"
                )

    validate_edit_scope(classes[PRODUCTION], classes[TESTS], checkout, base, paths, config)
    validate_context(classes[CONTEXT], inventory, checkout, base)

    return {**classes, "reason": reason.strip()}


def committed_text(checkout, base, name):
    return git(checkout, "show", base + ":" + name, raw=True)


def proposal_evidence(checkout, base, selection):
    """Complete committed content of every nominated file. Java loses only
    presentation whitespace (7B's compact_prompt_java); nothing is clipped."""
    sections = []

    for key in CLASSES:
        for name in selection[key]:
            text = committed_text(checkout, base, name)
            if name.endswith(".java"):
                text = compact_prompt_java(text)
            sections.append(f"FILE {name}:\n{text.rstrip()}\n")

    return "".join(sections)


def proposal_prompt(task, selection, evidence):
    prompt = (
        "Finalize the edit scope for a small Java task. Do not write code. You "
        "may DROP listed paths but never add one or move one to another list. "
        "Keep at least one production and one test file. Give every kept path "
        "a reason. Return JSON only: "
        '{"production_files":[{"path":"...","reason":"..."}],'
        '"test_files":[{"path":"...","reason":"..."}],'
        '"context_files":[{"path":"...","reason":"..."}],'
        '"risks":["..."],"summary":"..."}.\n'
        f"Task: {task}\n"
        f"Production: {json.dumps(selection[PRODUCTION])}\n"
        f"Test: {json.dumps(selection[TESTS])}\n"
        f"Context: {json.dumps(selection[CONTEXT])}\n"
        + evidence
    )

    size = len(prompt.encode())

    if size > PROMPT_LIMIT:
        raise ValueError(
            f"Scope proposal evidence needs {size}/{PROMPT_LIMIT} bytes; nothing truncated"
        )

    return prompt


def _proposal_entries(data, key):
    value = data.get(key)

    if not isinstance(value, list):
        raise ValueError(f"Scope proposal requires a {key} list")

    entries = []

    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"Scope proposal {key} entries must be objects")

        path, reason = item.get("path"), item.get("reason")

        if not isinstance(path, str) or not path:
            raise ValueError(f"Scope proposal {key} entry requires a path")

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"Scope proposal requires a reason for {path}")

        entries.append({"path": path, "reason": reason.strip()})

    return entries


def validate_proposal(data, selection, inventory, checkout, base, paths, config):
    """Call 2. It may only narrow call 1, class by class.

    Rejects, in order: a non-object; a missing or mistyped class list,
    entry, reason, risks or summary; a path named twice anywhere; any path
    call 1 did not nominate in the same class (context promoted to edit,
    a class change, or a new path); every edit-scope rule; the context
    bounds. Unrecognized keys are ignored and never reach the validated record.
    """
    if not isinstance(data, dict):
        raise ValueError("Scope proposal must be a JSON object")

    entries = {key: _proposal_entries(data, key) for key in CLASSES}
    risks = data.get("risks")
    summary = data.get("summary")

    if not isinstance(risks, list) or not all(isinstance(risk, str) for risk in risks):
        raise ValueError("Scope proposal risks must be a list of strings")

    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Scope proposal requires a summary")

    classes = {key: [item["path"] for item in items] for key, items in entries.items()}

    _reject_duplicates(classes, "Scope proposal")

    for key, names in classes.items():
        for name in names:
            if name in selection[key]:
                continue

            if key in EDIT_CLASSES and name in selection[CONTEXT]:
                raise ValueError(
                    f"Scope proposal promotes context-only {name} to {key}; "
                    "a context file is never edit authority"
                )

            for other in CLASSES:
                if name in selection[other]:
                    raise ValueError(
                        f"Scope proposal moves {name} from {other} to {key}"
                    )

            raise ValueError(
                f"Scope proposal introduces {name}, which call 1 did not nominate"
            )

    validate_edit_scope(classes[PRODUCTION], classes[TESTS], checkout, base, paths, config)
    validate_context(classes[CONTEXT], inventory, checkout, base)

    return {
        **entries,
        "risks": [risk.strip() for risk in risks if risk.strip()],
        "summary": summary.strip(),
    }


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run_job(store, job, generate_fn=generate, artifacts=JOBS):
    """Two inference calls, deterministic validation, no repair, no execution."""
    job_id = job["id"]
    root = artifacts / f"workflow-{job_id}"
    created = False

    try:
        spec = json.loads(job["repo_spec"])

        if spec.get("profile") != PROFILE:
            raise ValueError("Not a repo-scope-v1 workflow")

        base = spec["base_commit"]

        root.mkdir(parents=True, exist_ok=False)
        created = True

        checkout = root / "repo"

        store.status(job_id, "inspecting")

        git(root, "clone", "--no-hardlinks", "--no-checkout", "--",
            pathlib.Path(spec["repo"]).as_posix(), checkout.as_posix())
        git(checkout, "checkout", "--detach", base)
        git(checkout, "remote", "remove", "origin")

        paths, config = inspect_gradle(checkout, base)
        committed_scope(config)

        inventory = committed_inventory(checkout, base)
        (root / "repo-tree.txt").write_text("\n".join(inventory) + "\n", encoding="utf-8")

        # ----- Call 1: candidate selection -----
        attempt = root / "attempt-1"
        attempt.mkdir()

        store.attempt(job_id, 1, phase="selecting-scope", artifact_dir=str(attempt))

        prompt = selection_prompt(spec["task"], inventory)
        (attempt / "selection-prompt.txt").write_text(prompt, encoding="utf-8")

        store.status(job_id, "selecting-scope")

        answer = generate_fn(
            prompt, lambda ident: store.attempt(job_id, 1, inference_job=ident)
        )
        (attempt / "selection-answer.txt").write_text(answer, encoding="utf-8")

        store.status(job_id, "validating-selection")
        store.attempt(job_id, 1, phase="validating-selection")

        selection = validate_selection(
            extract_json(answer), inventory, checkout, base, paths, config
        )

        _write_json(attempt / "selection.json", selection)

        selection_result = {"passed": True, "stage": "selection", "selection": selection}
        _write_json(attempt / "result.json", selection_result)
        store.attempt(job_id, 1, phase="passed", result=json.dumps(selection_result))

        # ----- Call 2: final scope proposal -----
        attempt = root / "attempt-2"
        attempt.mkdir()

        store.attempt(job_id, 2, phase="proposing-scope", artifact_dir=str(attempt))

        prompt = proposal_prompt(
            spec["task"], selection, proposal_evidence(checkout, base, selection)
        )
        (attempt / "proposal-prompt.txt").write_text(prompt, encoding="utf-8")

        store.status(job_id, "proposing-scope")

        answer = generate_fn(
            prompt, lambda ident: store.attempt(job_id, 2, inference_job=ident)
        )
        (attempt / "proposal-answer.txt").write_text(answer, encoding="utf-8")

        store.status(job_id, "validating-proposal")
        store.attempt(job_id, 2, phase="validating-proposal")

        validated = validate_proposal(
            extract_json(answer), selection, inventory, checkout, base, paths, config
        )

        # Read-only, checked rather than assumed.
        if git(checkout, "status", "--porcelain") or git(checkout, "rev-parse", "HEAD") != base:
            raise ValueError("Checkout changed during read-only scope proposal")

        proposal = {
            "profile": PROFILE,
            "workflow_id": job_id,
            "task": spec["task"],
            "base_commit": base,
            "committed_config_sha256": sha256_bytes(
                git(checkout, "show", base + ":.nullcode.json", binary=True)
            ),
            **validated,
            "edit_authority": False,
            "note": INERT_NOTE,
        }

        _write_json(attempt / "scope-proposal.json", proposal)
        _write_json(root / "scope-proposal.json", proposal)

        result = {"passed": True, "stage": "proposal", "profile": PROFILE,
                  "proposal": proposal, "repository_clean": True}
        _write_json(attempt / "result.json", result)
        store.attempt(job_id, 2, phase="passed", result=json.dumps(result))

        store.status(job_id, "succeeded")

    except Exception as error:
        number = 2 if created and (root / "attempt-2").is_dir() else 1
        failure = {"passed": False, "error": str(error)}

        if created and (root / f"attempt-{number}").is_dir():
            try:
                _write_json(root / f"attempt-{number}" / "result.json", failure)
            except Exception:
                pass

        try:
            store.attempt(job_id, number, phase="error", result=json.dumps(failure))
        except Exception:
            pass

        store.status(job_id, "failed", str(error))
