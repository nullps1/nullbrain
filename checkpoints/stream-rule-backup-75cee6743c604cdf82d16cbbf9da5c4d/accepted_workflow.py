"""One production file, committed reviewed acceptance tests, at most two repairs."""
import hashlib
import json
import pathlib

from java_workflow import JOBS, generate
from gradle_workflow import inspect, verify
from repo_execute_workflow import compact_prompt_java, extract_java, verification_diagnostic
from repo_workflow import git
from review_java import review_source

PROFILE = "accepted-java-v1"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_contract(repo, commit, contract):
    paths, config = inspect(repo, commit)
    if contract not in paths:
        raise ValueError("Acceptance contract must be a committed file")
    data = json.loads(git(repo, "show", commit + ":" + contract, raw=True))
    if not isinstance(data, dict) or data.get("profile") != PROFILE:
        raise ValueError("Unsupported acceptance contract")
    target, task = data.get("target"), data.get("task")
    if target not in config["editable_files"]:
        raise ValueError("Contract target must be an approved production file")
    if not isinstance(task, str) or not task.strip() or len(task.encode()) > 500:
        raise ValueError("Contract task must contain 1 to 500 bytes")
    tests = data.get("acceptance_tests")
    if (not isinstance(tests, list) or not tests
            or any(not isinstance(p, str) for p in tests)
            or len(set(tests)) != len(tests)):
        raise ValueError("Contract requires unique acceptance test paths")
    if any(p not in paths or not p.startswith("src/test/java/")
           or not p.endswith(".java") for p in tests):
        raise ValueError("Acceptance tests must be committed Java test files")
    minimum = data.get("minimum_tests")
    if type(minimum) is not int or not config["minimum_tests"] <= minimum <= 1000:
        raise ValueError("Contract minimum_tests must cover the original suite")
    source = git(repo, "show", commit + ":" + target, binary=True)
    if len(source) > 900:
        raise ValueError("Initial production source exceeds 900 bytes")
    return paths, data


def prepare_spec(repo, base, contract, reviewed=False):
    if not reviewed:
        raise ValueError("Review the committed contract/tests and use --acceptance-reviewed")
    repo = pathlib.Path(repo).resolve(strict=True)
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Commit the reviewed tests and contract; repository must be clean")
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}")
    paths, data = load_contract(repo, commit, contract)
    # Bind the exact immutable inputs in the queued job, including build files.
    protected = {p: digest(git(repo, "show", commit + ":" + p, binary=True))
                 for p in paths if p != data["target"]}
    return {"profile": PROFILE, "repo": str(repo), "base_commit": commit,
            "contract": contract, "contract_sha256": protected[contract],
            "protected_sha256": protected, "acceptance_reviewed": True}


def prompt_for(contract, source, diagnostic=None):
    text = (
        f"Java 21. Return ONLY the complete {pathlib.PurePosixPath(contract['target']).name}; "
        "no prose, fences, or other files. Preserve existing API/behavior. "
        "Acceptance tests are human-reviewed, fixed, and cannot be edited.\n"
        f"TASK:\n{contract['task']}\n"
    )
    if diagnostic is not None:
        if not diagnostic.strip():
            raise ValueError("Repair requires a diagnostic")
        text += "Fix production code against the fixed contract.\nFAILURE:\n" + diagnostic + "\n"
    text += "COMPLETE TARGET SOURCE:\n" + compact_prompt_java(source)
    if len(text.encode()) > 2000:
        raise ValueError("Complete accepted-task prompt exceeds 2000 bytes; nothing truncated")
    return text


def hashes(checkout, paths):
    result = {}
    for name in paths:
        file = checkout / name
        if file.is_symlink() or not file.is_file():
            raise ValueError("Missing or non-regular tracked file: " + name)
        result[name] = digest(file.read_bytes())
    return result


def enforce_scope(checkout, paths, protected, target):
    current = hashes(checkout, paths)
    if any(current.get(p) != value for p, value in protected.items()):
        raise ValueError("Fixed tests, contract, or other protected content changed")
    changed = set(git(checkout, "diff", "--name-only", "HEAD").splitlines())
    staged = git(checkout, "diff", "--cached", "--name-only")
    untracked = git(checkout, "ls-files", "--others", "--exclude-standard")
    if not changed <= {target} or staged or untracked:
        raise ValueError("Changes escaped the one-file production scope")
    return current


def save(folder, name, value):
    (folder / name).write_text(json.dumps(value, indent=2), encoding="utf-8")


def run_job(store, job, generate_fn=generate, verify_fn=verify, artifacts=JOBS):
    ident, number = job["id"], 1
    folder = None
    try:
        spec = json.loads(job["repo_spec"])
        if spec.get("profile") != PROFILE or spec.get("acceptance_reviewed") is not True:
            raise ValueError("Job lacks reviewed acceptance contract")
        root = artifacts / f"workflow-{ident}"
        root.mkdir(parents=True, exist_ok=False)
        checkout = root / "repo"
        git(root, "clone", "--no-hardlinks", "--no-checkout", "--",
            pathlib.Path(spec["repo"]).as_posix(), checkout.as_posix())
        paths, contract = load_contract(checkout, spec["base_commit"], spec["contract"])
        target = contract["target"]
        branch = f"agent/workflow-{ident}"
        git(checkout, "checkout", "-b", branch, spec["base_commit"])
        git(checkout, "remote", "remove", "origin")
        initial = hashes(checkout, paths)
        protected = {p: h for p, h in initial.items() if p != target}
        if (protected != spec["protected_sha256"]
                or protected[spec["contract"]] != spec["contract_sha256"]):
            raise ValueError("Queued contract or protected inputs do not match the pinned commit")
        save(root, "acceptance.json", {"contract": contract, "base_commit": spec["base_commit"],
                                      "protected_sha256": protected})
        source = (checkout / target).read_text(encoding="utf-8")
        diagnostic = None
        history = []
        for number in (1, 2, 3):
            folder = root / f"attempt-{number}"
            folder.mkdir()
            store.attempt(ident, number, phase="generating", artifact_dir=str(folder))
            store.status(ident, "generating" if number == 1 else f"repairing-{number-1}")
            prompt = prompt_for(contract, source, diagnostic)
            (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
            answer = generate_fn(prompt, lambda job_id: store.attempt(ident, number, inference_job=job_id))
            (folder / "answer.txt").write_text(answer, encoding="utf-8")
            enforce_scope(checkout, paths, protected, target)
            try:
                candidate = extract_java(answer, target)
            except ValueError as error:
                result = {"passed": False, "repairable": True, "error": str(error)}
                diagnostic = str(error)
            else:
                if compact_prompt_java(candidate) == compact_prompt_java(source):
                    raise ValueError("Model returned unchanged source; stopped without another inference")
                (checkout / target).write_text(candidate, encoding="utf-8")
                source = candidate
                before = enforce_scope(checkout, paths, protected, target)
                def phase(state):
                    store.status(ident, state if number == 1 else f"repair-{number-1}-{state}")
                    store.attempt(ident, number, phase=state)
                result = verify_fn(checkout, paths, folder, contract["minimum_tests"], phase)
                compiled = result.get("compile") or {}
                # Fixed tests may reference a method the candidate forgot to add.
                # Its missing-symbol error appears in compileTestJava, not compileJava.
                if (not result["passed"] and compiled.get("exit_code") == 1
                        and not compiled.get("timed_out")
                        and any(stage in compiled.get("log", "") for stage in
                                (":compileJava FAILED", ":compileTestJava FAILED"))
                        and result.get("cleanup", {}).get("exit_code") == 0):
                    result["repairable"] = True
                current = enforce_scope(checkout, paths, protected, target)
                if current != before or result.get("snapshot_sha256") != before:
                    raise ValueError("Checkout or verification snapshot changed")
                if result["passed"]:
                    report = result.get("junit") or {}
                    cleanup = result.get("cleanup") or {}
                    if (not result.get("compile") or result["compile"].get("exit_code") != 0
                            or result["compile"].get("timed_out")
                            or not result.get("tests") or result["tests"].get("exit_code") != 0
                            or result["tests"].get("timed_out")
                            or report.get("failures") != 0 or report.get("skipped") != 0
                            or report.get("tests", 0) < contract["minimum_tests"]
                            or cleanup.get("exit_code") != 0 or cleanup.get("timed_out")):
                        raise ValueError("Incomplete acceptance evidence; cannot accept result")
                diagnostic = verification_diagnostic(result)
            history.append({"attempt": number, "passed": result["passed"]})
            save(folder, "result.json", result)
            store.attempt(ident, number, phase="passed" if result["passed"] else "failed",
                          result=json.dumps(result))
            if result["passed"]:
                patch = git(checkout, "diff", "--no-ext-diff", "--no-textconv", "--binary",
                            spec["base_commit"], raw=True)
                review = review_source(source, patch)
                save(folder, "review.json", review)
                if review["status"] != "passed":
                    raise ValueError("Targeted review requested changes; inspect review.json")
                if enforce_scope(checkout, paths, protected, target) != result["snapshot_sha256"]:
                    raise ValueError("Checkout changed after verification")
                (folder / "diff.patch").write_text(patch, encoding="utf-8")
                git(checkout, "add", "--", target)
                git(checkout, "-c", "user.name=NullCode", "-c", "user.email=nullcode@localhost",
                    "commit", "-m", f"Implement accepted Java task {ident}")
                commit = git(checkout, "rev-parse", "HEAD")
                committed = {p: digest(git(checkout, "show", commit + ":" + p, binary=True)) for p in paths}
                if committed != result["snapshot_sha256"] or git(checkout, "status", "--porcelain"):
                    raise ValueError("Commit does not match verified snapshot")
                repository = {"profile": PROFILE, "checkout": str(checkout), "branch": branch,
                              "base_commit": spec["base_commit"], "commit": commit,
                              "editable_files": [target], "diff_path": str(folder / "diff.patch")}
                save(root, "repository.json", repository)
                final = {"passed": True, "profile": PROFILE, "verification": result,
                         "review": review, "repository": repository, "attempts": history,
                         "contract_sha256": spec["contract_sha256"], "fixed_tests_unchanged": True}
                save(folder, "result.json", final)
                store.attempt(ident, number, phase="passed", result=json.dumps(final))
                store.status(ident, "succeeded")
                return
            if not result.get("repairable"):
                raise ValueError("Verification cannot be repaired by a production edit")
        store.status(ident, "failed", "Fixed acceptance suite still fails after two repairs")
    except Exception as error:
        result = {"passed": False, "error": str(error)}
        if folder is not None:
            save(folder, "error.json", result)
        store.attempt(ident, number, phase="error", result=json.dumps(result))
        store.status(ident, "failed", str(error))
