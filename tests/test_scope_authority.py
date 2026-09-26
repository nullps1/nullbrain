"""Milestone 7C-1 authority boundary: a scope proposal is inert.

repo-execute-v1 and the 7C-2 publisher must keep deriving authority only from
the committed .nullcode.json at the pinned base, whatever proposal artifacts
exist. Only a human-authored commit of the configuration grants scope.
"""

import ast
import builtins
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch

import test_repo_execute_publish as publish_tests
from test_patient_zero_compat import build_patient_zero
from nullcode.core import java_workflow
from nullcode.core.java_workflow import Store
from nullcode.publish import publish_workflow
from nullcode.repo import repo_execute_workflow, repo_scope_review, repo_scope_workflow as scope
from nullcode.repo.repo_workflow import git

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
PACKAGE = SRC / "nullcode"

TASK = "Make Main print the word count of its argument using TextStats, and test it."
NEW_PROD = "src/main/java/lab/cli/Main.java"
GRANTED_TEST = "src/test/java/lab/TextStatsTest.java"

SCOPE_ARTIFACTS = ("scope-proposal.json", "proposed-nullcode-config.json",
                   "proposed-nullcode.patch", "scope-review.json")

SELECTION = {"production_files": [NEW_PROD], "test_files": [GRANTED_TEST],
             "context_files": [], "reason": "Main gains the feature"}
PROPOSAL = {"production_files": [{"path": NEW_PROD, "reason": "feature"}],
            "test_files": [{"path": GRANTED_TEST, "reason": "coverage"}],
            "context_files": [], "risks": [], "summary": "Word-count mode for Main."}


def module_name(path):
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def imported_modules(path):
    """Every module a source file imports, at any depth (lazy imports included)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


class ReadAudit:
    """Record every file the code under test opens or reads."""

    def __init__(self):
        self.paths = []

    def __enter__(self):
        audit = self
        real_open = builtins.open
        real_read_text = pathlib.Path.read_text
        real_read_bytes = pathlib.Path.read_bytes

        def opened(file, *args, **kwargs):
            audit.paths.append(("open", str(file)))
            return real_open(file, *args, **kwargs)

        def read_text(self, *args, **kwargs):
            audit.paths.append(("read_text", str(self)))
            return real_read_text(self, *args, **kwargs)

        def read_bytes(self):
            audit.paths.append(("read_bytes", str(self)))
            return real_read_bytes(self)

        self.patches = [patch.object(builtins, "open", opened),
                        patch.object(pathlib.Path, "read_text", read_text),
                        patch.object(pathlib.Path, "read_bytes", read_bytes)]
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self.patches:
            item.stop()

    def scope_reads(self, how=("open", "read_text", "read_bytes")):
        return [p for kind, p in self.paths
                if kind in how and pathlib.Path(p).name in SCOPE_ARTIFACTS]


class ImportBoundaryTests(unittest.TestCase):
    def test_execution_and_publishing_never_load_the_scope_modules(self):
        code = (
            "import sys\n"
            "import nullcode.repo.repo_execute_workflow, nullcode.repo.accepted_workflow\n"
            "import nullcode.publish.publish_workflow, nullcode.publish.repo_execute_publish\n"
            "import nullcode.publish.check_acceptance, nullcode.publish.prepare_acceptance\n"
            "import nullcode.gradle.gradle_workflow, nullcode.repo.repo_plan_workflow\n"
            "import nullcode.core.java_workflow\n"
            "print(sorted(m for m in sys.modules if 'scope' in m))\n"
        )
        output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                env=dict(os.environ, PYTHONPATH=str(SRC)), check=True).stdout
        self.assertEqual(output.strip(), "[]")

    def test_only_the_worker_dispatch_and_the_review_import_the_scope_workflow(self):
        importers = {}
        for path in PACKAGE.rglob("*.py"):
            for target in imported_modules(path):
                if target.startswith("nullcode.repo.repo_scope"):
                    importers.setdefault(target.split(".")[2], set()).add(module_name(path))
        self.assertEqual(importers.get("repo_scope_workflow"),
                         {"nullcode.core.java_workflow", "nullcode.repo.repo_scope_review"})
        self.assertNotIn("repo_scope_review", importers)

    def test_no_module_sets_scope_reviewed_programmatically(self):
        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                    if name == "render_grant":
                        self.assertEqual(module_name(path), "nullcode.repo.repo_scope_review")
                        self.assertEqual(ast.unparse(node.args[-1]), "args.scope_reviewed")


class ScopeFixture(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / "test-results" / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.repo = build_patient_zero(self.root)
        self.store = Store(self.root / "db")
        self.artifacts = self.root / "jobs"

    def propose(self):
        spec = scope.prepare_spec(self.repo, "main", TASK)
        job_id = self.store.submit(repo_spec=spec)
        answers = [json.dumps(SELECTION), json.dumps(PROPOSAL)]
        scope.run_job(self.store, self.store.claim(),
                      lambda prompt, record: (record(1), answers.pop(0))[1], self.artifacts)
        job = self.store.show(job_id)
        self.assertEqual(job["status"], "succeeded", job.get("error"))
        repo_scope_review.render_grant(job, self.artifacts, True)
        return job, self.artifacts / f"workflow-{job_id}"

    def execute(self, selection_files):
        """Run repo-execute-v1 until its selection gate. Verification must never run."""
        spec = repo_execute_workflow.prepare_spec(self.repo, "main", TASK)
        job_id = self.store.submit(repo_spec=spec)
        answers = [json.dumps({"files": selection_files, "reason": "r"}), "not a plan"]

        def verify(*args):
            raise AssertionError("verification must not run")

        repo_execute_workflow.run_job(
            self.store, self.store.claim(),
            lambda prompt, record: (record(1), answers.pop(0))[1], verify, self.artifacts)
        return self.store.show(job_id)


class RepoExecuteAuthorityTests(ScopeFixture):
    def test_proposal_does_not_widen_execution_scope(self):
        _job, workdir = self.propose()
        self.assertIn(NEW_PROD, json.loads((workdir / "proposed-nullcode-config.json").read_text())["editable_files"])

        with ReadAudit() as audit:
            result = self.execute([NEW_PROD, GRANTED_TEST])
        self.assertEqual(result["status"], "failed")
        self.assertIn("unapproved file: " + NEW_PROD, result["error"])
        self.assertEqual(audit.scope_reads(), [])

    def test_proposal_artifacts_inside_the_repository_are_not_authority(self):
        _job, workdir = self.propose()
        for name in SCOPE_ARTIFACTS:
            shutil.copy(workdir / name, self.repo / name)

        # Untracked in the working tree: prepare_spec reads committed content only.
        result = self.execute([NEW_PROD, GRANTED_TEST])
        self.assertIn("unapproved file", result["error"])

        # Even committed as ordinary files, they are not the configuration.
        git(self.repo, "add", *SCOPE_ARTIFACTS)
        git(self.repo, "-c", "user.name=T", "-c", "user.email=t@localhost", "commit", "-m", "artifacts")
        with ReadAudit() as audit:
            result = self.execute([NEW_PROD, GRANTED_TEST])
        self.assertIn("unapproved file", result["error"])
        # 7B hashes every tracked file as bytes for tamper detection; that is
        # the only contact. Nothing reads them as text or parses them.
        self.assertEqual(audit.scope_reads(("open", "read_text")), [])
        self.assertTrue(all(p.startswith(str(self.artifacts)) for p in audit.scope_reads()))

    def test_only_a_human_commit_of_the_configuration_grants_scope(self):
        _job, workdir = self.propose()
        # The human act: copy the rendered configuration and commit it.
        shutil.copy(workdir / "proposed-nullcode-config.json", self.repo / ".nullcode.json")
        self.assertIn("unapproved file", self.execute([NEW_PROD, GRANTED_TEST])["error"])
        git(self.repo, "add", ".nullcode.json")
        git(self.repo, "-c", "user.name=Human", "-c", "user.email=h@localhost",
            "commit", "-m", "Grant Main.java")

        result = self.execute([NEW_PROD, GRANTED_TEST])
        # Selection now passes; the run stops at the canned malformed plan.
        self.assertIn("did not return a JSON object", result["error"])
        self.assertEqual(result["attempts"][0]["phase"], "passed")
        self.assertEqual(result["attempts"][0]["result"]["selected_files"], [NEW_PROD, GRANTED_TEST])

    def test_no_automatic_handoff(self):
        job, _workdir = self.propose()
        with self.store.db() as db:
            rows = [dict(r) for r in db.execute("SELECT id, status FROM workflows")]
        self.assertEqual(rows, [{"id": job["id"], "status": "succeeded"}])
        self.assertIsNone(self.store.claim())
        self.assertEqual(git(self.repo, "log", "--oneline").count("\n"), 1)


class PublisherAuthorityTests(ScopeFixture):
    def test_scope_workflows_are_never_publishable(self):
        job, _workdir = self.propose()
        failed = dict(job, status="failed")
        with patch.object(publish_workflow, "gh_api", side_effect=AssertionError("network")), \
                patch.object(publish_workflow, "deliver", side_effect=AssertionError("deliver")), \
                patch.object(publish_workflow, "prepare_gradle", side_effect=AssertionError("gradle")):
            for candidate in (job, failed):
                with self.subTest(status=candidate["status"]):
                    with self.assertRaisesRegex(ValueError, "^Scope proposal workflows are not publishable$"):
                        publish_workflow.prepare(candidate, self.artifacts)

    def test_publish_cli_refuses_before_any_delivery(self):
        job, _workdir = self.propose()
        with patch.object(publish_workflow, "Store", return_value=self.store), \
                patch.object(publish_workflow, "deliver", side_effect=AssertionError("deliver")), \
                patch.object(sys, "argv", ["publish", str(job["id"]), "--repo", "o/r", "--publish"]):
            with self.assertRaisesRegex(ValueError, "not publishable"):
                publish_workflow.main()
        self.assertFalse((self.artifacts / f"workflow-{job['id']}" / "publication.json").exists())

    def test_seven_c_two_ignores_proposal_artifacts(self):
        harness = publish_tests.RepoExecutePublishTests()
        harness.make_workflow()
        baseline = publish_workflow.prepare(harness.job, harness.artifacts)

        # A proposal "granting" everything, dropped beside the executed workflow.
        widened = {"editable_files": ["src/main/java/lab/Slugs.java", "src/main/java/lab/TextStats.java"],
                   "editable_test_files": ["src/test/java/lab/SlugsTest.java", "src/test/java/lab/TextStatsTest.java"]}
        for name in SCOPE_ARTIFACTS:
            (harness.root / name).write_text(json.dumps(widened), encoding="utf-8")

        with ReadAudit() as audit:
            self.assertEqual(publish_workflow.prepare(harness.job, harness.artifacts), baseline)
        self.assertEqual(audit.scope_reads(), [])


class SharedPredicateTests(unittest.TestCase):
    """The helpers extracted for 7C-1 keep 7B/Gradle behavior and messages, and
    7B still calls them, so a scope proposal is judged by the same code."""

    PATHS = ["src/main/java/lab/A.java", "src/main/java/lab/B.java",
             "src/test/java/lab/ATest.java", "README.md"]

    def test_editable_files_rules(self):
        from nullcode.gradle.gradle_workflow import validate_editable_files
        validate_editable_files(["src/main/java/lab/A.java"], self.PATHS)
        for bad in (None, "src/main/java/lab/A.java", [],
                    ["src/main/java/lab/A.java"] * 2,
                    [f"src/main/java/lab/F{i}.java" for i in range(9)]):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "^Configure 1 to 8 unique editable Java files$"):
                    validate_editable_files(bad, self.PATHS)
        for bad in (["src/main/java/lab/Absent.java"], ["src/test/java/lab/ATest.java"], ["README.md"]):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "^Editable files must be committed Java production sources$"):
                    validate_editable_files(bad, self.PATHS)

    def test_editable_test_files_rules(self):
        from nullcode.repo.repo_execute_workflow import validate_editable_test_files
        validate_editable_test_files(["src/test/java/lab/ATest.java"], self.PATHS)
        for bad in (None, [], "x", ["src/test/java/lab/ATest.java"] * 2,
                    [f"src/test/java/lab/T{i}.java" for i in range(9)]):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "^Configure 1 to 8 unique editable_test_files for repo-execute-v1$"):
                    validate_editable_test_files(bad, self.PATHS)
        for bad in (["src/test/java/lab/Absent.java"], ["src/main/java/lab/A.java"], ["README.md"]):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "^editable_test_files must be committed Java test sources$"):
                    validate_editable_test_files(bad, self.PATHS)

    def test_seven_b_and_gradle_route_through_the_shared_helpers(self):
        from nullcode.gradle import gradle_workflow
        root = pathlib.Path(__file__).resolve().parent / "test-results" / uuid.uuid4().hex
        root.mkdir(parents=True)
        repo = build_patient_zero(root)
        for module, name in ((gradle_workflow, "validate_editable_files"),
                             (repo_execute_workflow, "validate_editable_test_files"),
                             (repo_execute_workflow, "check_source_limit")):
            with self.subTest(helper=name):
                with patch.object(module, name, side_effect=ValueError("shared helper")):
                    with self.assertRaisesRegex(ValueError, "shared helper"):
                        repo_execute_workflow.prepare_spec(repo, "main", TASK)
        self.assertTrue(repo_execute_workflow.prepare_spec(repo, "main", TASK))


class DispatchAndCliTests(unittest.TestCase):
    def test_worker_dispatches_scope_jobs_to_the_scope_workflow_only(self):
        job = {"id": 1, "repo_spec": json.dumps({"profile": "repo-scope-v1"})}
        from nullcode.repo import repo_workflow
        with patch.object(scope, "run_job") as run_scope, \
                patch.object(repo_workflow, "run_repo_job", side_effect=AssertionError("legacy")), \
                patch.object(repo_execute_workflow, "run_job", side_effect=AssertionError("execute")):
            java_workflow.run_repository_job("store", job)
        run_scope.assert_called_once_with("store", job)

    def test_submit_scope_queues_a_read_only_profile(self):
        root = pathlib.Path(__file__).resolve().parent / "test-results" / uuid.uuid4().hex
        root.mkdir(parents=True)
        repo = build_patient_zero(root)
        env = dict(os.environ, PYTHONPATH=str(SRC), NULLCODE_WORKFLOW_DIR=str(root / "db"),
                   NULLCODE_JOBS_DIR=str(root / "jobs"))
        output = subprocess.run(
            [sys.executable, "-m", "nullcode.core.java_workflow", "submit-scope",
             "--repo", str(repo), "--base", "main", "--task", TASK],
            capture_output=True, text=True, env=env, check=True).stdout
        submitted = json.loads(output)
        spec = json.loads(Store(root / "db").show(submitted["workflow_id"])["repo_spec"])
        self.assertEqual(spec["profile"], "repo-scope-v1")
        self.assertEqual(set(spec), {"profile", "repo", "base", "base_commit", "task"})

    def test_review_cli_requires_the_attestation(self):
        env = dict(os.environ, PYTHONPATH=str(SRC))
        run = subprocess.run([sys.executable, "-m", "nullcode.repo.repo_scope_review", "1"],
                             capture_output=True, text=True, env=env)
        self.assertEqual(run.returncode, 2)
        self.assertIn("--scope-reviewed", run.stderr)
        helptext = subprocess.run([sys.executable, "-m", "nullcode.repo.repo_scope_review", "--help"],
                                  capture_output=True, text=True, env=env, check=True).stdout
        self.assertIn("not edit authority", " ".join(helptext.split()))


if __name__ == "__main__":
    unittest.main()
