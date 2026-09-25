"""Milestone 7B: planned, bounded multi-file Java repository execution."""

import contextlib
import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import time

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


def context_for_plan(checkout, selected, budget=1100):
    # Keep total repository evidence near 1100 bytes so the complete planning
    # prompt stays beneath the controller's 2000-byte limit. Callers with a
    # longer instruction block pass a smaller budget rather than overflowing.
    per_file = max(120, budget // len(selected))

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


def hybrid_manifest(original_hashes, overlaid_hashes):
    """The snapshot the hybrid counterfactual must have.

    ``original_hashes`` is taken at the base commit before any edit, so it is
    base content for every tracked path; the only permitted departure is the
    approved candidate test files overlaid on top. The verifier's own snapshot
    is compared against this, which is what makes candidate production unable
    to enter the counterfactual silently.
    """
    expected = dict(original_hashes)
    expected.update(overlaid_hashes)
    return expected


@contextlib.contextmanager
def pinned_base_production(checkout, base_commit, reverted):
    """Run a block with the selected production files at base-commit content.

    The candidate's production sources are restored on the way out whatever
    happens inside, so a failure in the counterfactual cannot leave the
    checkout holding base content that would later be committed as if it were
    the candidate.
    """
    saved = {name: (checkout / name).read_bytes() for name in reverted}

    try:
        for name in reverted:
            (checkout / name).write_text(
                git(checkout, "show", base_commit + ":" + name, raw=True),
                encoding="utf-8",
            )

        yield

    finally:
        for name, data in saved.items():
            (checkout / name).write_bytes(data)


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

# ---------------------------------------------------------------------------
# Typed repair-target routing (Milestone 7B.2)
# ---------------------------------------------------------------------------
# The repair-selection reply names a fault domain as well as a file, and the
# two must agree. Workflow 32 replied with a reason blaming the test
# expectation and a target naming the production file; nothing deterministic
# could see that, because the diagnosis lived only in prose.
#
# The validator checks agreement between two model claims. It cannot check
# that either claim is right, it never parses `reason`, and it never corrects
# a contradiction: a contradictory or malformed reply fails the workflow.
# There is no second routing call.
FAULT_DOMAIN_PRODUCTION = "production"
FAULT_DOMAIN_TEST = "test"
FAULT_DOMAINS = (FAULT_DOMAIN_PRODUCTION, FAULT_DOMAIN_TEST)


def repair_route_domains(candidates, production, tests):
    """Offered repair candidates grouped by fault domain.

    Membership comes from the approved editable_files / editable_test_files
    lists the workflow already validated the selection against - never from
    a path heuristic. Only offered candidates appear, so grouping can remove
    choices but never add one.
    """
    return {
        FAULT_DOMAIN_PRODUCTION: [n for n in candidates if n in production],
        FAULT_DOMAIN_TEST: [n for n in candidates if n in tests],
    }


def repair_selection_prompt(task, plan, candidates, diagnostic, production, tests):
    # ADVISORY ONLY. The prompt asks for a fault domain and a file listed
    # under it; validate_repair_selection() is what enforces the agreement.
    domains = repair_route_domains(candidates, production, tests)
    prompt = (
        "A Java change failed verification. Pick ONE listed file to repair; "
        "do not write code. fault_domain is \"production\" if the "
        "implementation is wrong, \"test\" if a test expectation is wrong; "
        "file must be listed under that domain. Reason literally from the "
        "task; a JUnit expected value may itself be wrong. For 'longer than "
        "N', use strict > N. Return JSON only: "
        '{"fault_domain":"production|test","file":"listed/path.java",'
        '"reason":"short evidence-based explanation"}.\n'
        f"Task: {task}\n"
        f"Production files: {json.dumps(domains[FAULT_DOMAIN_PRODUCTION])}\n"
        f"Test files: {json.dumps(domains[FAULT_DOMAIN_TEST])}\n"
        f"Plan summary: {str(plan.get('summary', ''))[:220]}\n"
        "Verification failure:\n"
        f"{diagnostic}"
    )

    if len(prompt.encode()) > 2000:
        raise ValueError("Repair-selection prompt exceeds controller limit")

    return prompt


def validate_repair_selection(data, candidates, production, tests,
                              required_domain=None, repair_number=1):
    """Deterministic repair routing. Returns (fault_domain, file, reason).

    Rejects, in order: a non-object reply; a missing, non-string or unknown
    fault_domain (exact match only - no case folding, trimming, aliases or
    default); a file outside the offered candidates; a domain other than the
    one this failure class requires; a file not listed under the stated
    domain; an empty reason. Nothing is inferred and nothing is corrected.
    """
    if not isinstance(data, dict):
        raise ValueError("Repair selection must be a JSON object")

    if "fault_domain" not in data:
        raise ValueError(
            f"Repair {repair_number} routing requires fault_domain"
        )

    domain = data["fault_domain"]

    if type(domain) is not str or domain not in FAULT_DOMAINS:
        raise ValueError(
            f"Repair {repair_number} routing has invalid fault_domain: "
            f"{json.dumps(domain)}"
        )

    target = data.get("file")
    reason = data.get("reason")

    if type(target) is not str or target not in candidates:
        raise ValueError(
            f"Repair selected an unapproved file: {target}"
        )

    if required_domain is not None and domain != required_domain:
        raise ValueError(
            f"Repair {repair_number} routing is contradictory: this failure "
            f"requires fault_domain '{required_domain}'"
        )

    if target not in repair_route_domains(candidates, production, tests)[domain]:
        raise ValueError(
            f"Repair {repair_number} routing is contradictory: fault_domain "
            f"'{domain}' but {target} is not a selected {domain} file"
        )

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Repair selection requires a reason")

    return domain, target, reason.strip()


# ---------------------------------------------------------------------------
# Evidence strength (Milestone 7B.1)
# ---------------------------------------------------------------------------
# The two distinguishing classifications are NOT equally strong evidence and
# must never be recorded, summarized or published as if they were.
#
#   distinguishing-test-failure        -> behavioral
#       The candidate suite compiled against pinned-base production and a
#       case FAILED there. Runtime behavior was observed to differ.
#
#   distinguishing-api-compile-failure -> structural
#       The candidate suite could not be compiled against pinned-base
#       production at all: it references API the base does not declare. The
#       API surface demonstrably differs, but no assertion ever executed
#       against the base, so nothing about runtime behavior was observed.
#
# Structural evidence is never upgraded to behavioral certainty. It says only
# what it proves: the base cannot even build the candidate's tests.
EVIDENCE_BEHAVIORAL = "behavioral"
EVIDENCE_STRUCTURAL = "structural"
EVIDENCE_NONE = "none"

EVIDENCE_LEVELS = {
    DISTINGUISHING_TEST_FAILURE: EVIDENCE_BEHAVIORAL,
    DISTINGUISHING_API_COMPILE_FAILURE: EVIDENCE_STRUCTURAL,
    NO_BEHAVIORAL_DELTA: EVIDENCE_NONE,
    INFRASTRUCTURE_FAILURE: EVIDENCE_NONE,
}

EVIDENCE_SUMMARIES = {
    EVIDENCE_BEHAVIORAL: (
        "Behavioral evidence: the candidate suite ran against pinned-base "
        "production and a case failed there, so observed runtime behavior "
        "differs between base and candidate."
    ),
    EVIDENCE_STRUCTURAL: (
        "Structural evidence only: the candidate suite could not be compiled "
        "against pinned-base production, so no assertion executed against the "
        "base. This proves the API surface differs. It does NOT prove that "
        "runtime behavior differs."
    ),
    EVIDENCE_NONE: (
        "No novelty evidence: nothing here distinguishes candidate production "
        "from pinned-base production."
    ),
}


def evidence_level(classification):
    """Evidence strength for a behavioral-delta classification.

    An unrecognized classification is deliberately ``none``: an outcome the
    classifier does not positively explain is never promoted to evidence.
    """
    return EVIDENCE_LEVELS.get(classification, EVIDENCE_NONE)


def evidence_summary(classification):
    """Human-readable statement of exactly what the evidence does prove."""
    return EVIDENCE_SUMMARIES[evidence_level(classification)]


# ---------------------------------------------------------------------------
# API compile-failure policy
# ---------------------------------------------------------------------------
# Three policy shapes were run against deterministic fixtures before this
# module chose one; the experiment is
# tests/test_behavioral_delta_policy.py and the decision record is
# docs/milestones/MILESTONE-7B-1.md.
#
#   A  allow structural evidence            -> continue, evidence_level=structural
#   B  require additional robust evidence   -> no such check exists (see below)
#   C  structural evidence is insufficient  -> reject
#
# Measured outcome: the hybrid evidence produced by a legitimate new API
# (Fixture D3) and by a deliberately trivial new API (Fixture D4) is the same
# class of evidence - ":compileTestJava FAILED" with `cannot find symbol` at
# candidate test locations. Nothing in the counterfactual, the JUnit reports,
# the executed-case counts or the deterministic review separates them, because
# a compile failure aborts the suite before any assertion runs. Policy C would
# therefore reject every legitimate new-API task (a normal, common task shape
# for this profile) to exclude the weak ones; Policy B's "additional robust
# evidence" would have to come from parsing Java/JUnit sources, which this
# milestone explicitly excludes and which would be brittle rather than robust.
#
# Policy A is selected, and the weakness it admits is handled by recording it
# honestly instead of hiding it: evidence_level=structural travels with the
# classification into the artifact, the workflow result and the draft-PR body,
# and the human acceptance boundary is unchanged.
POLICY_ALLOW_STRUCTURAL = "A-allow-structural"
POLICY_REQUIRE_ADDITIONAL = "B-require-additional-evidence"
POLICY_REJECT_STRUCTURAL = "C-structural-insufficient"

CONTINUE = "continue"
REJECT = "reject"
FAIL_CLOSED = "fail-closed"
UNAVAILABLE = "unavailable"

API_COMPILE_POLICIES = {
    POLICY_ALLOW_STRUCTURAL: CONTINUE,
    # No robust additional check is available from existing structured
    # information; see the comment above. The experiment records this rather
    # than pretending an implementable check exists.
    POLICY_REQUIRE_ADDITIONAL: UNAVAILABLE,
    POLICY_REJECT_STRUCTURAL: REJECT,
}

# The policy this workflow actually enforces.
API_COMPILE_POLICY = POLICY_ALLOW_STRUCTURAL


def policy_decision(classification, policy=API_COMPILE_POLICY):
    """What the workflow does with a behavioral-delta classification.

    The recorded policy experiment calls this with each candidate policy and
    the workflow calls it with the selected one, so the shipped behavior and
    the evidence used to choose it cannot drift apart.
    """
    if classification == DISTINGUISHING_TEST_FAILURE:
        return CONTINUE

    if classification == DISTINGUISHING_API_COMPILE_FAILURE:
        if policy not in API_COMPILE_POLICIES:
            raise ValueError("Unknown API compile-failure policy: " + str(policy))
        return API_COMPILE_POLICIES[policy]

    if classification == NO_BEHAVIORAL_DELTA:
        return REJECT

    # Infrastructure failure and anything unrecognized.
    return FAIL_CLOSED


# ---------------------------------------------------------------------------
# Semantic re-plan (Milestone 7B.1, Deliverable B)
# ---------------------------------------------------------------------------
# A no-behavioral-delta candidate is not a broken implementation: it compiled,
# its tests passed, it did not regress the suite and it added coverage. What
# failed is the TASK INTERPRETATION - the model chose behavior the repository
# already provides. That is a different failure from the ones the ordinary
# repair loop handles, so it gets its own, separately bounded budget. The
# ordinary repair budget (2) is untouched and the two never share an
# allowance.
#
# The diagnostic prompt below is ADVISORY ONLY. Nothing in it is a safety
# control: a re-planned candidate is a new candidate and is re-verified from
# scratch by the same deterministic gates as the first one - approved file
# scope, real-edit and scope checks, candidate compile and test, the minimum
# executed-test floor, the baseline regression, the added-coverage comparison,
# the behavioral-delta counterfactual with its snapshot-hash manifest, and the
# deterministic review. No safety property depends on the model obeying prose.
SEMANTIC_REPLAN_BUDGET = 1

# Attempt numbers per candidate generation: the first candidate uses 2
# (planning), 3 (editing and verification) and 4-5 (ordinary repairs); the
# semantic diagnosis that starts generation 1 uses 6, and that generation's
# planning, editing and repairs use 7, 8 and 9-10.
REPLAN_ATTEMPT_STRIDE = 5

SEMANTIC_REPLAN_ADVISORY = (
    "Do not widen file scope. Do not weaken or delete tests. "
    "Do not change build or repository configuration."
)

SEMANTIC_REPLAN_DIAGNOSING = "behavioral-delta-diagnosing"
SEMANTIC_REPLAN_REPLANNING = "behavioral-delta-replanning"
# The candidate generation that produced no delta is recorded under its own
# phase so the history shows a re-interpreted task rather than a repair.
SEMANTIC_REPLAN_SUPERSEDED = "superseded-no-behavioral-delta"


def budgeted_context(checkout, selected, prefix, limit=2000, floor=200):
    """Repository evidence sized to whatever the prompt prefix leaves free.

    Prompt construction always fails loudly rather than silently truncating
    the instruction block, exactly as the other prompt builders do.
    """
    budget = limit - len(prefix.encode()) - 8

    if budget < floor:
        raise ValueError(
            "Semantic re-plan instructions leave no room for repository "
            "evidence within the controller limit"
        )

    return context_for_plan(checkout, selected, budget)


def semantic_diagnosis_prompt(task, selected, checkout,
                              diagnostic=NO_BEHAVIORAL_DELTA_DIAGNOSTIC):
    """Ask the model to reinterpret the task. Advisory; enforces nothing.

    The sources shown are the pinned-base sources, which is what the candidate
    failed to improve on. The reply is validated for shape only.
    """
    prefix = (
        "A Java change was rejected: its tests pass, but they ALSO pass "
        "against the unchanged repository below, so it demonstrated no new "
        "behavior. Do not write code. Name ONE behavior improvement the "
        "unchanged sources do NOT already provide.\n"
        + SEMANTIC_REPLAN_ADVISORY + "\n"
        'Return JSON only: {"diagnosis":"why the last attempt showed no new '
        'behavior","behavior":"the different behavior to implement"}.\n'
        f"Task: {task}\n"
        f"Files you may change: {json.dumps(selected)}\n"
        f"Evidence: {diagnostic}\n"
        "Unchanged sources:\n"
    )

    prompt = prefix + budgeted_context(checkout, selected, prefix)

    if len(prompt.encode()) > 2000:
        raise ValueError("Semantic diagnosis prompt exceeds controller limit")

    return prompt


def validate_semantic_diagnosis(data):
    """Shape check for the semantic diagnosis reply.

    This is not a safety gate and cannot be one: the behavior string only
    steers the next planning prompt. Everything that decides whether the
    re-planned candidate is acceptable is deterministic and runs afterwards.
    """
    if not isinstance(data, dict):
        raise ValueError("Semantic diagnosis must be a JSON object")

    values = {}

    for field, limit in (("diagnosis", 400), ("behavior", 300)):
        value = data.get(field)

        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Semantic diagnosis requires a {field}")

        # Advisory text: collapse whitespace and bound it so it cannot crowd
        # the next prompt out of the controller's byte limit.
        values[field] = " ".join(value.split()).encode()[:limit].decode(
            errors="ignore"
        )

    return values["diagnosis"], values["behavior"]


def semantic_replan_planning_prompt(task, selected, behavior, checkout):
    """Planning prompt for a re-planned candidate.

    Same JSON contract and same selected-file scope as ordinary planning: the
    re-plan changes what the model aims at, never what it is allowed to touch.
    """
    behavior = " ".join(str(behavior).split())

    if not behavior:
        raise ValueError("Semantic re-plan requires a chosen behavior")

    prefix = (
        "You are planning a small Java repository change. Do not write code. "
        "Use only the supplied repository evidence. "
        "Return JSON only with this shape: "
        '{"summary":"...",'
        '"files":[{"path":"...","reason":"..."}],'
        '"steps":["..."],'
        '"risks":["..."]}. '
        "Every file path must be one of the selected files.\n"
        "The previous attempt changed nothing the repository did not already "
        "do. Plan THIS behavior instead: " + behavior + "\n"
        + SEMANTIC_REPLAN_ADVISORY + "\n"
        f"Task: {task}\n"
        f"Selected files: {json.dumps(selected)}\n"
        "Repository evidence:\n"
    )

    prompt = prefix + budgeted_context(checkout, selected, prefix)

    if len(prompt.encode()) > 2000:
        raise ValueError("Semantic re-plan planning prompt exceeds controller limit")

    return prompt


def reset_to_base(checkout, base_commit, selected, paths, original_hashes):
    """Discard a superseded candidate: every selected file back to base bytes.

    A re-planned candidate must not inherit anything from the candidate it
    replaces - not its sources, and not its verification evidence. The working
    tree is returned to pinned-base content and the result is checked against
    the hashes taken before the first edit, so a partial reset fails closed
    instead of producing a hybrid of two candidates.
    """
    git(checkout, "checkout", base_commit, "--", *selected)

    restored = {
        name: hashlib.sha256((checkout / name).read_bytes()).hexdigest()
        for name in paths
    }

    if restored != original_hashes or git(
        checkout, "diff", "--name-only", base_commit
    ):
        raise ValueError(
            "Checkout could not be returned to pinned-base content before "
            "the semantic re-plan"
        )


# ---------------------------------------------------------------------------
# Stage timing (Milestone 7B.1, Deliverable C)
# ---------------------------------------------------------------------------
# Measurement only. No threshold, budget or optimization is derived from these
# numbers in this milestone: there is not yet enough evidence to justify one.


class StageTimer:
    """Monotonic-clock stage accounting for one workflow run.

    time.monotonic_ns() cannot jump backwards when the wall clock is adjusted,
    which matters because these durations are the only evidence a future
    decision about counterfactual cost will have.
    """

    def __init__(self):
        self._started = time.monotonic_ns()
        self._stages = {}

    def record_ns(self, name, elapsed):
        self._stages[name] = self._stages.get(name, 0) + max(0, elapsed)

    def merge_ns(self, prefix, stages):
        for name, elapsed in stages.items():
            self.record_ns(f"{prefix}_{name}", elapsed)

    @contextlib.contextmanager
    def stage(self, name):
        started = time.monotonic_ns()
        try:
            yield
        finally:
            self.record_ns(name, time.monotonic_ns() - started)

    def elapsed_ms(self):
        return (time.monotonic_ns() - self._started) // 1_000_000

    def snapshot(self, **extra):
        timings = {
            name: elapsed // 1_000_000
            for name, elapsed in sorted(self._stages.items())
        }
        timings.update(extra)
        timings["workflow_total"] = self.elapsed_ms()
        return timings


class VerificationTimer:
    """Split one verify() call into preparation, compilation and test time.

    The shared Gradle verifier already reports its own progress through the
    phase callback: everything before 'compiling' is snapshot and container
    preparation, 'compiling' to 'testing' is compilation, and the remainder is
    test execution. Deriving the split from those transitions needs no change
    to the shared verifier, which other profiles also use.
    """

    def __init__(self, phase, prepared_ns=0):
        self._phase = phase
        self._started = time.monotonic_ns()
        self._mark = self._started
        self._current = "prepare"
        self.stages = {"prepare": prepared_ns, "compile": 0, "test": 0, "total": 0}

    def _advance(self, name, now):
        self.stages[self._current] += max(0, now - self._mark)
        self._current = name
        self._mark = now

    def phase(self, state):
        now = time.monotonic_ns()

        if state == "compiling":
            self._advance("compile", now)
        elif state == "testing":
            self._advance("test", now)

        self._phase(state)

    def finish(self):
        """Close the open stage. A run that never reached 'testing' - a
        compile failure, for instance - attributes its remaining time to the
        stage that was actually running, never to one that never started."""
        now = time.monotonic_ns()
        self.stages[self._current] += max(0, now - self._mark)
        self._mark = now
        self.stages["total"] = (
            self.stages["prepare"] + self.stages["compile"] + self.stages["test"]
        )
        return self.stages


# ---------------------------------------------------------------------------
# Test-side logic observation (Milestone 7B.1, Deliverable E)
# ---------------------------------------------------------------------------


DIFF_FILE = re.compile(r"^\+\+\+ b/(.+)$")


def diff_line_counts(patch):
    """Added and removed line counts per file from a unified diff.

    Pure text accounting over the diff the workflow already produces. It reads
    no Java syntax and makes no judgement.
    """
    added = {}
    removed = {}
    current = None

    for line in str(patch or "").splitlines():
        match = DIFF_FILE.match(line)

        if match:
            current = match.group(1)
            added.setdefault(current, 0)
            removed.setdefault(current, 0)
            continue

        if current is None or line.startswith("+++") or line.startswith("---"):
            continue

        if line.startswith("+"):
            added[current] += 1
        elif line.startswith("-"):
            removed[current] += 1

    return added, removed


def test_side_logic_observation(patch, selected, tests):
    """Advisory statistics on where the candidate's new code actually landed.

    NOT a gate, and deliberately not a rule: test helpers and fixtures are
    legitimate, and a blanket prohibition on them would reject honest work.
    What this records is how much of the change is test-side, so a human
    reviewer can see at a glance when the interesting code lives in the test
    file rather than in production. Nothing in the workflow branches on it.

    The counterfactual already bounds - but does not eliminate - the related
    risk: candidate test sources are byte-identical in the candidate run and
    in the hybrid run, so any behavior difference between the two is caused by
    production content. It cannot show that the assertions are meaningful, or
    that an expected value was not computed by logic copied out of production.
    See MILESTONE-7B-1.md, "Test-side logic".
    """
    added, removed = diff_line_counts(patch)

    def total(counter, group):
        return sum(
            count for name, count in counter.items()
            if (name in tests) == group and name in selected
        )

    test_added = total(added, True)
    production_added = total(added, False)
    combined = test_added + production_added

    return {
        "advisory": True,
        "gating": False,
        "added_lines": {"production": production_added, "test": test_added},
        "removed_lines": {
            "production": total(removed, False),
            "test": total(removed, True),
        },
        "test_added_line_share": (
            round(test_added / combined, 3) if combined else None
        ),
        "note": (
            "Advisory diff statistics only. Test helpers and fixtures are "
            "legitimate and nothing is rejected on these numbers. They exist "
            "so a reviewer can see when new logic is concentrated in test "
            "sources; the workflow cannot prove that all exercised behavior "
            "lives in production code."
        ),
    }


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds"
    )


def semantic_replan_outcome(classification, attempt, budget=SEMANTIC_REPLAN_BUDGET):
    """How one candidate generation relates to the semantic re-plan budget.

    ``attempt`` is the generation that produced this classification: 0 is the
    original candidate, 1 the candidate a semantic re-plan produced.
    """
    if classification == NO_BEHAVIORAL_DELTA:
        if attempt < budget:
            return "replan-triggered"
        return "replan-exhausted-no-delta"

    if classification in DISTINGUISHING_CLASSIFICATIONS:
        if attempt:
            return "replan-produced-distinguishing-evidence"
        return "not-required"

    return "not-applicable"


def highest_attempt_number(root):
    """The furthest attempt directory a run actually created."""
    numbers = [1]

    if root.is_dir():
        for entry in root.glob("attempt-*"):
            suffix = entry.name.split("-", 1)[1]

            if entry.is_dir() and suffix.isdigit():
                numbers.append(int(suffix))

    return max(numbers)


TIMING_NOTE = (
    "Measurement only. No performance threshold, budget or optimization is "
    "derived from these numbers: current evidence is insufficient to justify "
    "one. They exist so a future decision about counterfactual and re-plan "
    "cost can be made from observed data."
)


def record_run_evidence(root, store, job_id, timer, replan):
    """Persist stage timings and semantic re-plan telemetry for a finished run.

    Written for every terminal state, success or failure alike. A re-plan
    budget of 1 and the cost of the counterfactual are initial policy, not
    settled truth; revising either needs observed success rates and durations
    rather than intuition, so the evidence is recorded even when the run ends
    badly.
    """
    if not root.is_dir():
        return

    try:
        status = store.show(job_id).get("status")
    except Exception:
        status = None

    record = replan["record"]

    if record is not None:
        started = replan.get("started_ns")
        # Everything from the semantic diagnosis to the end of the run is
        # latency the re-plan added.
        record["duration_ms"] = (
            (time.monotonic_ns() - started) // 1_000_000
            if started
            else 0
        )
        record["final_result"] = status
        record["reached_succeeded"] = status == "succeeded"

    (root / "semantic-replan.json").write_text(
        json.dumps(
            {
                "profile": PROFILE,
                "workflow_id": job_id,
                "final_result": status,
                "semantic_replan_budget": SEMANTIC_REPLAN_BUDGET,
                "semantic_replan_attempts": replan["attempts_used"],
                "semantic_replan": record,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (root / "timing.json").write_text(
        json.dumps(
            {
                "profile": PROFILE,
                "workflow_id": job_id,
                "final_result": status,
                "clock": "time.monotonic_ns",
                "timing_ms": timer.snapshot(
                    semantic_replan=(record or {}).get("duration_ms", 0),
                ),
                "note": TIMING_NOTE,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run_job(store, job, generate_fn=generate, verify_fn=verify_gradle, artifacts=JOBS):
    job_id = job["id"]
    root = artifacts / f"workflow-{job_id}"

    # Measurement only: nothing below branches on a duration.
    timer = StageTimer()

    # Semantic re-plan accounting, kept deliberately separate from the
    # ordinary repair budget so neither can spend the other's allowance.
    replan = {
        "budget": SEMANTIC_REPLAN_BUDGET,
        "attempts_used": 0,
        "started_ns": None,
        "record": None,
    }

    try:
        spec = json.loads(job["repo_spec"])

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

        # Stages 2 to 8 run once per candidate generation. A generation that
        # produces no behavioral delta may be superseded by one bounded
        # semantic re-plan (SEMANTIC_REPLAN_BUDGET); the replacement is a new
        # candidate that re-runs every gate from planning onwards. Only the
        # immutable, already-established evidence - the clone, the pinned base
        # commit, the approved scope and the selection made above - carries
        # over.
        for replan_attempt in range(SEMANTIC_REPLAN_BUDGET + 1):
            offset = REPLAN_ATTEMPT_STRIDE * replan_attempt
            planning_number = 2 + offset
            edit_number = 3 + offset

            if replan_attempt:
                # Defence in depth: the superseded candidate was already
                # discarded before its diagnosis ran, so anything other than
                # pinned-base content here means the workspace is not what
                # the next candidate must start from.
                reset_to_base(
                    checkout,
                    spec["base_commit"],
                    selected,
                    paths,
                    original_hashes,
                )

            # ----- Stage 2: planning -----
            attempt = root / f"attempt-{planning_number}"
            attempt.mkdir()

            if replan_attempt:
                # Same JSON contract and the same approved scope as ordinary
                # planning; only the behavior the model aims at changed. The plan
                # is still validated against the originally selected files.
                prompt = semantic_replan_planning_prompt(
                    spec["task"],
                    selected,
                    replan["record"]["behavior"],
                    checkout,
                )
            else:
                prompt = planning_prompt(
                    spec["task"],
                    selected,
                    context_for_plan(checkout, selected),
                )

            (attempt / "prompt.txt").write_text(prompt, encoding="utf-8")

            store.status(job_id, "planning")
            store.attempt(
                job_id,
                planning_number,
                phase="planning",
                artifact_dir=str(attempt),
            )

            answer = generate_fn(
                prompt,
                lambda ident: store.attempt(
                    job_id,
                    planning_number,
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
                planning_number,
                phase="passed",
                result=json.dumps(plan_result),
            )

            # ----- Stage 3: edit selected files -----
            attempt = root / f"attempt-{edit_number}"
            attempt.mkdir()

            store.status(job_id, "editing")
            store.attempt(
                job_id,
                edit_number,
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
                        edit_number,
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
                store.attempt(job_id, edit_number, phase=state)

            # Measurement only. The phase callback the shared Gradle verifier
            # already drives is what separates preparation, compilation and test
            # execution, so no other profile's verifier contract changes.
            candidate_clock = VerificationTimer(candidate_phase)

            candidate_result = verify_fn(
                checkout,
                paths,
                verification,
                config["minimum_tests"],
                candidate_clock.phase,
            )

            timer.record_ns(
                "candidate_verification",
                candidate_clock.finish()["total"],
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
                        edit_number,
                        phase="failed",
                        result=json.dumps(result),
                    )

                    store.status(
                        job_id,
                        "failed",
                        "Candidate verification failed and was not repairable",
                    )
                    return

                attempt_number = edit_number + repair_number
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
                    production,
                    tests,
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

                # Routing evidence is written before validation, so a rejected
                # reply is preserved exactly as the model gave it. A rejection
                # ends the workflow: no reselection, no repair edit.
                routing = {
                    "repair_number": repair_number,
                    "offered": repair_route_domains(
                        repair_candidates, production, tests,
                    ),
                    "required_domain": (
                        FAULT_DOMAIN_TEST if short_count else None
                    ),
                    "response": None,
                    "accepted": False,
                    "error": None,
                }

                try:
                    repair_data = extract_json(answer)
                    routing["response"] = repair_data

                    (
                        repair_domain,
                        repair_target,
                        repair_reason,
                    ) = validate_repair_selection(
                        repair_data,
                        repair_candidates,
                        production,
                        tests,
                        required_domain=routing["required_domain"],
                        repair_number=repair_number,
                    )
                except Exception as error:
                    routing["error"] = str(error)
                    raise
                else:
                    routing["accepted"] = True
                finally:
                    (repair_dir / "repair-routing.json").write_text(
                        json.dumps(routing, indent=2),
                        encoding="utf-8",
                    )

                repair_selection = {
                    "repair_number": repair_number,
                    "fault_domain": repair_domain,
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

                repaired_clock = VerificationTimer(repaired_phase)

                candidate_result = verify_fn(
                    checkout,
                    paths,
                    repaired_verification,
                    config["minimum_tests"],
                    repaired_clock.phase,
                )

                # Repair rounds are candidate verification too; their cost
                # accumulates into the same measured stage.
                timer.record_ns(
                    "candidate_verification",
                    repaired_clock.finish()["total"],
                )

                repair_result = {
                    "passed": candidate_result["passed"],
                    "repair_number": repair_number,
                    "fault_domain": repair_domain,
                    "repair_target": repair_target,
                    "repair_reason": repair_reason,
                    "diagnostic": diagnostic,
                    "previous_verification": previous_result,
                    "verification": candidate_result,
                }

                repair_history.append({
                    "repair_number": repair_number,
                    "fault_domain": repair_domain,
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
                        edit_number,
                        phase="baseline-" + state,
                    )

                baseline_clock = VerificationTimer(regression_phase)

                regression_result = verify_fn(
                    checkout,
                    paths,
                    regression,
                    config["minimum_tests"],
                    baseline_clock.phase,
                )

                timer.record_ns(
                    "baseline_verification",
                    baseline_clock.finish()["total"],
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
                    edit_number,
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
                        edit_number,
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
                        edit_number,
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
            hybrid_started = time.monotonic_ns()
            hybrid_clock = None
            hybrid_timing = {"prepare": 0, "compile": 0, "test": 0, "total": 0}

            overlaid = hybrid_overlay_files(selected, tests)
            reverted = hybrid_reverted_files(selected, tests)

            overlaid_hashes = {
                name: hashlib.sha256((checkout / name).read_bytes()).hexdigest()
                for name in overlaid
            }

            hybrid_dir = attempt / "behavioral-delta-verification"
            hybrid_dir.mkdir()

            with pinned_base_production(
                checkout,
                spec["base_commit"],
                reverted,
            ):
                def hybrid_phase(state):
                    store.status(job_id, "behavioral-delta-" + state)
                    store.attempt(
                        job_id,
                        edit_number,
                        phase="behavioral-delta-" + state,
                    )

                store.status(job_id, "behavioral-delta-preparing")

                # Reverting the selected production files to base content is part
                # of this stage's preparation cost, so it is measured together
                # with the container preparation the verifier does next.
                hybrid_clock = VerificationTimer(
                    hybrid_phase,
                    prepared_ns=time.monotonic_ns() - hybrid_started,
                )

                hybrid_result = verify_fn(
                    checkout,
                    paths,
                    hybrid_dir,
                    config["minimum_tests"],
                    hybrid_clock.phase,
                )

            if hybrid_clock is not None:
                hybrid_timing = hybrid_clock.finish()
                timer.merge_ns("behavioral_delta", hybrid_timing)

            classification, delta_diagnostic = classify_behavioral_delta(
                hybrid_result,
                overlaid,
            )

            # Evidence strength is recorded separately from the verdict. A
            # structural distinction continues under the selected API
            # compile-failure policy, but it is never reported as proof of
            # runtime behavioral novelty.
            level = evidence_level(classification)
            decision = policy_decision(classification)

            if replan["record"] is not None:
                replan["record"]["resulting_classification"] = classification
                replan["record"]["resulting_evidence_level"] = level

            # original_hashes was taken at the base commit before any edit, so it
            # is exactly base content for every tracked path. The hybrid the
            # verifier actually built must equal that, with only the approved
            # candidate test files overlaid.
            expected_hybrid = hybrid_manifest(original_hashes, overlaid_hashes)

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
                "evidence_level": level,
                "evidence_summary": evidence_summary(classification),
                "api_compile_policy": API_COMPILE_POLICY,
                "policy_decision": decision,
                "diagnostic": delta_diagnostic,
                "distinguishing": classification in DISTINGUISHING_CLASSIFICATIONS,
                "semantic_replan_attempt": replan_attempt,
                "semantic_replan_outcome": semantic_replan_outcome(
                    classification,
                    replan_attempt,
                ),
                "timing_ms": {
                    name: value // 1_000_000
                    for name, value in hybrid_timing.items()
                },
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

                if replan["attempts_used"] < SEMANTIC_REPLAN_BUDGET:
                    # Not ordinary repair. This candidate compiled, passed its own
                    # suite, left the original suite green and added coverage;
                    # what was empty is the task interpretation. It therefore
                    # draws on the separate semantic budget - never on the repair
                    # budget, which is unchanged - and the replacement candidate
                    # is re-verified from scratch by every gate below, reusing
                    # none of this candidate's evidence.
                    store.attempt(
                        job_id,
                        edit_number,
                        phase=SEMANTIC_REPLAN_SUPERSEDED,
                        result=json.dumps(result),
                    )

                    diagnosis_number = 1 + REPLAN_ATTEMPT_STRIDE * (
                        replan_attempt + 1
                    )
                    diagnosis_dir = root / f"attempt-{diagnosis_number}"
                    diagnosis_dir.mkdir()

                    replan["started_ns"] = time.monotonic_ns()

                    store.status(job_id, SEMANTIC_REPLAN_DIAGNOSING)
                    store.attempt(
                        job_id,
                        diagnosis_number,
                        phase=SEMANTIC_REPLAN_DIAGNOSING,
                        artifact_dir=str(diagnosis_dir),
                    )

                    (diagnosis_dir / "diagnostic.txt").write_text(
                        delta_diagnostic + "\n",
                        encoding="utf-8",
                    )

                    # Discard the superseded candidate before diagnosing: the
                    # diagnosis is about what the pinned base already does, and
                    # the next candidate starts from base content, not from this
                    # candidate's sources.
                    reset_to_base(
                        checkout,
                        spec["base_commit"],
                        selected,
                        paths,
                        original_hashes,
                    )

                    with timer.stage("semantic_replan_diagnosis"):
                        prompt = semantic_diagnosis_prompt(
                            spec["task"],
                            selected,
                            checkout,
                        )

                        (diagnosis_dir / "prompt.txt").write_text(
                            prompt,
                            encoding="utf-8",
                        )

                        answer = generate_fn(
                            prompt,
                            lambda ident, n=diagnosis_number: store.attempt(
                                job_id,
                                n,
                                inference_job=ident,
                            ),
                        )

                    (diagnosis_dir / "answer.txt").write_text(
                        answer,
                        encoding="utf-8",
                    )

                    diagnosis, behavior = validate_semantic_diagnosis(
                        extract_json(answer)
                    )

                    replan["attempts_used"] += 1
                    replan["record"] = {
                        "attempt": replan["attempts_used"],
                        "budget": SEMANTIC_REPLAN_BUDGET,
                        "reason": NO_BEHAVIORAL_DELTA,
                        "started_at": utc_now(),
                        "superseded_attempt": edit_number,
                        "superseded_classification": classification,
                        "diagnosis": diagnosis,
                        "behavior": behavior,
                        # Recorded so the artifact says plainly that the prompt
                        # steers the model and enforces nothing.
                        "prompt_is_advisory": True,
                        "resulting_classification": None,
                        "resulting_evidence_level": None,
                        "final_result": None,
                        "reached_succeeded": False,
                    }

                    (diagnosis_dir / "semantic-diagnosis.json").write_text(
                        json.dumps(replan["record"], indent=2),
                        encoding="utf-8",
                    )

                    store.status(job_id, SEMANTIC_REPLAN_REPLANNING)
                    store.attempt(
                        job_id,
                        diagnosis_number,
                        phase=SEMANTIC_REPLAN_REPLANNING,
                        result=json.dumps(replan["record"]),
                    )

                    continue

                store.attempt(
                    job_id,
                    edit_number,
                    phase=REJECTED_NO_BEHAVIORAL_DELTA,
                    result=json.dumps(result),
                )

                # A distinct terminal state, not a generic failure: Workflow
                # 26-style outcomes have to be queryable on their own. Nothing is
                # committed and nothing is publishable from here. The semantic
                # budget is spent, so a repeated no-delta terminates here rather
                # than retrying.
                store.status(
                    job_id,
                    REJECTED_NO_BEHAVIORAL_DELTA,
                    delta_diagnostic,
                )
                return

            if decision != CONTINUE:
                # Either the hybrid environment broke - not evidence either way,
                # so the candidate stops under the workflow's normal
                # infrastructure-failure semantics - or the selected API
                # compile-failure policy judges this evidence insufficient.
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
                    edit_number,
                    phase="failed",
                    result=json.dumps(result),
                )

                store.status(
                    job_id,
                    "failed",
                    (
                        "Behavioral-delta evidence was rejected by the "
                        f"{API_COMPILE_POLICY} policy: "
                        if decision == REJECT
                        else "Behavioral-delta verification could not produce "
                             "usable evidence: "
                    )
                    + delta_diagnostic,
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
                # Advisory, non-gating: see test_side_logic_observation().
                "test_side_logic": test_side_logic_observation(
                    patch,
                    selected,
                    tests,
                ),
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
                    edit_number,
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
                "semantic_replan": replan["record"],
                "semantic_replan_attempt": replan_attempt,
                # timing.json carries the authoritative totals, written once the
                # workflow has actually finished.
                "timing_ms": timer.snapshot(),
            }

            (attempt / "result.json").write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )

            store.attempt(
                job_id,
                edit_number,
                phase="passed",
                result=json.dumps(result),
            )

            store.status(job_id, "succeeded")

            # A verified candidate ends the run: the semantic re-plan exists
            # only for a candidate that demonstrated no behavioral delta.
            return


    except Exception as error:
        # Report the furthest attempt directory that actually got created, so
        # a failure's attempt number still says which stage it reached - now
        # across both candidate generations.
        number = highest_attempt_number(root)

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

    finally:
        # Cost and outcome evidence is written for every terminal state,
        # including failures: the point of measuring is to be able to revisit
        # the budget and the counterfactual's cost later from real data.
        try:
            record_run_evidence(root, store, job_id, timer, replan)
        except Exception:
            pass
