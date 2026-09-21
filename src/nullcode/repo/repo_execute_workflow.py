"""Milestone 7B: planned, bounded multi-file Java repository execution."""

import hashlib
import json
import pathlib
import re
import subprocess

from nullcode.core.java_workflow import (
    JOBS,
    REJECTED_NO_BEHAVIORAL_DELTA,
    generate,
)
from nullcode.gradle.gradle_workflow import inspect as inspect_gradle
from nullcode.gradle.gradle_workflow import verify as verify_gradle
from nullcode.repo.repo_plan_workflow import (
    committed_inventory,
    extract_json,
    planning_prompt,
    validate_plan,
)
from nullcode.repo.repo_workflow import git
from nullcode.core.review_java import review_source


PROFILE = "repo-execute-v1"
MAX_SELECTED_FILES = 3
MAX_SOURCE_BYTES = 900

# ---------------------------------------------------------------------------
# Behavioral-delta (hybrid counterfactual) classifications
# ---------------------------------------------------------------------------
# Only the two distinguishing values are evidence that the candidate tests
# exercise behavior the pinned base does not satisfy. Everything else stops
# the workflow: a no-op change is rejected on its own terminal state, and an
# unexplained hybrid run is infrastructure failure, never novelty evidence.
DISTINGUISHING_TEST_FAILURE = "distinguishing-test-failure"
DISTINGUISHING_API_COMPILE_FAILURE = "distinguishing-api-compile-failure"
NO_BEHAVIORAL_DELTA = "no-behavioral-delta"
INFRASTRUCTURE_FAILURE = "infrastructure-failure"

DISTINGUISHING_CLASSIFICATIONS = (
    DISTINGUISHING_TEST_FAILURE,
    DISTINGUISHING_API_COMPILE_FAILURE,
)

NO_BEHAVIORAL_DELTA_DIAGNOSTIC = (
    "Candidate tests also pass against pinned-base production; the candidate "
    "does not demonstrate a tested behavioral delta from the base."
)


def prepare_spec(repo, base, task):
    repo = pathlib.Path(repo).resolve(strict=True)

    if not task.strip() or len(task.encode()) > 500:
        raise ValueError("Task must contain 1 to 500 bytes")

    commit = git(
        repo,
        "rev-parse",
        "--verify",
        "--end-of-options",
        base + "^{commit}",
    )

    paths, config = inspect_gradle(repo, commit)

    tests = config.get("editable_test_files")

    if (
        not isinstance(tests, list)
        or not tests
        or len(tests) > 8
        or len(set(tests)) != len(tests)
    ):
        raise ValueError(
            "Configure 1 to 8 unique editable_test_files for repo-execute-v1"
        )

    for name in tests:
        if (
            name not in paths
            or not name.startswith("src/test/java/")
            or not name.endswith(".java")
        ):
            raise ValueError(
                "editable_test_files must be committed Java test sources"
            )

    approved = config["editable_files"] + tests

    for name in approved:
        source = git(repo, "show", commit + ":" + name, raw=True)
        if len(source.encode()) > MAX_SOURCE_BYTES:
            raise ValueError(
                f"{name} exceeds the {MAX_SOURCE_BYTES}-byte 7B source limit"
            )

    return {
        "profile": PROFILE,
        "repo": str(repo),
        "base": base,
        "base_commit": commit,
        "task": task.strip(),
        "minimum_tests": config["minimum_tests"],
    }


def selection_prompt(task, approved):
    listing = "\n".join(approved)

    prompt = (
        "You are choosing files to CHANGE for a small Java task. "
        "Choose 2 or 3 files from the approved list only. "
        "Choose at least one production Java file and at least one Java test file. "
        "Do not write code. "
        'Return JSON only: {"files":["path"],"reason":"short reason"}.\n'
        f"Task: {task}\n"
        "Approved editable files:\n"
        f"{listing}"
    )

    if len(prompt.encode()) > 2000:
        raise ValueError("File-selection prompt exceeds controller limit")

    return prompt


def validate_selection(data, production, tests):
    if not isinstance(data, dict):
        raise ValueError("Selection must be a JSON object")

    files = data.get("files")

    if not isinstance(files, list):
        raise ValueError("Selection requires files")

    if len(files) < 2 or len(files) > MAX_SELECTED_FILES:
        raise ValueError("7B requires 2 or 3 selected files")

    if len(files) != len(set(files)):
        raise ValueError("Selection contains duplicate paths")

    approved = set(production) | set(tests)

    for name in files:
        if name not in approved:
            raise ValueError(f"Model selected unapproved file: {name}")

    if not any(name in production for name in files):
        raise ValueError("Selection requires a production Java file")

    if not any(name in tests for name in files):
        raise ValueError("Selection requires a Java test file")

    return files


def context_for_plan(checkout, selected):
    # Keep total repository evidence near 1100 bytes so the complete planning
    # prompt stays beneath the controller's 2000-byte limit.
    budget = 1100
    per_file = max(250, budget // len(selected))

    parts = []

    for name in selected:
        source = (checkout / name).read_text(encoding="utf-8")
        encoded = source.encode()

        if len(encoded) > per_file:
            source = encoded[:per_file].decode(errors="ignore")

        parts.append(f"FILE: {name}\n{source}")

    return "\n\n".join(parts)


def target_plan(plan, target):
    reason = ""

    for item in plan.get("files", []):
        if isinstance(item, dict) and item.get("path") == target:
            reason = str(item.get("reason", "")).strip()
            break

    steps = [
        str(step).strip()
        for step in plan.get("steps", [])
        if str(step).strip()
    ]

    target_name = pathlib.PurePosixPath(target).name.lower()

    relevant = [
        step for step in steps
        if target_name in step.lower()
    ]

    if not relevant:
        relevant = steps[:1]

    result = reason

    if relevant:
        result += " Steps: " + " ".join(relevant[:2])

    encoded = result.encode()

    if len(encoded) > 350:
        result = encoded[:350].decode(errors="ignore")

    return result


def compact_prompt_java(value):
    # Keep text-block indentation. Otherwise remove presentation whitespace only.
    if '"""' in value:
        return value
    return "\n".join(line.lstrip(" \t") for line in value.splitlines()
                     if line.strip())


def related_context(checkout, target, selected, tests):
    if target not in tests:
        return ""
    # Whole selected production files: never clip a Java method mid-expression.
    return "\n".join(
        name + ":\n" + compact_prompt_java(
            (checkout / name).read_text(encoding="utf-8"))
        for name in selected if name not in tests
    )


# Guidance only. The minimum-count floor, the original-suite regression and
# the added-coverage comparison remain the enforcement mechanism; this just
# stops the model reaching for a replacement suite in the first place.
PRESERVE_TESTS = "Keep every existing @Test method; add new ones alongside them.\n"


def edit_prompt(task, plan, target, source, related=""):
    name = pathlib.PurePosixPath(target).name
    prompt = (
        f"Return ONLY complete {name}, no fences/prose or other files. "
        "Follow TASK; preserve existing behavior/API except requested additions.\n"
        + (PRESERVE_TESTS if target.startswith("src/test/java/") else "")
        + f"TASK:\n{task}\nPLAN:\n{target_plan(plan, target)}\n"
        + f"REFERENCE ONLY:\n{related}\n"
        + f"TARGET {target}:\n{compact_prompt_java(source)}\n"
    )
    if len(prompt.encode("utf-8")) > 2000:
        raise ValueError("Complete edit context exceeds 2000 bytes; nothing truncated")
    return prompt


def repair_edit_prompt(task, plan, target, source, diagnostic, repair_reason, related=""):
    task, diagnostic = str(task).strip(), str(diagnostic).strip()
    if not task or not diagnostic or not source.strip():
        raise ValueError("Repair requires task, diagnostic and complete target source")
    if target.startswith("src/test/java/") and not related.strip():
        raise ValueError("Test repair requires complete production context")
    if diagnostic.startswith("JUnit: ") and "\nTest log: " in diagnostic:
        diagnostic = diagnostic.split("\nTest log: ", 1)[0]
    name = pathlib.PurePosixPath(target).name
    prompt = (
        f"Return ONLY complete {name}, no fences/prose or other files. "
        "Follow TASK; preserve existing behavior. Code/tests may be wrong.\n"
        + (PRESERVE_TESTS if target.startswith("src/test/java/") else "")
        + f"TASK:\n{task}\nFAILURE:\n{diagnostic}\n"
        + f"REFERENCE ONLY:\n{related}\n"
        + f"TARGET:\n{compact_prompt_java(source)}\n"
    )
    size = len(prompt.encode("utf-8"))
    if size > 2000:
        raise ValueError(f"Complete repair context needs {size}/2000 bytes; nothing truncated")
    return prompt




def extract_java(answer, target):
    blocks = re.findall(
        r"^```(?:java)?[ \t]*\r?\n(.*?)^```[ \t]*$",
        answer,
        re.M | re.S,
    )

    if "```" in answer:
        if len(blocks) != 1:
            raise ValueError("Return exactly one complete Java source block")
        source = blocks[0].strip() + "\n"
    else:
        source = answer.strip() + "\n"

    if len(source.encode()) > 4096:
        raise ValueError("Generated Java source exceeds 4096 bytes")

    type_name = pathlib.PurePosixPath(target).stem

    if not re.search(
        r"\b(?:public\s+)?(?:final\s+)?"
        r"(?:class|record|interface|enum)\s+"
        + re.escape(type_name)
        + r"\b",
        source,
    ):
        raise ValueError(
            "Expected a complete Java type named " + type_name
        )

    return source



def executed_test_count(junit):
    """Executed JUnit cases, or None when the evidence is unusable.

    Mirrors gradle_workflow.verify()'s floor arithmetic exactly: skipped
    cases never count as executed.
    """
    tests = junit.get("tests")
    skipped = junit.get("skipped", 0)

    if type(tests) is not int or type(skipped) is not int:
        return None

    return tests - skipped


def insufficient_test_count(result, minimum):
    """True when a candidate built and ran cleanly but executed too few tests.

    gradle_workflow.verify() folds the minimum-test floor into `passed` but
    not into `repairable`, which only fires for a conventional failing JUnit
    run (test exit 1 with failures). A candidate that compiles, runs green
    and simply deletes coverage therefore arrives here as
    passed=False/repairable=False and used to terminate the workflow.

    That shared flag is also consumed by gradle-junit-v1 and
    accepted-java-v1, so repo-execute-v1 classifies this one failure reason
    locally rather than changing the shared contract. Nothing here relaxes a
    gate: the floor, the original-suite regression and the added-coverage
    comparison all still run afterwards, unchanged.
    """
    if result.get("passed") or result.get("infrastructure_error"):
        return False

    if type(minimum) is not int:
        return False

    compile_result = result.get("compile") or {}
    tests_result = result.get("tests") or {}
    junit = result.get("junit") or {}

    if compile_result.get("exit_code") != 0 or compile_result.get("timed_out"):
        return False

    if tests_result.get("exit_code") != 0 or tests_result.get("timed_out"):
        return False

    failures = junit.get("failures")

    if type(failures) is not int or failures != 0:
        return False

    # verify() tolerates an already-gone container and nothing else. Any
    # other cleanup trouble is infrastructure, not a repairable candidate.
    cleanup = result.get("cleanup") or {}

    if cleanup.get("exit_code") and "No such container" not in str(
        cleanup.get("log") or ""
    ):
        return False

    executed = executed_test_count(junit)

    return executed is not None and executed < minimum


def candidate_repairable(result, minimum, selected_tests):
    """Repair eligibility for the repo-execute-v1 candidate stage only.

    The shared flag still decides on its own terms; this only adds the
    insufficient-executed-count case, and only when the selection already
    contains an editable test file that could restore the missing coverage.
    """
    if result.get("repairable"):
        return True

    return bool(selected_tests) and insufficient_test_count(result, minimum)


# ---------------------------------------------------------------------------
# Behavioral-delta counterfactual
# ---------------------------------------------------------------------------
# Every gate before this one can pass on a task that rewrites production code
# cosmetically and adds a test for behavior the pinned base already
# satisfied. The counterfactual run - pinned-base production plus the exact
# candidate test sources - is what supplies the missing evidence: those tests
# must NOT fully pass against the base.


def hybrid_overlay_files(selected, tests):
    """Candidate files the hybrid counterfactual may overlay onto the base.

    Test sources from the approved editable test scope, and nothing else.
    Candidate production is what the hybrid is meant to exclude, so it never
    appears here, and neither does anything outside the approved scope.
    """
    return [name for name in selected if name in tests]


def hybrid_reverted_files(selected, tests):
    """Selected production files the hybrid pins back to base content."""
    return [name for name in selected if name not in tests]


# javac diagnostics that mean "this source no longer matches the API it is
# being compiled against" - exactly what a candidate test referencing a new
# candidate method, type or constructor produces against pinned-base
# production. Matched case-insensitively against the error text only.
SOURCE_COMPATIBILITY_ERRORS = (
    "cannot find symbol",
    "cannot be applied",
    "no suitable method found",
    "no suitable constructor found",
    "incompatible types",
    "has private access",
    "is not public",
    "does not override or implement a method from a supertype",
)

JAVAC_ERROR = re.compile(
    r"(?P<path>[^\s:]+\.java):(?P<line>\d+):\s*error:\s*(?P<message>.+)"
)


def javac_errors(log):
    """(path, message) pairs for every javac error line in a build log."""
    found = []

    for line in str(log or "").splitlines():
        match = JAVAC_ERROR.search(line)

        if match:
            found.append(
                (match.group("path"), match.group("message").strip())
            )

    return found


def distinguishing_compile_failure(result, overlaid):
    """True when the hybrid's TEST compilation failed because the candidate
    tests reference API absent from pinned-base production.

    This is behavioral-delta evidence, not infrastructure failure - but only
    when the compiler output actually says so. The decision is made from that
    output alone: every reported error must sit inside a candidate test file
    the hybrid overlaid, and at least one must be a source-compatibility
    diagnostic. Anything else - production failing to compile, an error in a
    file the candidate never touched, an unparsable log - is not attributable
    to the candidate tests and falls through to infrastructure failure.
    """
    compile_result = result.get("compile") or {}

    if compile_result.get("timed_out") or compile_result.get("exit_code") != 1:
        return False

    log = str(compile_result.get("log") or "")

    # Pinned-base production is known to compile: the baseline regression
    # stage built it moments ago. Production compilation failing here is an
    # environment problem, not candidate-versus-base source incompatibility.
    if ":compileJava FAILED" in log or ":compileTestJava FAILED" not in log:
        return False

    errors = javac_errors(log)

    if not errors:
        return False

    suffixes = tuple("/" + name for name in overlaid)

    if not suffixes or not all(path.endswith(suffixes) for path, _ in errors):
        return False

    return any(
        any(pattern in message.lower() for pattern in SOURCE_COMPATIBILITY_ERRORS)
        for _, message in errors
    )


def classify_behavioral_delta(result, overlaid):
    """Classify the hybrid counterfactual run.

    Returns ``(classification, diagnostic)``. The candidate continues only on
    a distinguishing classification. A hybrid run that fully passes is a
    no-op change and is rejected on its own terminal state. Everything the
    evidence does not positively explain is infrastructure failure, which is
    never novelty evidence and never a no-op verdict.
    """
    if not isinstance(result, dict):
        return (
            INFRASTRUCTURE_FAILURE,
            "Hybrid verification produced no usable result record",
        )

    if result.get("infrastructure_error"):
        return (
            INFRASTRUCTURE_FAILURE,
            "Hybrid verification container could not be started or prepared",
        )

    # verify() tolerates an already-gone container and nothing else.
    cleanup = result.get("cleanup") or {}

    if cleanup.get("exit_code") and "No such container" not in str(
        cleanup.get("log") or ""
    ):
        return (
            INFRASTRUCTURE_FAILURE,
            "Hybrid verification container could not be removed",
        )

    compile_result = result.get("compile") or {}

    if (
        compile_result.get("timed_out")
        or type(compile_result.get("exit_code")) is not int
    ):
        return (
            INFRASTRUCTURE_FAILURE,
            "Hybrid compilation produced no usable exit evidence",
        )

    if compile_result["exit_code"] != 0:
        if distinguishing_compile_failure(result, overlaid):
            return (
                DISTINGUISHING_API_COMPILE_FAILURE,
                "Candidate tests do not compile against pinned-base "
                "production: they reference API the base does not provide.",
            )

        return (
            INFRASTRUCTURE_FAILURE,
            "Hybrid compilation failed for reasons the compiler output does "
            "not attribute to the candidate test sources",
        )

    tests_result = result.get("tests") or {}

    if (
        tests_result.get("timed_out")
        or type(tests_result.get("exit_code")) is not int
    ):
        return (
            INFRASTRUCTURE_FAILURE,
            "Hybrid test execution produced no usable exit evidence",
        )

    junit = result.get("junit") or {}

    if any(
        type(junit.get(field)) is not int
        for field in ("tests", "failures", "skipped")
    ):
        return (
            INFRASTRUCTURE_FAILURE,
            "Hybrid run produced no usable JUnit case evidence",
        )

    if junit["failures"] > 0:
        diagnostics = str(junit.get("diagnostics") or "").strip()

        detail = (
            "Candidate tests fail against pinned-base production, which is "
            "direct evidence that they exercise behavior the base does not "
            "satisfy."
        )

        if diagnostics:
            detail += " " + diagnostics

        return DISTINGUISHING_TEST_FAILURE, detail

    if tests_result["exit_code"] != 0:
        return (
            INFRASTRUCTURE_FAILURE,
            "Hybrid test task failed without reporting a JUnit failure",
        )

    if junit["tests"] - junit["skipped"] < 1:
        return (
            INFRASTRUCTURE_FAILURE,
            "Hybrid run executed no test cases",
        )

    return NO_BEHAVIORAL_DELTA, NO_BEHAVIORAL_DELTA_DIAGNOSTIC


def verification_diagnostic(result, minimum=None):
    parts = []

    junit = result.get("junit") or {}
    diagnostics = str(junit.get("diagnostics") or "").strip()

    if diagnostics:
        parts.append("JUnit: " + diagnostics)

    tests = result.get("tests") or {}
    test_log = str(tests.get("log") or "")

    interesting = [
        line.strip()
        for line in test_log.splitlines()
        if (
            "FAILED" in line
            or "expected:" in line
            or "but was:" in line
            or ".java:" in line
        )
    ]

    if interesting:
        parts.append("Test log: " + " | ".join(interesting[-6:]))

    compile_result = result.get("compile") or {}

    if compile_result.get("exit_code"):
        lines = str(compile_result.get("log") or "").splitlines()

        # Preserve the local javac error block: source location, offending
        # expression, symbol, and inferred receiver/type.
        blocks = []

        for index, line in enumerate(lines):
            if ".java:" in line and "error:" in line:
                block = []

                for candidate in lines[index:index + 7]:
                    candidate = candidate.strip()

                    if candidate:
                        block.append(candidate)

                if block:
                    blocks.append(" | ".join(block))

        if blocks:
            parts.append("Compile: " + " || ".join(blocks[-2:]))
        else:
            fallback = [
                line.strip()
                for line in lines
                if (
                    "error:" in line.lower()
                    or "symbol:" in line
                    or "location:" in line
                    or "FAILED" in line
                )
            ]

            if fallback:
                parts.append("Compile: " + " | ".join(fallback[-8:]))

    # Gradle succeeds, JUnit reports no failures and diagnostics are empty
    # when the candidate simply deleted coverage, so none of the branches
    # above fire. Say what actually went wrong instead of falling through to
    # the generic message, which gives repair selection nothing to act on.
    executed = executed_test_count(junit)

    if type(minimum) is int and executed is not None and executed < minimum:
        parts.append(
            f"Executed tests: {executed}; at least {minimum} must run. "
            "The candidate suite runs fewer cases than required, which "
            "happens when existing tests are replaced instead of extended. "
            "Restore every original test case and add the new coverage "
            "alongside them."
        )

    diagnostic = "\n".join(parts).strip()

    if not diagnostic:
        diagnostic = "Verification failed without a concise diagnostic."

    encoded = diagnostic.encode()

    if len(encoded) > 700:
        diagnostic = encoded[:700].decode(errors="ignore")

    return diagnostic

def repair_selection_prompt(task, plan, selected, diagnostic):
    prompt = (
        "A Java repository change failed verification. "
        "Choose exactly ONE already-selected file to repair. "
        "Do not write code. Determine whether the failure comes from the "
        "implementation or from an incorrect test expectation. "
        "Reason literally from the task semantics; do not assume the expected "
        "test value is correct merely because JUnit reports it as expected. "
        "For wording such as 'longer than N', use strict > N semantics. "
        "Return JSON only: "
        '{"file":"selected/path.java","reason":"short evidence-based explanation"}.\n'
        f"Task: {task}\n"
        f"Selected files: {json.dumps(selected)}\n"
        f"Plan summary: {str(plan.get('summary', ''))[:220]}\n"
        "Verification failure:\n"
        f"{diagnostic}"
    )

    if len(prompt.encode()) > 2000:
        raise ValueError("Repair-selection prompt exceeds controller limit")

    return prompt

def validate_repair_selection(data, selected):
    if not isinstance(data, dict):
        raise ValueError("Repair selection must be a JSON object")

    target = data.get("file")
    reason = data.get("reason")

    if target not in selected:
        raise ValueError(
            f"Repair selected an unapproved file: {target}"
        )

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Repair selection requires a reason")

    return target, reason.strip()




def run_job(store, job, generate_fn=generate, verify_fn=verify_gradle, artifacts=JOBS):
    job_id = job["id"]

    try:
        spec = json.loads(job["repo_spec"])

        root = artifacts / f"workflow-{job_id}"
        root.mkdir(parents=True, exist_ok=False)

        checkout = root / "repo"

        git(
            root,
            "clone",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            pathlib.Path(spec["repo"]).as_posix(),
            checkout.as_posix(),
        )

        paths, config = inspect_gradle(checkout, spec["base_commit"])

        production = config["editable_files"]
        tests = config.get("editable_test_files", [])
        approved = production + tests

        branch = f"agent/workflow-{job_id}"

        git(
            checkout,
            "checkout",
            "-b",
            branch,
            spec["base_commit"],
        )

        git(checkout, "remote", "remove", "origin")

        original_hashes = {
            name: hashlib.sha256(
                (checkout / name).read_bytes()
            ).hexdigest()
            for name in paths
        }

        # ----- Stage 1: file selection -----
        attempt = root / "attempt-1"
        attempt.mkdir()

        prompt = selection_prompt(spec["task"], approved)

        (attempt / "prompt.txt").write_text(prompt, encoding="utf-8")

        store.status(job_id, "inspecting")
        store.attempt(
            job_id,
            1,
            phase="inspecting",
            artifact_dir=str(attempt),
        )

        answer = generate_fn(
            prompt,
            lambda ident: store.attempt(
                job_id,
                1,
                inference_job=ident,
            ),
        )

        (attempt / "answer.txt").write_text(answer, encoding="utf-8")

        selection = extract_json(answer)
        selected = validate_selection(selection, production, tests)

        selection_result = {
            "passed": True,
            "selected_files": selected,
            "reason": selection.get("reason", ""),
        }

        (attempt / "result.json").write_text(
            json.dumps(selection_result, indent=2),
            encoding="utf-8",
        )

        store.attempt(
            job_id,
            1,
            phase="passed",
            result=json.dumps(selection_result),
        )

        (root / "candidate-files.json").write_text(
            json.dumps(selection_result, indent=2),
            encoding="utf-8",
        )

        # ----- Stage 2: planning -----
        attempt = root / "attempt-2"
        attempt.mkdir()

        context = context_for_plan(checkout, selected)

        prompt = planning_prompt(
            spec["task"],
            selected,
            context,
        )

        (attempt / "prompt.txt").write_text(prompt, encoding="utf-8")

        store.status(job_id, "planning")
        store.attempt(
            job_id,
            2,
            phase="planning",
            artifact_dir=str(attempt),
        )

        answer = generate_fn(
            prompt,
            lambda ident: store.attempt(
                job_id,
                2,
                inference_job=ident,
            ),
        )

        (attempt / "answer.txt").write_text(answer, encoding="utf-8")

        plan = validate_plan(extract_json(answer), selected)

        (root / "plan.json").write_text(
            json.dumps(plan, indent=2),
            encoding="utf-8",
        )

        plan_result = {
            "passed": True,
            "plan": plan,
            "selected_files": selected,
        }

        (attempt / "result.json").write_text(
            json.dumps(plan_result, indent=2),
            encoding="utf-8",
        )

        store.attempt(
            job_id,
            2,
            phase="passed",
            result=json.dumps(plan_result),
        )

        # ----- Stage 3: edit selected files -----
        attempt = root / "attempt-3"
        attempt.mkdir()

        store.status(job_id, "editing")
        store.attempt(
            job_id,
            3,
            phase="editing",
            artifact_dir=str(attempt),
        )

        inference_jobs = []

        # Production source first, then tests.
        ordered = sorted(
            selected,
            key=lambda name: 1 if name in tests else 0,
        )

        for index, name in enumerate(ordered, start=1):
            path = checkout / name

            source = path.read_text(encoding="utf-8")

            related = related_context(
                checkout,
                name,
                selected,
                tests,
            )

            prompt = edit_prompt(
                spec["task"],
                plan,
                name,
                source,
                related,
            )

            stem = f"{index}-{pathlib.PurePosixPath(name).name}"

            (attempt / f"{stem}.prompt.txt").write_text(
                prompt,
                encoding="utf-8",
            )

            def record(ident):
                inference_jobs.append(
                    {"path": name, "inference_job": ident}
                )
                store.attempt(
                    job_id,
                    3,
                    inference_job=ident,
                )

            answer = generate_fn(prompt, record)

            (attempt / f"{stem}.answer.txt").write_text(
                answer,
                encoding="utf-8",
            )

            candidate = extract_java(answer, name)

            path.write_text(candidate, encoding="utf-8")

            changed_now = set(
                git(
                    checkout,
                    "diff",
                    "--name-only",
                    spec["base_commit"],
                ).splitlines()
            )

            if not changed_now <= set(selected):
                raise ValueError(
                    "Model changed a file outside the selected scope"
                )

        (attempt / "inference-jobs.json").write_text(
            json.dumps(inference_jobs, indent=2),
            encoding="utf-8",
        )

        changed = git(
            checkout,
            "diff",
            "--name-only",
            spec["base_commit"],
        ).splitlines()

        if set(changed) != set(selected):
            raise ValueError(
                "Every selected file must contain a real edit"
            )

        # ----- Stage 4: candidate verification -----
        verification = attempt / "candidate-verification"
        verification.mkdir()

        def candidate_phase(state):
            store.status(job_id, state)
            store.attempt(job_id, 3, phase=state)

        candidate_result = verify_fn(
            checkout,
            paths,
            verification,
            config["minimum_tests"],
            candidate_phase,
        )

        # If the model was allowed to change tests, it must actually add
        # coverage. Whether it did is checked later, against the real
        # original-suite count from Stage 5's baseline regression run - not
        # here, against the configured floor, which can be stale relative to
        # a suite that has already grown past it.
        selected_tests = [name for name in selected if name in tests]

        initial_candidate_result = candidate_result
        repair_history = []

        for repair_number in (1, 2):
            if candidate_result["passed"]:
                break

            # Insufficient executed-test count arrives from the shared
            # verifier as repairable=False; classify it here so the existing
            # bounded repair can restore the coverage the candidate deleted.
            short_count = insufficient_test_count(
                candidate_result,
                config["minimum_tests"],
            )

            if not candidate_repairable(
                candidate_result,
                config["minimum_tests"],
                selected_tests,
            ):
                result = {
                    "passed": False,
                    "stage": "candidate-verification",
                    "selected_files": selected,
                    "verification": candidate_result,
                    "repairs": repair_history,
                }

                (attempt / "result.json").write_text(
                    json.dumps(result, indent=2),
                    encoding="utf-8",
                )

                store.attempt(
                    job_id,
                    3,
                    phase="failed",
                    result=json.dumps(result),
                )

                store.status(
                    job_id,
                    "failed",
                    "Candidate verification failed and was not repairable",
                )
                return

            attempt_number = 3 + repair_number
            repair_dir = root / f"attempt-{attempt_number}"
            repair_dir.mkdir()

            diagnostic = verification_diagnostic(
                candidate_result,
                config["minimum_tests"],
            )

            (repair_dir / "diagnostic.txt").write_text(
                diagnostic + "\n",
                encoding="utf-8",
            )

            store.status(
                job_id,
                f"diagnosing-repair-{repair_number}",
            )

            store.attempt(
                job_id,
                attempt_number,
                phase=f"diagnosing-repair-{repair_number}",
                artifact_dir=str(repair_dir),
            )

            # A production edit cannot restore deleted test cases, and the
            # repair budget is only two attempts. Offer the already-selected
            # test files alone for this failure reason. This removes a
            # choice; it never adds edit authority.
            repair_candidates = selected_tests if short_count else selected

            prompt = repair_selection_prompt(
                spec["task"],
                plan,
                repair_candidates,
                diagnostic,
            )

            (repair_dir / "selection-prompt.txt").write_text(
                prompt,
                encoding="utf-8",
            )

            answer = generate_fn(
                prompt,
                lambda ident, n=attempt_number: store.attempt(
                    job_id,
                    n,
                    inference_job=ident,
                ),
            )

            (repair_dir / "selection-answer.txt").write_text(
                answer,
                encoding="utf-8",
            )

            repair_data = extract_json(answer)

            repair_target, repair_reason = validate_repair_selection(
                repair_data,
                repair_candidates,
            )

            repair_selection = {
                "repair_number": repair_number,
                "file": repair_target,
                "reason": repair_reason,
            }

            (repair_dir / "repair-selection.json").write_text(
                json.dumps(repair_selection, indent=2),
                encoding="utf-8",
            )

            repair_path = checkout / repair_target
            repair_source = repair_path.read_text(encoding="utf-8")

            related = related_context(
                checkout,
                repair_target,
                selected,
                tests,
            )

            prompt = repair_edit_prompt(
                spec["task"],
                plan,
                repair_target,
                repair_source,
                diagnostic,
                repair_reason,
                related,
            )

            (repair_dir / "repair-prompt.txt").write_text(
                prompt,
                encoding="utf-8",
            )

            store.status(
                job_id,
                f"repairing-{repair_number}",
            )

            store.attempt(
                job_id,
                attempt_number,
                phase=f"repairing-{repair_number}",
            )

            answer = generate_fn(
                prompt,
                lambda ident, n=attempt_number: store.attempt(
                    job_id,
                    n,
                    inference_job=ident,
                ),
            )

            (repair_dir / "repair-answer.txt").write_text(
                answer,
                encoding="utf-8",
            )

            repaired_source = extract_java(
                answer,
                repair_target,
            )

            if repaired_source == repair_source:
                raise ValueError(
                    f"Repair {repair_number} returned unchanged source"
                )

            repair_path.write_text(
                repaired_source,
                encoding="utf-8",
            )

            changed_after_repair = set(
                git(
                    checkout,
                    "diff",
                    "--name-only",
                    spec["base_commit"],
                ).splitlines()
            )

            if changed_after_repair != set(selected):
                raise ValueError(
                    "Repair changed repository scope outside "
                    "the originally selected files"
                )

            repaired_verification = (
                repair_dir / "candidate-verification"
            )
            repaired_verification.mkdir()

            def repaired_phase(state, rn=repair_number, n=attempt_number):
                store.status(
                    job_id,
                    f"repair-{rn}-{state}",
                )

                store.attempt(
                    job_id,
                    n,
                    phase=f"repair-{rn}-{state}",
                )

            previous_result = candidate_result

            candidate_result = verify_fn(
                checkout,
                paths,
                repaired_verification,
                config["minimum_tests"],
                repaired_phase,
            )

            repair_result = {
                "passed": candidate_result["passed"],
                "repair_number": repair_number,
                "repair_target": repair_target,
                "repair_reason": repair_reason,
                "diagnostic": diagnostic,
                "previous_verification": previous_result,
                "verification": candidate_result,
            }

            repair_history.append({
                "repair_number": repair_number,
                "repair_target": repair_target,
                "repair_reason": repair_reason,
                "passed": candidate_result["passed"],
            })

            (repair_dir / "result.json").write_text(
                json.dumps(repair_result, indent=2),
                encoding="utf-8",
            )

            store.attempt(
                job_id,
                attempt_number,
                phase=(
                    "passed"
                    if candidate_result["passed"]
                    else "failed"
                ),
                result=json.dumps(repair_result),
            )

        if not candidate_result["passed"]:
            store.status(
                job_id,
                "failed",
                "Candidate still failed after two bounded repairs",
            )
            return

        # ----- Stage 5: original-test regression verification -----
        saved_tests = {
            name: (checkout / name).read_bytes()
            for name in selected_tests
        }

        regression = attempt / "baseline-test-verification"
        regression.mkdir()

        try:
            for name in selected_tests:
                original = git(
                    checkout,
                    "show",
                    spec["base_commit"] + ":" + name,
                    raw=True,
                )
                (checkout / name).write_text(
                    original,
                    encoding="utf-8",
                )

            def regression_phase(state):
                store.status(job_id, "baseline-" + state)
                store.attempt(
                    job_id,
                    3,
                    phase="baseline-" + state,
                )

            regression_result = verify_fn(
                checkout,
                paths,
                regression,
                config["minimum_tests"],
                regression_phase,
            )

        finally:
            for name, data in saved_tests.items():
                (checkout / name).write_bytes(data)

        if not regression_result["passed"]:
            result = {
                "passed": False,
                "stage": "baseline-regression",
                "selected_files": selected,
                "candidate_verification": candidate_result,
                "baseline_verification": regression_result,
            }

            (attempt / "result.json").write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )

            store.attempt(
                job_id,
                3,
                phase="failed",
                result=json.dumps(result),
            )

            store.status(
                job_id,
                "failed",
                "Candidate breaks the original test suite",
            )
            return

        # If the model was allowed to change tests, it must have actually
        # added coverage - compared against the real original-suite count
        # that Stage 5 just measured (reverted test files, same production
        # code), not the possibly-stale configured minimum_tests floor. A
        # repository whose suite already exceeds that floor must still see
        # a genuine increase; missing or unusable count evidence fails
        # closed rather than silently falling back to the configured
        # minimum.
        if selected_tests:
            baseline_report = regression_result.get("junit") or {}
            candidate_report = candidate_result.get("junit") or {}
            baseline_count = baseline_report.get("tests")
            candidate_count = candidate_report.get("tests")

            if not isinstance(baseline_count, int) or not isinstance(candidate_count, int):
                result = {
                    "passed": False,
                    "stage": "coverage-check",
                    "selected_files": selected,
                    "candidate_verification": candidate_result,
                    "baseline_verification": regression_result,
                    "coverage_error": (
                        "Missing or unusable JUnit case-count evidence; "
                        "cannot verify added coverage"
                    ),
                }

                (attempt / "result.json").write_text(
                    json.dumps(result, indent=2),
                    encoding="utf-8",
                )

                store.attempt(
                    job_id,
                    3,
                    phase="failed",
                    result=json.dumps(result),
                )

                store.status(
                    job_id,
                    "failed",
                    "Missing or unusable test-count evidence for the added-coverage check",
                )
                return

            if candidate_count <= baseline_count:
                result = {
                    "passed": False,
                    "stage": "coverage-check",
                    "selected_files": selected,
                    "candidate_verification": candidate_result,
                    "baseline_verification": regression_result,
                    "coverage_error": (
                        f"Editable tests were changed but the JUnit case count "
                        f"did not increase over the original suite "
                        f"({candidate_count} vs {baseline_count})"
                    ),
                }

                (attempt / "result.json").write_text(
                    json.dumps(result, indent=2),
                    encoding="utf-8",
                )

                store.attempt(
                    job_id,
                    3,
                    phase="failed",
                    result=json.dumps(result),
                )

                store.status(
                    job_id,
                    "failed",
                    "Editable tests were changed but no new cases were added",
                )
                return

        # ----- Stage 6: behavioral-delta counterfactual -----
        # Everything above proves the candidate is self-consistent and does
        # not regress the original suite. None of it proves the production
        # edit was necessary: a cosmetic rewrite plus a test for behavior the
        # pinned base already satisfied passes every one of those gates.
        #
        # Build the hybrid state - pinned-base production plus the exact
        # candidate test sources - and run the candidate suite against it.
        # Candidate production must NOT reach this state; the manifest check
        # below enforces that against the verifier's own snapshot hashes.
        overlaid = hybrid_overlay_files(selected, tests)
        reverted = hybrid_reverted_files(selected, tests)

        overlaid_hashes = {
            name: hashlib.sha256((checkout / name).read_bytes()).hexdigest()
            for name in overlaid
        }

        saved_production = {
            name: (checkout / name).read_bytes()
            for name in reverted
        }

        hybrid_dir = attempt / "behavioral-delta-verification"
        hybrid_dir.mkdir()

        try:
            for name in reverted:
                original = git(
                    checkout,
                    "show",
                    spec["base_commit"] + ":" + name,
                    raw=True,
                )
                (checkout / name).write_text(
                    original,
                    encoding="utf-8",
                )

            def hybrid_phase(state):
                store.status(job_id, "behavioral-delta-" + state)
                store.attempt(
                    job_id,
                    3,
                    phase="behavioral-delta-" + state,
                )

            store.status(job_id, "behavioral-delta-preparing")

            hybrid_result = verify_fn(
                checkout,
                paths,
                hybrid_dir,
                config["minimum_tests"],
                hybrid_phase,
            )

        finally:
            for name, data in saved_production.items():
                (checkout / name).write_bytes(data)

        classification, delta_diagnostic = classify_behavioral_delta(
            hybrid_result,
            overlaid,
        )

        # original_hashes was taken at the base commit before any edit, so it
        # is exactly base content for every tracked path. The hybrid the
        # verifier actually built must equal that, with only the approved
        # candidate test files overlaid.
        expected_hybrid = dict(original_hashes)
        expected_hybrid.update(overlaid_hashes)

        hybrid_compile = hybrid_result.get("compile") or {}
        hybrid_tests = hybrid_result.get("tests") or {}

        for name, log in (
            ("compile.log", hybrid_compile.get("log")),
            ("tests.log", hybrid_tests.get("log")),
        ):
            (hybrid_dir / name).write_text(
                str(log or ""),
                encoding="utf-8",
            )

        behavioral_delta = {
            "stage": "behavioral-delta",
            "profile": PROFILE,
            "workflow_id": job_id,
            "classification": classification,
            "diagnostic": delta_diagnostic,
            "distinguishing": classification in DISTINGUISHING_CLASSIFICATIONS,
            "base_commit": spec["base_commit"],
            "branch": branch,
            "manifest": {
                "base_commit": spec["base_commit"],
                "overlaid_candidate_test_files": overlaid_hashes,
                "base_production_files": list(reverted),
                "expected_snapshot_sha256": expected_hybrid,
                "hybrid_snapshot_sha256": hybrid_result.get("snapshot_sha256"),
            },
            "image_id": hybrid_result.get("image_id"),
            "compile_exit_code": hybrid_compile.get("exit_code"),
            "tests_exit_code": hybrid_tests.get("exit_code"),
            "junit": hybrid_result.get("junit"),
            "artifact_dir": str(hybrid_dir),
            "verification": hybrid_result,
        }

        (attempt / "behavioral-delta.json").write_text(
            json.dumps(behavioral_delta, indent=2),
            encoding="utf-8",
        )

        if hybrid_result.get("snapshot_sha256") != expected_hybrid:
            raise ValueError(
                "Hybrid counterfactual state is not pinned-base production "
                "plus candidate tests"
            )

        if classification == NO_BEHAVIORAL_DELTA:
            result = {
                "passed": False,
                "stage": "behavioral-delta",
                "selected_files": selected,
                "candidate_verification": candidate_result,
                "baseline_verification": regression_result,
                "behavioral_delta": behavioral_delta,
            }

            (attempt / "result.json").write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )

            store.attempt(
                job_id,
                3,
                phase=REJECTED_NO_BEHAVIORAL_DELTA,
                result=json.dumps(result),
            )

            # A distinct terminal state, not a generic failure: Workflow
            # 26-style outcomes have to be queryable on their own. Nothing is
            # committed and nothing is publishable from here.
            store.status(
                job_id,
                REJECTED_NO_BEHAVIORAL_DELTA,
                delta_diagnostic,
            )
            return

        if classification not in DISTINGUISHING_CLASSIFICATIONS:
            # The hybrid environment broke. That is not evidence either way,
            # so the candidate stops here under the workflow's normal
            # infrastructure-failure semantics.
            result = {
                "passed": False,
                "stage": "behavioral-delta",
                "selected_files": selected,
                "candidate_verification": candidate_result,
                "baseline_verification": regression_result,
                "behavioral_delta": behavioral_delta,
            }

            (attempt / "result.json").write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )

            store.attempt(
                job_id,
                3,
                phase="failed",
                result=json.dumps(result),
            )

            store.status(
                job_id,
                "failed",
                "Behavioral-delta verification could not produce usable "
                "evidence: " + delta_diagnostic,
            )
            return

        # ----- Stage 7: deterministic review -----
        patch = git(
            checkout,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            spec["base_commit"],
            raw=True,
        )

        (attempt / "diff.patch").write_text(
            patch,
            encoding="utf-8",
        )

        reviews = {}

        for name in selected:
            source = (checkout / name).read_text(encoding="utf-8")
            reviews[name] = review_source(source, patch)

        review_passed = all(
            review["status"] == "passed"
            for review in reviews.values()
        )

        review_result = {
            "status": "passed" if review_passed else "changes_requested",
            "files": reviews,
            "scope": (
                "Deterministic targeted text checks on every changed Java file; "
                "Gradle/JUnit remains the behavioral verifier."
            ),
        }

        (attempt / "review.json").write_text(
            json.dumps(review_result, indent=2),
            encoding="utf-8",
        )

        if not review_passed:
            result = {
                "passed": False,
                "stage": "review",
                "selected_files": selected,
                "candidate_verification": candidate_result,
                "baseline_verification": regression_result,
                "behavioral_delta": behavioral_delta,
                "review": review_result,
            }

            (attempt / "result.json").write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )

            store.attempt(
                job_id,
                3,
                phase="failed",
                result=json.dumps(result),
            )

            store.status(
                job_id,
                "failed",
                "Deterministic review requested changes",
            )
            return

        # Verification used snapshots. Make sure nothing mutated afterward.
        current_hashes = {
            name: hashlib.sha256(
                (checkout / name).read_bytes()
            ).hexdigest()
            for name in paths
        }

        for name in paths:
            if name not in selected and current_hashes[name] != original_hashes[name]:
                raise ValueError(
                    "Unselected repository content changed during execution"
                )

        candidate_hashes = candidate_result["snapshot_sha256"]

        if current_hashes != candidate_hashes:
            raise ValueError(
                "Checkout changed after candidate verification"
            )

        # ----- Stage 8: verified commit -----
        git(checkout, "add", "--", *selected)

        git(
            checkout,
            "-c",
            "user.name=NullCode",
            "-c",
            "user.email=nullcode@localhost",
            "commit",
            "-m",
            f"Implement verified repository task {job_id}",
        )

        repository = {
            "profile": PROFILE,
            "checkout": str(checkout),
            "branch": branch,
            "base_commit": spec["base_commit"],
            "commit": git(checkout, "rev-parse", "HEAD"),
            "editable_files": selected,
            "diff_path": str(attempt / "diff.patch"),
            "plan_path": str(root / "plan.json"),
        }

        (root / "repository.json").write_text(
            json.dumps(repository, indent=2),
            encoding="utf-8",
        )

        result = {
            "passed": True,
            "profile": PROFILE,
            "selected_files": selected,
            "plan": plan,
            "candidate_verification": candidate_result,
            "baseline_verification": regression_result,
            "behavioral_delta": behavioral_delta,
            "review": review_result,
            "repository": repository,
        }

        (attempt / "result.json").write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )

        store.attempt(
            job_id,
            3,
            phase="passed",
            result=json.dumps(result),
        )

        store.status(job_id, "succeeded")

    except Exception as error:
        root = artifacts / f"workflow-{job_id}"

        if (root / "attempt-5").exists():
            number = 5
        elif (root / "attempt-4").exists():
            number = 4
        elif (root / "attempt-3").exists():
            number = 3
        elif (root / "attempt-2").exists():
            number = 2
        else:
            number = 1

        try:
            store.attempt(
                job_id,
                number,
                phase="error",
                result=json.dumps({"error": str(error)}),
            )
        except Exception:
            pass

        store.status(job_id, "failed", str(error))
