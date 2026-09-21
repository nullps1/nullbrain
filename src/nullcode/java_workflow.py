"""Persistent, single-worker Java smoke workflow around the Rust inference API."""
import argparse
import contextlib
import json
import os
import pathlib
import sqlite3
import subprocess
import threading
import time
import urllib.request
import uuid

from validate_java import TESTS, extract_source

ROOT = pathlib.Path(os.environ.get("NULLCODE_WORKFLOW_DIR", "/srv/nullbrain/data/nullcode-workflows"))
JOBS = pathlib.Path(os.environ.get("NULLCODE_JOBS_DIR", "/srv/nullbrain/jobs"))
SPEC = ("Return only Java source for public class Numbers, no package or main method. "
        "Implement public static int max(int[] values). Reject null and empty arrays with "
        "IllegalArgumentException. Correctly handle negative numbers and int boundaries.")


class Store:
    def __init__(self, root=ROOT):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "workflows.sqlite3"
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS workflows(
                    id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at TEXT, initial_source TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS attempts(
                    workflow_id INTEGER NOT NULL, number INTEGER NOT NULL,
                    phase TEXT NOT NULL, inference_job INTEGER, source TEXT,
                    result TEXT, artifact_dir TEXT,
                    PRIMARY KEY(workflow_id,number));
            """)
            if 'repo_spec' not in {row[1] for row in db.execute('PRAGMA table_info(workflows)')}:
                db.execute('ALTER TABLE workflows ADD COLUMN repo_spec TEXT')

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, source=None, repo_spec=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM workflows WHERE finished_at IS NULL").fetchone()[0] >= 16:
                raise ValueError("Workflow queue is full (16 unfinished jobs)")
            return db.execute("INSERT INTO workflows(initial_source,repo_spec) VALUES(?,?)",
                              (source, json.dumps(repo_spec) if repo_spec else None)).lastrowid

    def status(self, job, state, error=None):
        with self.db() as db:
            db.execute("UPDATE workflows SET status=?,error=?,finished_at=CASE WHEN ? IN "
                       "('succeeded','failed','interrupted') THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",
                       (state, error, state, job))

    def attempt(self, job, number, **fields):
        allowed = {"phase", "inference_job", "source", "result", "artifact_dir"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid attempt fields")
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO attempts(workflow_id,number,phase) VALUES(?,?,'generating')", (job, number))
            db.execute("UPDATE attempts SET " + ",".join(f"{key}=?" for key in fields) +
                       " WHERE workflow_id=? AND number=?", (*fields.values(), job, number))

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE workflows SET status='interrupted', finished_at=CURRENT_TIMESTAMP, "
                       "error='Worker restarted; review attempts and submit a new workflow.' "
                       "WHERE finished_at IS NULL AND status != 'queued'")

    def claim(self):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM workflows WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE workflows SET status='generating' WHERE id=?", (row["id"],))
            return dict(row) if row else None

    def show(self, job):
        with self.db() as db:
            row = db.execute("SELECT * FROM workflows WHERE id=?", (job,)).fetchone()
            if not row:
                raise ValueError("Workflow not found")
            result = dict(row)
            result["attempts"] = [dict(r) for r in db.execute(
                "SELECT * FROM attempts WHERE workflow_id=? ORDER BY number", (job,))]
            for attempt in result["attempts"]:
                if attempt["result"]:
                    attempt["result"] = json.loads(attempt["result"])
            return result


def api(path, data=None):
    request = urllib.request.Request("http://127.0.0.1:8080" + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def generate(prompt, record_id):
    # Controller enforces a 2000-byte input limit.
    if len(prompt.encode()) > 2000:
        raise ValueError("Repair prompt exceeded controller input limit")
    job = api("/jobs", {"prompt": prompt})
    record_id(job["id"])
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        result = api(f'/jobs/{job["id"]}')
        if result["status"] == "succeeded":
            return result["response"]
        if result["status"] == "failed":
            raise RuntimeError("Inference failed: " + str(result["error"]))
        time.sleep(3)
    raise TimeoutError("Inference wait exceeded 30 minutes; inspect the linked inference job")


def command(args, timeout=120):
    """Drain all output, retain at most 64 KiB, and bound process duration."""
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    def drain():
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            output.extend(chunk[:max(0, 65536 - len(output))])
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
        code = 124
    reader.join(timeout=5)
    return {"exit_code": code, "timed_out": timed_out, "log": output.decode(errors="replace")}


def verify(source, folder, phase, test_source=TESTS):
    for name, text in [("Numbers.java", source), ("NumbersTest.java", test_source)]:
        path = folder / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o644)
    image = subprocess.check_output(["docker", "image", "inspect", "eclipse-temurin:21-jdk",
                                    "--format", "{{.Id}}"], text=True, timeout=30).strip()
    name = "nullcode-workflow-" + uuid.uuid4().hex
    result = {"image_id": image, "compile": None, "tests": None, "passed": False, "repairable": False}
    try:
        started = command([
            "docker", "run", "-d", "--rm", "--pull=never", "--name", name,
            "--label=io.nullcode.workflow=java-smoke",
            "--network=none", "--read-only", "--user=1001:1001", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--memory=512m", "--memory-swap=512m",
            "--cpus=1", "--pids-limit=128", "--log-driver=none",
            "--tmpfs=/work:rw,nosuid,nodev,size=64m,mode=1777",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=16m,mode=1777",
            "--mount", f"type=bind,source={folder},target=/src,readonly",
            "--workdir=/work", "--entrypoint=/bin/sleep", image, "300"], timeout=30)
        if started["exit_code"]:
            result["infrastructure_error"] = started
            return result
        phase("compiling")
        compiled = command(["docker", "exec", name, "javac", "-J-Xmx128m",
            "-J-XX:ActiveProcessorCount=1", "-proc:none", "-d", "/work",
            "/src/Numbers.java", "/src/NumbersTest.java"])
        result["compile"] = compiled
        result["repairable"] = compiled["exit_code"] == 1 and not compiled["timed_out"]
        if compiled["exit_code"]:
            return result
        phase("testing")
        tested = command(["docker", "exec", name, "java", "-Xmx128m",
            "-XX:ActiveProcessorCount=1", "-XX:+UseSerialGC", "-cp", "/work", "NumbersTest"])
        result["tests"] = tested
        result["passed"] = tested["exit_code"] == 0 and "RESULT: 8/8 checks passed" in tested["log"]
        result["repairable"] = not tested["timed_out"] and tested["exit_code"] in (0, 1) and not result["passed"]
        return result
    finally:
        cleanup = command(["docker", "rm", "-f", name], timeout=30)
        result["cleanup"] = cleanup
        # A successful run must not silently leave a sandbox behind.
        if cleanup["exit_code"] and "No such container" not in cleanup["log"]:
            result["passed"] = False
            result["repairable"] = False
        (folder / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def clip(text, limit):
    return text.encode()[:limit].decode(errors="ignore")


def run_job(store, job, generate_fn=generate, verify_fn=verify, artifacts=JOBS):
    job_id = job["id"]
    prompt = SPEC
    try:
        for number in (1, 2):
            store.attempt(job_id, number, phase="generating")
            folder = artifacts / f"workflow-{job_id}" / f"attempt-{number}"
            folder.mkdir(parents=True, exist_ok=False)
            folder.chmod(0o755)
            store.attempt(job_id, number, artifact_dir=str(folder))
            answer = job.get("initial_source") if number == 1 else None
            if answer is None:
                answer = generate_fn(prompt, lambda inference_id: store.attempt(job_id, number, inference_job=inference_id))
            (folder / "answer.txt").write_text(answer, encoding="utf-8")
            source = answer
            try:
                source = extract_source(answer)
            except ValueError as error:
                result = {"passed": False, "repairable": True, "extraction_error": str(error)}
            else:
                store.attempt(job_id, number, source=source)
                def phase(state):
                    store.status(job_id, state)
                    store.attempt(job_id, number, phase=state)
                result = verify_fn(source, folder, phase)
            (folder / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            store.attempt(job_id, number, phase="passed" if result["passed"] else "failed", result=json.dumps(result))
            if result["passed"]:
                store.status(job_id, "succeeded")
                return
            if number == 2 or not result.get("repairable"):
                store.status(job_id, "failed", "Verification failed; see attempt results. Maximum one repair.")
                return
            diagnostic = result.get("extraction_error") or next(
                (result[key]["log"] for key in ("tests", "compile") if result.get(key)), "Verification failed")
            prompt = SPEC + "\nFix the previous attempt using this diagnostic. Return the complete corrected class.\nPrevious source (may be shortened):\n" + clip(source, 900) + "\nDiagnostic:\n" + clip(diagnostic, 500)
            store.status(job_id, "repairing")
    except Exception as error:
        store.attempt(job_id, number, phase="error", result=json.dumps({"error": str(error)}))
        store.status(job_id, "failed", str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("--repair-demo", action="store_true", help="Start with deliberately wrong source to exercise repair")
    repo = sub.add_parser("submit-repo")
    repo.add_argument("--repo", required=True)
    repo.add_argument("--base", default="HEAD")
    repo.add_argument("--task", required=True)
    repo.add_argument("--review-demo", action="store_true", help="Start with passing code containing an impossible int range check")
    gradle = sub.add_parser("submit-gradle")
    gradle.add_argument("--repo", required=True)
    gradle.add_argument("--base", default="HEAD")
    gradle.add_argument("--file", required=True)
    gradle.add_argument("--task", required=True)

    plan = sub.add_parser("submit-plan")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--base", default="HEAD")
    plan.add_argument("--task", required=True)

    execute = sub.add_parser("submit-execute")
    execute.add_argument("--repo", required=True)
    execute.add_argument("--base", default="HEAD")
    execute.add_argument("--task", required=True)
    accepted = sub.add_parser("submit-accepted", help="Edit production against committed reviewed tests")
    accepted.add_argument("--repo", required=True)
    accepted.add_argument("--base", default="HEAD")
    accepted.add_argument("--contract", default=".nullcode-acceptance.json")
    accepted.add_argument("--acceptance-reviewed", action="store_true", required=True)
    show = sub.add_parser("show")
    show.add_argument("id", type=int)
    wait = sub.add_parser("wait")
    wait.add_argument("id", type=int)
    sub.add_parser("list")
    sub.add_parser("run")
    args = parser.parse_args()
    store = Store()
    if args.action == "submit":
        initial = "public class Numbers { public static int max(int[] values) { return 0; } }" if args.repair_demo else None
        print(json.dumps({"workflow_id": store.submit(initial), "status": "queued"}))
    elif args.action == "submit-repo":
        from repo_workflow import prepare_spec
        spec = prepare_spec(args.repo, args.base, args.task)
        spec['review_demo'] = args.review_demo
        print(json.dumps({"workflow_id": store.submit(repo_spec=spec), "status": "queued", "base_commit": spec['base_commit']}))
    elif args.action == "submit-gradle":
        from gradle_workflow import prepare_spec
        spec = prepare_spec(args.repo, args.base, args.task, args.file)
        print(json.dumps({"workflow_id": store.submit(repo_spec=spec), "status": "queued", "base_commit": spec['base_commit']}))
    elif args.action == "submit-plan":
        from repo_plan_workflow import prepare_spec
        spec = prepare_spec(args.repo, args.base, args.task)
        print(json.dumps({"workflow_id": store.submit(repo_spec=spec), "status": "queued", "base_commit": spec['base_commit']}))
    elif args.action == "submit-execute":
        from repo_execute_workflow import prepare_spec
        spec = prepare_spec(args.repo, args.base, args.task)
        print(json.dumps({"workflow_id": store.submit(repo_spec=spec), "status": "queued", "base_commit": spec['base_commit']}))
    elif args.action == "submit-accepted":
        from accepted_workflow import prepare_spec
        spec = prepare_spec(args.repo, args.base, args.contract, args.acceptance_reviewed)
        print(json.dumps({"workflow_id": store.submit(repo_spec=spec), "status": "queued", "base_commit": spec['base_commit']}))
    elif args.action == "show":
        print(json.dumps(store.show(args.id), indent=2))
    elif args.action == "wait":
        deadline = time.monotonic() + 5700
        last = None
        while time.monotonic() < deadline:
            result = store.show(args.id)
            if result["status"] != last:
                print("Workflow", args.id, result["status"], flush=True)
                last = result["status"]
            if result["finished_at"]:
                for attempt in result["attempts"]:
                    print("Attempt", attempt["number"], attempt["phase"], "inference job", attempt["inference_job"])
                    print(json.dumps(attempt["result"], indent=2))
                    print("Artifacts:", attempt["artifact_dir"])
                if result["status"] != "succeeded":
                    raise SystemExit(result["error"] or result["status"])
                return
            time.sleep(3)
        raise SystemExit("Wait timed out; inspect the worker service and workflow record")
    elif args.action == "list":
        with store.db() as db:
            print(json.dumps([dict(row) for row in db.execute("SELECT id,status,created_at,finished_at,error FROM workflows ORDER BY id DESC LIMIT 50")], indent=2))
    else:
        import fcntl
        with (ROOT / "worker.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            leftover = subprocess.check_output(["docker", "ps", "-aq", "--filter",
                "label=io.nullcode.workflow=java-smoke"], text=True, timeout=30).split()
            if leftover:
                subprocess.run(["docker", "rm", "-f", *leftover], check=True, timeout=30)
            store.recover()
            while True:
                job = store.claim()
                if job:
                    print(f'Workflow {job["id"]} started', flush=True)
                    if job.get('repo_spec'):
                        profile = json.loads(job['repo_spec']).get('profile')
                        if profile == 'gradle-junit-v1':
                            from gradle_workflow import run_job as run_gradle_job
                            run_gradle_job(store, job)
                        elif profile == 'repo-plan-v1':
                            from repo_plan_workflow import run_job as run_plan_job
                            run_plan_job(store, job)
                        elif profile == 'accepted-java-v1':
                            from accepted_workflow import run_job as run_accepted_job
                            run_accepted_job(store, job)
                        elif profile == 'repo-execute-v1':
                            from repo_execute_workflow import run_job as run_execute_job
                            run_execute_job(store, job)
                        else:
                            from repo_workflow import run_repo_job
                            run_repo_job(store, job)
                    else:
                        run_job(store, job)
                    print(f'Workflow {job["id"]}: {store.show(job["id"])["status"]}', flush=True)
                else:
                    time.sleep(2)


if __name__ == "__main__":
    main()
