"""Read-only repository inspection and planning workflow for NullCode Milestone 7A."""

import json
import pathlib
import subprocess

from java_workflow import JOBS, generate


PROFILE = "repo-plan-v1"
MAX_TREE_BYTES = 1200
MAX_SELECTED_FILES = 3
MAX_FILE_BYTES = 550
MAX_TASK_BYTES = 500

ALLOWED_SUFFIXES = {
    ".java",
    ".gradle",
    ".kts",
    ".properties",
    ".json",
    ".md",
    ".txt",
}

IGNORED_PARTS = {
    ".git",
    ".gradle",
    "build",
    "out",
    "target",
    "node_modules",
}


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
        stderr=subprocess.STDOUT,
        timeout=30,
    ).strip()


def clip_bytes(text, limit):
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", errors="ignore")


def prepare_spec(repo, base, task):
    repo = pathlib.Path(repo).resolve()

    if not repo.is_dir():
        raise ValueError("Repository does not exist")

    if len(task.encode("utf-8")) > MAX_TASK_BYTES:
        raise ValueError(f"Task exceeds {MAX_TASK_BYTES}-byte planning limit")

    base_commit = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")

    return {
        "profile": PROFILE,
        "repo": str(repo),
        "base": base,
        "base_commit": base_commit,
        "task": task,
    }


def committed_inventory(checkout, base_commit):
    raw = git(checkout, "ls-tree", "-r", base_commit)

    files = []

    for line in raw.splitlines():
        try:
            metadata, path = line.split("\t", 1)
            mode, object_type, _sha = metadata.split()
        except ValueError:
            continue

        if mode == "120000" or object_type != "blob":
            continue

        candidate = pathlib.PurePosixPath(path)

        if any(part in IGNORED_PARTS for part in candidate.parts):
            continue

        if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
            continue

        files.append(path)

    files.sort()

    if not files:
        raise ValueError("No readable repository files found")

    rendered = clip_bytes("\n".join(files), MAX_TREE_BYTES)
    visible = [line for line in rendered.splitlines() if line in files]

    if not visible:
        raise ValueError("Repository inventory does not fit planning budget")

    return visible


def extract_json(answer):
    text = answer.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError("Model did not return a JSON object")

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid model JSON: {error}") from error


def selection_prompt(task, files):
    tree = "\n".join(files)

    prompt = (
        "You are inspecting a Java repository for a future coding task. "
        "Do not write code and do not propose files that are not listed. "
        f"Choose at most {MAX_SELECTED_FILES} files most relevant to understanding the task. "
        "Prefer implementation files, tests, and directly relevant build/config files. "
        'Return JSON only: {"files":["path"],"reason":"short explanation"}.\n'
        f"Task: {task}\n"
        "Committed repository files:\n"
        f"{tree}"
    )

    if len(prompt.encode("utf-8")) > 2000:
        raise ValueError("Repository selection prompt exceeds controller input limit")

    return prompt


def validate_selection(data, inventory):
    if not isinstance(data, dict):
        raise ValueError("Selection must be a JSON object")

    files = data.get("files")

    if not isinstance(files, list) or not files:
        raise ValueError("Selection must include at least one file")

    if len(files) > MAX_SELECTED_FILES:
        raise ValueError(f"Selection exceeds {MAX_SELECTED_FILES} files")

    if len(set(files)) != len(files):
        raise ValueError("Selection contains duplicate files")

    for path in files:
        if not isinstance(path, str) or path not in inventory:
            raise ValueError(f"Model selected unapproved file: {path}")

    return files


def read_context(checkout, selected):
    sections = []
    root = checkout.resolve()

    for relative in selected:
        path = checkout / relative

        if path.is_symlink():
            raise ValueError(f"Refusing symlink: {relative}")

        resolved = path.resolve()

        if root not in resolved.parents:
            raise ValueError(f"Path escaped checkout: {relative}")

        content = path.read_text(encoding="utf-8", errors="replace")
        excerpt = clip_bytes(content, MAX_FILE_BYTES)

        sections.append(f"FILE: {relative}\n{excerpt}")

    return "\n\n".join(sections)


def planning_prompt(task, selected, context):
    prompt = (
        "You are planning a small Java repository change. Do not write code. "
        "Use only the supplied repository evidence. "
        "Return JSON only with this shape: "
        '{"summary":"...",'
        '"files":[{"path":"...","reason":"..."}],'
        '"steps":["..."],'
        '"risks":["..."]}. '
        "Every file path must be one of the selected files. "
        "Keep the plan concise and implementation-oriented.\n"
        f"Task: {task}\n"
        f"Selected files: {json.dumps(selected)}\n"
        "Repository evidence:\n"
        f"{context}"
    )

    if len(prompt.encode("utf-8")) > 2000:
        raise ValueError(
            "Selected repository context exceeds controller input limit; "
            "choose fewer or smaller files"
        )

    return prompt


def validate_plan(data, selected):
    if not isinstance(data, dict):
        raise ValueError("Plan must be a JSON object")

    summary = data.get("summary")
    files = data.get("files")
    steps = data.get("steps")
    risks = data.get("risks")

    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Plan requires a summary")

    if not isinstance(files, list) or not files:
        raise ValueError("Plan requires relevant files")

    if not isinstance(steps, list) or not steps:
        raise ValueError("Plan requires implementation steps")

    if not isinstance(risks, list):
        raise ValueError("Plan risks must be a list")

    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Plan file entries must be objects")

        path = item.get("path")
        reason = item.get("reason")

        if path not in selected:
            raise ValueError(f"Plan referenced unselected file: {path}")

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"Missing reason for planned file: {path}")

    if not all(isinstance(step, str) and step.strip() for step in steps):
        raise ValueError("Plan steps must be non-empty strings")

    if not all(isinstance(risk, str) for risk in risks):
        raise ValueError("Plan risks must be strings")

    return data


def run_job(store, job, generate_fn=generate, artifacts=JOBS):
    job_id = job["id"]

    try:
        spec = json.loads(job["repo_spec"])

        root = artifacts / f"workflow-{job_id}"
        root.mkdir(parents=True, exist_ok=False)

        checkout = root / "repo"

        subprocess.run(
            [
                "git",
                "clone",
                "--no-hardlinks",
                "--no-checkout",
                "--",
                spec["repo"],
                str(checkout),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )

        git(checkout, "checkout", "--detach", spec["base_commit"])

        inventory = committed_inventory(checkout, spec["base_commit"])

        (root / "repo-tree.txt").write_text(
            "\n".join(inventory) + "\n",
            encoding="utf-8",
        )

        selection_dir = root / "attempt-1"
        selection_dir.mkdir()

        prompt = selection_prompt(spec["task"], inventory)
        (selection_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

        store.status(job_id, "inspecting")
        store.attempt(
            job_id,
            1,
            phase="inspecting",
            artifact_dir=str(selection_dir),
        )

        answer = generate_fn(
            prompt,
            lambda ident: store.attempt(
                job_id,
                1,
                inference_job=ident,
            ),
        )

        (selection_dir / "answer.txt").write_text(answer, encoding="utf-8")

        selection = extract_json(answer)
        selected = validate_selection(selection, inventory)

        selection_result = {
            "passed": True,
            "selected_files": selected,
            "reason": selection.get("reason", ""),
        }

        (selection_dir / "result.json").write_text(
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

        context = read_context(checkout, selected)

        (root / "context.txt").write_text(
            context,
            encoding="utf-8",
        )

        planning_dir = root / "attempt-2"
        planning_dir.mkdir()

        prompt = planning_prompt(
            spec["task"],
            selected,
            context,
        )

        (planning_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

        store.status(job_id, "planning")
        store.attempt(
            job_id,
            2,
            phase="planning",
            artifact_dir=str(planning_dir),
        )

        answer = generate_fn(
            prompt,
            lambda ident: store.attempt(
                job_id,
                2,
                inference_job=ident,
            ),
        )

        (planning_dir / "answer.txt").write_text(answer, encoding="utf-8")

        plan = validate_plan(extract_json(answer), selected)

        dirty = git(checkout, "status", "--porcelain")

        if dirty:
            raise ValueError(
                "Repository changed during read-only planning workflow"
            )

        result = {
            "passed": True,
            "profile": PROFILE,
            "base_commit": spec["base_commit"],
            "selected_files": selected,
            "plan": plan,
            "repository_clean": True,
        }

        (planning_dir / "result.json").write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )

        (root / "plan.json").write_text(
            json.dumps(plan, indent=2),
            encoding="utf-8",
        )

        store.attempt(
            job_id,
            2,
            phase="passed",
            result=json.dumps(result),
        )

        store.status(job_id, "succeeded")

    except Exception as error:
        root = artifacts / f"workflow-{job_id}"
        number = 2 if (root / "attempt-2").exists() else 1

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
