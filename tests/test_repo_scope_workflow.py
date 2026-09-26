"""Milestone 7C-1: repo-scope-v1 proposes scope; it never grants it.

Real Git fixtures (the Patient Zero shape), real isolated clones, canned
inference. No Ollama, Docker or Gradle is involved, and none may be.
"""

import hashlib
import inspect as pyinspect
import io
import json
import os
import pathlib
import subprocess
import unittest
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch

from test_patient_zero_compat import (
    APPROVED_PRODUCTION,
    APPROVED_TESTS,
    build_patient_zero,
    filler_production,
    filler_test,
)
from nullcode.core.java_workflow import Store
from nullcode.gradle.gradle_workflow import inspect as inspect_gradle
from nullcode.repo import repo_scope_review, repo_scope_workflow as scope
from nullcode.repo.repo_plan_workflow import committed_inventory
from nullcode.repo.repo_workflow import git

TASK = "Make Main print the word count of its argument using TextStats, and test it."

NEW_PROD = "src/main/java/lab/cli/Main.java"            # committed, not yet granted
NEW_TEST = "src/test/java/lab/SlugsTest.java"           # committed, not yet granted
GRANTED_PROD = "src/main/java/lab/Slugs.java"
GRANTED_TEST = "src/test/java/lab/TextStatsTest.java"
CONTEXT = "src/main/java/lab/TextStats.java"
OVERSIZED = "src/main/java/lab/service/TextAnalysis.java"   # 1144 bytes
PROTECTED = (".nullcode.json", "build.gradle", "settings.gradle", "gradle.properties")

RECORDED_GIT = []


def selection(production=(NEW_PROD,), tests=(GRANTED_TEST,), context=(CONTEXT,),
              reason="Main gains the feature; TextStatsTest covers it", **extra):
    data = {"production_files": list(production), "test_files": list(tests),
            "context_files": list(context), "reason": reason}
    data.update(extra)
    return data


def proposal(production=(NEW_PROD,), tests=(GRANTED_TEST,), context=(CONTEXT,),
             risks=("Main has no existing test",), summary="Add a word-count mode to Main.",
             **extra):
    def entries(names):
        return [{"path": name, "reason": "needed for " + name} for name in names]
    data = {"production_files": entries(production), "test_files": entries(tests),
            "context_files": entries(context), "risks": list(risks), "summary": summary}
    data.update(extra)
    return data


def commit_all(repo, message="fixture change", force=()):
    for name in force:
        git(repo, "add", "-f", "--", name)
    git(repo, "add", "--all")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=f@localhost",
        "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def write(repo, name, content):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def sized_java(package_path, type_name, size):
    """A committed Java source of exactly ``size`` bytes."""
    head = f"package lab.sized;\npublic final class {type_name} {{\n"
    tail = "}\n"
    padding = size - len(head) - len(tail) - len("// \n")
    source = head + "// " + "x" * padding + "\n" + tail
    assert len(source.encode()) == size
    return package_path, source


def tree_digest(path):
    """Every file under a directory, including .git, by content."""
    digest = {}
    for item in sorted(pathlib.Path(path).rglob("*")):
        if item.is_file() and not item.is_symlink():
            digest[str(item.relative_to(path))] = hashlib.sha256(item.read_bytes()).hexdigest()
    return digest


class ScopeHarness(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / "test-results" / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.repo = build_patient_zero(self.root)
        self.store = Store(self.root / "db")
        self.artifacts = self.root / "jobs"

    def spec(self, task=TASK):
        return scope.prepare_spec(self.repo, "main", task)

    def run_case(self, answers, spec=None, generate=None):
        spec = spec or self.spec()
        job_id = self.store.submit(repo_spec=spec)
        remaining = [a if isinstance(a, str) else json.dumps(a) for a in answers]
        self.prompts = []

        def canned(prompt, record):
            self.prompts.append(prompt)
            self.assertLessEqual(len(prompt.encode()), 2000)
            record(40 + len(self.prompts))
            return remaining.pop(0)

        self.before = tree_digest(self.repo)
        scope.run_job(self.store, self.store.claim(), generate or canned, self.artifacts)
        self.workdir = self.artifacts / f"workflow-{job_id}"
        return self.store.show(job_id)

    def assert_source_untouched(self):
        self.assertEqual(tree_digest(self.repo), self.before)
        self.assertEqual(git(self.repo, "status", "--porcelain", "--untracked-files=all"), "")

    def assert_no_valid_proposal(self):
        self.assertFalse((self.workdir / "scope-proposal.json").exists())
        self.assertFalse((self.workdir / "attempt-2" / "scope-proposal.json").exists())
        self.assertFalse((self.workdir / "repository.json").exists())

    def validators(self):
        base = git(self.repo, "rev-parse", "main")
        paths, config = inspect_gradle(self.repo, base)
        inventory = committed_inventory(self.repo, base)
        return base, paths, config, inventory

    def check_selection(self, data):
        base, paths, config, inventory = self.validators()
        return scope.validate_selection(data, inventory, self.repo, base, paths, config)

    def check_proposal(self, data, chosen=None):
        base, paths, config, inventory = self.validators()
        chosen = chosen or self.check_selection(selection())
        return scope.validate_proposal(data, chosen, inventory, self.repo, base, paths, config)


class WorkflowTests(ScopeHarness):
    def test_valid_proposal_succeeds_and_changes_nothing(self):
        result = self.run_case([selection(), proposal()])
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual([a["number"] for a in result["attempts"]], [1, 2])
        self.assertEqual([a["inference_job"] for a in result["attempts"]], [41, 42])
        self.assertTrue(all(a["phase"] == "passed" for a in result["attempts"]))

        saved = json.loads((self.workdir / "scope-proposal.json").read_text())
        self.assertIs(saved["edit_authority"], False)
        self.assertIn("grants nothing", saved["note"])
        self.assertEqual([e["path"] for e in saved["production_files"]], [NEW_PROD])
        self.assertEqual(saved["base_commit"], git(self.repo, "rev-parse", "main"))
        self.assertEqual(saved["committed_config_sha256"], hashlib.sha256(
            (self.repo / ".nullcode.json").read_bytes()).hexdigest())
        self.assertEqual(saved, json.loads((self.workdir / "attempt-2" / "scope-proposal.json").read_text()))

        for name in ("attempt-1/selection-prompt.txt", "attempt-1/selection-answer.txt",
                     "attempt-1/selection.json", "attempt-1/result.json",
                     "attempt-2/proposal-prompt.txt", "attempt-2/proposal-answer.txt",
                     "attempt-2/result.json", "repo-tree.txt"):
            self.assertTrue((self.workdir / name).is_file(), name)

        checkout = self.workdir / "repo"
        self.assertEqual(git(checkout, "status", "--porcelain"), "")
        self.assertEqual(git(checkout, "rev-parse", "HEAD"), saved["base_commit"])
        self.assertEqual(git(checkout, "remote"), "")
        self.assertFalse((self.workdir / "repository.json").exists())
        self.assert_source_untouched()
        # The committed authority is exactly what it was.
        self.assertNotIn(NEW_PROD, json.loads((self.repo / ".nullcode.json").read_text())["editable_files"])

    def test_call_two_may_narrow_and_drop_context(self):
        wide = selection(production=(NEW_PROD,), tests=(GRANTED_TEST,), context=(CONTEXT,))
        result = self.run_case([wide, proposal(context=())])
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        saved = json.loads((self.workdir / "scope-proposal.json").read_text())
        self.assertEqual(saved["context_files"], [])

    def test_state_machine_statuses(self):
        seen = []
        real = self.store.status

        def status(job, state, error=None):
            seen.append(state)
            real(job, state, error)

        with patch.object(self.store, "status", side_effect=status):
            self.run_case([selection(), proposal()])
        self.assertEqual(seen, ["inspecting", "selecting-scope", "validating-selection",
                                "proposing-scope", "validating-proposal", "succeeded"])

    def test_malformed_selection_fails_before_any_source_is_read(self):
        from nullcode.repo import repo_execute_workflow
        with patch.object(scope, "committed_text", side_effect=AssertionError("read")), \
                patch.object(scope, "committed_source_bytes", side_effect=AssertionError("read")), \
                patch.object(repo_execute_workflow, "committed_source_bytes",
                             side_effect=AssertionError("read")):
            result = self.run_case(["not json at all", proposal()])
        self.assertEqual(result["status"], "failed")
        self.assertIn("did not return a JSON object", result["error"])
        self.assertEqual(len(self.prompts), 1)
        self.assertTrue((self.workdir / "attempt-1" / "selection-answer.txt").is_file())
        self.assertFalse((self.workdir / "attempt-1" / "selection.json").exists())
        self.assertFalse((self.workdir / "attempt-2").exists())
        self.assertIs(json.loads((self.workdir / "attempt-1" / "result.json").read_text())["passed"], False)
        self.assert_no_valid_proposal()
        self.assert_source_untouched()

    def test_invalid_selection_ends_the_workflow_without_a_second_call(self):
        result = self.run_case([selection(production=(".nullcode.json",)), proposal()])
        self.assertEqual(result["status"], "failed")
        self.assertIn("protected path", result["error"])
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(result["attempts"][-1]["phase"], "error")
        self.assert_no_valid_proposal()

    def test_rejected_proposal_leaves_only_failure_evidence(self):
        result = self.run_case([selection(), proposal(production=(CONTEXT,), context=())])
        self.assertEqual(result["status"], "failed")
        self.assertIn("promotes context-only", result["error"])
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual(result["attempts"][-1]["number"], 2)
        self.assertTrue((self.workdir / "attempt-2" / "proposal-answer.txt").is_file())
        self.assertIs(json.loads((self.workdir / "attempt-2" / "result.json").read_text())["passed"], False)
        self.assert_no_valid_proposal()
        self.assert_source_untouched()

    def test_inference_failure_is_terminal_with_no_retry(self):
        calls = []

        def failing(prompt, record):
            calls.append(prompt)
            raise RuntimeError("Inference failed: model offline")

        result = self.run_case([], generate=failing)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(calls), 1)
        self.assert_no_valid_proposal()

    def test_checkout_mutation_fails_the_read_only_check(self):
        answers = [json.dumps(selection()), json.dumps(proposal())]

        def mutating(prompt, record):
            record(1)
            answer = answers.pop(0)
            if not answers:
                (self.workdir / "repo" / "unexpected.txt").write_text("x", encoding="utf-8")
            return answer

        spec = self.spec()
        job_id = self.store.submit(repo_spec=spec)
        self.workdir = self.artifacts / f"workflow-{job_id}"
        scope.run_job(self.store, self.store.claim(), mutating, self.artifacts)
        result = self.store.show(job_id)
        self.assertEqual(result["status"], "failed")
        self.assertIn("Checkout changed during read-only", result["error"])
        self.assert_no_valid_proposal()

    def test_git_is_only_used_read_only(self):
        """The workflow clones and reads. It never adds, commits, pushes, or
        runs any git command against the source repository except the clone."""
        from nullcode.repo import repo_workflow
        calls = []
        real = subprocess.run

        def recording(args, **kwargs):
            if args and args[0] == "git":
                calls.append(list(args))
            return real(args, **kwargs)

        spec = self.spec()
        with patch.object(repo_workflow.subprocess, "run", side_effect=recording):
            result = self.run_case([selection(), proposal()], spec=spec)
        self.assertEqual(result["status"], "succeeded", result.get("error"))

        def subcommand(args):
            rest = args[args.index("-C") + 2:]
            return rest[0]

        used = {subcommand(args) for args in calls}
        self.assertLessEqual(used, {"clone", "checkout", "remote", "ls-tree", "show",
                                    "cat-file", "status", "rev-parse"})
        self.assertFalse(used & {"add", "commit", "push", "reset", "apply", "config"})
        source = str(self.repo)
        for args in calls:
            if args[args.index("-C") + 1] == source:
                self.fail("git ran inside the source repository: " + " ".join(args))
        checkouts = [a for a in calls if subcommand(a) == "checkout"]
        self.assertEqual(len(checkouts), 1)
        self.assertIn("--detach", checkouts[0])
        self.assertIn(["remote", "remove", "origin"],
                      [a[a.index("-C") + 2:] for a in calls if subcommand(a) == "remote"])

    def test_no_gradle_docker_or_generated_code_execution(self):
        from nullcode.core import java_workflow
        from nullcode.gradle import gradle_workflow
        with patch.object(gradle_workflow, "verify", side_effect=AssertionError("gradle")), \
                patch.object(gradle_workflow, "command", side_effect=AssertionError("docker")), \
                patch.object(java_workflow, "command", side_effect=AssertionError("docker")), \
                patch.object(java_workflow, "verify", side_effect=AssertionError("javac")):
            result = self.run_case([selection(), proposal()])
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.assertNotIn("verify_fn", pyinspect.signature(scope.run_job).parameters)

    def test_wrong_profile_spec_is_refused(self):
        spec = dict(self.spec(), profile="repo-execute-v1")
        result = self.run_case([selection(), proposal()], spec=spec)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.prompts, [])

    def test_unknown_keys_are_ignored_and_never_persisted(self):
        result = self.run_case([
            selection(editable_files=[OVERSIZED], grant=True),
            proposal(editable_test_files=["src/test/java/lab/service/TextAnalysisTest.java"],
                     write_config=True),
        ])
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        saved = json.loads((self.workdir / "scope-proposal.json").read_text())
        for key in ("editable_files", "editable_test_files", "grant", "write_config"):
            self.assertNotIn(key, saved)
        self.assertNotIn(OVERSIZED, json.dumps(saved))


class PrepareSpecTests(ScopeHarness):
    def test_task_limits(self):
        for task in ("", "   ", "x" * 501):
            with self.subTest(task=task[:5]):
                with self.assertRaisesRegex(ValueError, "1 to 500 bytes"):
                    scope.prepare_spec(self.repo, "main", task)
        self.assertEqual(scope.prepare_spec(self.repo, "main", "x" * 500)["profile"], "repo-scope-v1")

    def test_committed_symlink_is_refused(self):
        os.symlink("Slugs.java", self.repo / "src/main/java/lab/Link.java")
        commit_all(self.repo, "add symlink")
        with self.assertRaisesRegex(ValueError, "Only ordinary tracked files"):
            self.spec()

    def test_symlink_at_a_pinned_base_fails_the_run(self):
        spec = self.spec()
        os.symlink("Slugs.java", self.repo / "src/main/java/lab/Link.java")
        spec["base_commit"] = commit_all(self.repo, "add symlink")
        result = self.run_case([selection(), proposal()], spec=spec)
        self.assertEqual(result["status"], "failed")
        self.assertIn("Only ordinary tracked files", result["error"])
        self.assertEqual(self.prompts, [])

    def test_malformed_committed_test_scope_is_refused(self):
        config = json.loads((self.repo / ".nullcode.json").read_text())
        config["editable_test_files"] = "src/test/java/lab/TextStatsTest.java"
        (self.repo / ".nullcode.json").write_text(json.dumps(config), encoding="utf-8")
        commit_all(self.repo)
        with self.assertRaisesRegex(ValueError, "list of paths"):
            self.spec()

    def test_absent_committed_test_scope_can_be_created_by_a_grant(self):
        config = json.loads((self.repo / ".nullcode.json").read_text())
        del config["editable_test_files"]
        (self.repo / ".nullcode.json").write_text(json.dumps(config), encoding="utf-8")
        commit_all(self.repo)
        result = self.run_case([selection(), proposal()])
        self.assertEqual(result["status"], "succeeded", result.get("error"))


class SelectionValidationTests(ScopeHarness):
    def rejects(self, data, message):
        with self.assertRaisesRegex(ValueError, message):
            self.check_selection(data)

    def test_valid_selection(self):
        self.assertEqual(self.check_selection(selection()), {
            "production_files": [NEW_PROD], "test_files": [GRANTED_TEST],
            "context_files": [CONTEXT],
            "reason": "Main gains the feature; TextStatsTest covers it"})

    def test_non_object(self):
        for data in ([], "x", None, 3):
            with self.subTest(data=data):
                self.rejects(data, "must be a JSON object")

    def test_missing_or_mistyped_lists(self):
        for key in ("production_files", "test_files", "context_files"):
            for bad in (None, "path", [1], [""], [None], {"a": 1}):
                with self.subTest(key=key, bad=bad):
                    data = selection()
                    if bad is None:
                        del data[key]
                    else:
                        data[key] = bad
                    self.rejects(data, f"{key} must be a list of path strings")

    def test_reason_required(self):
        for bad in (None, "", "   ", 5, ["x"]):
            with self.subTest(bad=bad):
                data = selection()
                if bad is None:
                    del data["reason"]
                else:
                    data["reason"] = bad
                self.rejects(data, "requires a reason")

    def test_nonexistent_and_uncommitted_paths(self):
        write(self.repo, "src/main/java/lab/Uncommitted.java", "package lab; class Uncommitted {}\n")
        for name in ("src/main/java/lab/Missing.java", "src/main/java/lab/Uncommitted.java",
                     "/etc/passwd", "../outside.java", "src/main/java/../../../x.java"):
            with self.subTest(name=name):
                self.rejects(selection(production=(name,)), "outside the committed inventory")

    def test_excluded_directories_are_never_inventory(self):
        for name in ("src/main/java/lab/build/Gen.java", "src/main/java/lab/out/Gen.java"):
            write(self.repo, name, "package lab; class Gen {}\n")
        commit_all(self.repo, "generated", force=("src/main/java/lab/build/Gen.java",))
        paths, _ = inspect_gradle(self.repo, "main")
        for name in ("src/main/java/lab/build/Gen.java", "src/main/java/lab/out/Gen.java"):
            with self.subTest(name=name):
                self.assertIn(name, paths)  # committed ...
                self.rejects(selection(production=(name,)), "outside the committed inventory")

    def test_protected_paths_can_never_be_edit_scope(self):
        for name in PROTECTED:
            for cls in ("production", "tests"):
                with self.subTest(name=name, cls=cls):
                    data = selection(production=(name,)) if cls == "production" else selection(tests=(name,))
                    self.rejects(data, f"protected path: {name}")

    def test_protected_names_rejected_at_any_depth(self):
        base, paths, config, _ = self.validators()
        for name in ("src/main/java/lab/build.gradle", "src/test/java/.nullcode.json"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "protected path"):
                    scope.validate_edit_scope([name], [GRANTED_TEST], self.repo, base, paths, config)

    def test_protected_file_as_read_only_context_grants_nothing(self):
        chosen = self.check_selection(selection(context=(".nullcode.json",)))
        self.assertEqual(chosen["context_files"], [".nullcode.json"])

    def test_duplicates(self):
        cases = [
            selection(production=(NEW_PROD, NEW_PROD)),
            selection(tests=(GRANTED_TEST, GRANTED_TEST)),
            selection(production=(NEW_PROD,), tests=(NEW_PROD,)),
            selection(context=(NEW_PROD,)),
            selection(context=(GRANTED_TEST,)),
        ]
        for data in cases:
            with self.subTest(data=data):
                self.rejects(data, "more than once")

    def test_wrong_path_class(self):
        for name in (GRANTED_TEST, "README.md", "src/main/java/lab/../lab/Slugs.java"):
            with self.subTest(production=name):
                data = selection(production=(name,), tests=("src/test/java/lab/format/CaseConverterTest.java",))
                if name.startswith("src/main/java/lab/.."):
                    self.rejects(data, "outside the committed inventory")
                else:
                    self.rejects(data, "Not a Java production source")
        for name in (GRANTED_PROD, "README.md"):
            with self.subTest(test=name):
                self.rejects(selection(tests=(name,)), "Not a Java test source")

    def test_both_classes_required(self):
        self.rejects(selection(production=()), "at least one production")
        self.rejects(selection(tests=()), "at least one Java test")

    def test_more_than_three_edit_files(self):
        self.rejects(selection(production=(NEW_PROD, GRANTED_PROD),
                               tests=(GRANTED_TEST, NEW_TEST)), "exceeds 3 edit files")

    def test_three_edit_files_accepted(self):
        chosen = self.check_selection(selection(production=(NEW_PROD, GRANTED_PROD), context=()))
        self.assertEqual(len(chosen["production_files"]) + len(chosen["test_files"]), 3)

    def test_oversized_source(self):
        self.rejects(selection(production=(OVERSIZED,)), "900-byte 7B source limit")

    def test_context_bounds(self):
        self.rejects(selection(context=("src/main/java/lab/Missing.java",)), "outside the committed inventory")
        self.rejects(selection(context=(CONTEXT, "README.md")), "more than 1 context file")
        self.rejects(selection(context=("src/test/java/lab/service/TextAnalysisTest.java",)),
                     "Context file .* bytes; the limit is 900")


class SizeBoundaryTests(ScopeHarness):
    def setUp(self):
        super().setUp()
        for name, size in (("Nine00", 900), ("Nine01", 901)):
            path, source = sized_java(f"src/main/java/lab/sized/{name}.java", name, size)
            write(self.repo, path, source)
        commit_all(self.repo, "sized sources")

    def test_nine_hundred_bytes_is_the_exact_edit_limit(self):
        self.check_selection(selection(production=("src/main/java/lab/sized/Nine00.java",), context=()))
        with self.assertRaisesRegex(ValueError, "900-byte 7B source limit"):
            self.check_selection(selection(production=("src/main/java/lab/sized/Nine01.java",), context=()))

    def test_nine_hundred_bytes_is_the_exact_context_limit(self):
        self.check_selection(selection(context=("src/main/java/lab/sized/Nine00.java",)))
        with self.assertRaisesRegex(ValueError, "the limit is 900"):
            self.check_selection(selection(context=("src/main/java/lab/sized/Nine01.java",)))


class ResultingScopeCeilingTests(unittest.TestCase):
    """The grant a proposal implies must stay inside the shared 8 + 8 ceilings."""

    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / "test-results" / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def check(self, repo, production, tests):
        base = git(repo, "rev-parse", "main")
        paths, config = inspect_gradle(repo, base)
        inventory = committed_inventory(repo, base)
        return scope.validate_selection(
            selection(production=production, tests=tests, context=()),
            inventory, repo, base, paths, config)

    def test_production_ceiling(self):
        approved = APPROVED_PRODUCTION + [NEW_PROD] + [filler_production(i)[0] for i in range(2)]
        repo = build_patient_zero(self.root / "p8", production=approved, fillers=3)
        self.assertEqual(len(approved), 8)
        with self.assertRaisesRegex(ValueError, "exceed 8 editable production files \\(9\\)"):
            self.check(repo, (filler_production(2)[0],), (GRANTED_TEST,))
        # Re-proposing an already-granted file does not grow the list.
        self.check(repo, (GRANTED_PROD,), (GRANTED_TEST,))

        seven = build_patient_zero(self.root / "p7", production=approved[:7], fillers=3)
        self.check(seven, (filler_production(1)[0],), (GRANTED_TEST,))

    def test_test_ceiling(self):
        approved = APPROVED_TESTS + [filler_test(i)[0] for i in range(5)]
        repo = build_patient_zero(self.root / "t8", tests=approved, fillers=6)
        self.assertEqual(len(approved), 8)
        with self.assertRaisesRegex(ValueError, "exceed 8 editable test files \\(9\\)"):
            self.check(repo, (GRANTED_PROD,), (filler_test(5)[0],))
        self.check(repo, (GRANTED_PROD,), (GRANTED_TEST,))

        seven = build_patient_zero(self.root / "t7", tests=approved[:7], fillers=6)
        self.check(seven, (GRANTED_PROD,), (filler_test(4)[0],))

    def test_grant_config_is_pure_and_append_only(self):
        config = {"profile": "gradle-junit-v1", "editable_files": ["a"], "minimum_tests": 3}
        granted = scope.grant_config(config, ["a", "b"], ["t"])
        self.assertEqual(granted, {"profile": "gradle-junit-v1", "editable_files": ["a", "b"],
                                   "minimum_tests": 3, "editable_test_files": ["t"]})
        self.assertEqual(config, {"profile": "gradle-junit-v1", "editable_files": ["a"], "minimum_tests": 3})

    def test_resulting_configuration_passes_seven_b_itself(self):
        """Every granted file is re-checked with 7B's own validators, including
        files that were granted before this proposal."""
        from nullcode.gradle import gradle_workflow
        from nullcode.repo import repo_execute_workflow
        repo = build_patient_zero(self.root / "shared")
        self.assertIs(scope.validate_editable_test_files, repo_execute_workflow.validate_editable_test_files)
        self.assertIs(scope.validate_editable_files, gradle_workflow.validate_editable_files)
        self.assertIs(scope.validate_execute_selection, repo_execute_workflow.validate_selection)
        self.assertIs(scope.check_source_limit, repo_execute_workflow.check_source_limit)
        for name in ("validate_editable_test_files", "validate_editable_files",
                     "validate_execute_selection"):
            with self.subTest(shared=name):
                with patch.object(scope, name, side_effect=ValueError("7B says no")):
                    with self.assertRaisesRegex(ValueError, "repo-execute-v1 rejects: 7B says no"):
                        self.check(repo, (GRANTED_PROD,), (GRANTED_TEST,))


class ProposalValidationTests(ScopeHarness):
    def rejects(self, data, message, chosen=None):
        with self.assertRaisesRegex(ValueError, message):
            self.check_proposal(data, chosen)

    def test_valid_proposal(self):
        validated = self.check_proposal(proposal())
        self.assertEqual(validated["summary"], "Add a word-count mode to Main.")
        self.assertEqual(validated["context_files"], [{"path": CONTEXT, "reason": "needed for " + CONTEXT}])

    def test_shape(self):
        self.rejects([], "must be a JSON object")
        for key in ("production_files", "test_files", "context_files"):
            for bad, message in ((None, "requires a"), ("x", "requires a"),
                                 (["x"], "entries must be objects"),
                                 ([{"reason": "r"}], "entry requires a path"),
                                 ([{"path": 5, "reason": "r"}], "entry requires a path"),
                                 ([{"path": NEW_PROD}], "requires a reason"),
                                 ([{"path": NEW_PROD, "reason": " "}], "requires a reason")):
                with self.subTest(key=key, bad=bad):
                    data = proposal()
                    if bad is None:
                        del data[key]
                    else:
                        data[key] = bad
                    self.rejects(data, message)
        for bad in (None, "risk", [1]):
            with self.subTest(risks=bad):
                data = proposal()
                data["risks"] = bad
                self.rejects(data, "risks must be a list of strings")
        for bad in (None, "", "  ", 3):
            with self.subTest(summary=bad):
                data = proposal()
                data["summary"] = bad
                self.rejects(data, "requires a summary")

    def test_call_two_cannot_introduce_a_path(self):
        self.rejects(proposal(production=(GRANTED_PROD,)), "introduces .*Slugs.java.*did not nominate")
        self.rejects(proposal(tests=(NEW_TEST,)), "did not nominate")
        self.rejects(proposal(context=("README.md",)), "did not nominate")

    def test_call_two_cannot_promote_context_to_edit(self):
        self.rejects(proposal(production=(CONTEXT,), context=()), "promotes context-only")
        chosen = self.check_selection(selection(context=("src/test/java/lab/format/CaseConverterTest.java",)))
        self.rejects(proposal(tests=("src/test/java/lab/format/CaseConverterTest.java",), context=()),
                     "promotes context-only", chosen)

    def test_call_two_cannot_move_a_path_between_classes(self):
        self.rejects(proposal(production=(NEW_PROD,), tests=(GRANTED_TEST,),
                              context=(NEW_PROD,)), "more than once")
        self.rejects(proposal(production=(), tests=(GRANTED_TEST,), context=(NEW_PROD,)),
                     "moves .*Main.java from production_files to context_files")
        chosen = self.check_selection(selection(production=(NEW_PROD, GRANTED_PROD), context=()))
        self.rejects(proposal(production=(NEW_PROD,), tests=(GRANTED_TEST, GRANTED_PROD), context=()),
                     "moves .*Slugs.java from production_files to test_files", chosen)

    def test_call_two_may_narrow_but_not_below_the_seven_b_shape(self):
        chosen = self.check_selection(selection(production=(NEW_PROD, GRANTED_PROD), context=()))
        narrowed = self.check_proposal(proposal(production=(GRANTED_PROD,), context=()), chosen)
        self.assertEqual([e["path"] for e in narrowed["production_files"]], [GRANTED_PROD])
        self.rejects(proposal(production=(), context=()), "at least one production", chosen)
        self.rejects(proposal(tests=()), "at least one Java test")

    def test_duplicates_in_the_final_proposal(self):
        self.rejects(proposal(production=(NEW_PROD, NEW_PROD)), "more than once")
        self.rejects(proposal(production=(NEW_PROD,), tests=(NEW_PROD,)), "more than once")


class PromptBudgetTests(ScopeHarness):
    def budget_selection(self, production=(NEW_PROD,), tests=(GRANTED_TEST,), context=()):
        return {"production_files": list(production), "test_files": list(tests),
                "context_files": list(context)}

    def test_selection_prompt_limit_is_exact(self):
        inventory = committed_inventory(self.repo, "main")
        fixed = len(scope.selection_prompt("", inventory).encode())
        self.assertEqual(len(scope.selection_prompt("x" * (2000 - fixed), inventory).encode()), 2000)
        with self.assertRaisesRegex(ValueError, "needs 2001/2000 bytes; nothing truncated"):
            scope.selection_prompt("x" * (2001 - fixed), inventory)

    def test_proposal_prompt_limit_is_exact(self):
        chosen = self.budget_selection()
        evidence = scope.proposal_evidence(self.repo, "main", chosen)
        fixed = len(scope.proposal_prompt("", chosen, evidence).encode())
        self.assertEqual(len(scope.proposal_prompt("x" * (2000 - fixed), chosen, evidence).encode()), 2000)
        with self.assertRaisesRegex(ValueError, "needs 2001/2000 bytes; nothing truncated"):
            scope.proposal_prompt("x" * (2001 - fixed), chosen, evidence)

    def test_evidence_is_complete_never_clipped(self):
        chosen = self.budget_selection(context=(CONTEXT,))
        prompt = scope.proposal_prompt(TASK, chosen, scope.proposal_evidence(self.repo, "main", chosen))
        for name in (NEW_PROD, GRANTED_TEST, CONTEXT):
            for line in (self.repo / name).read_text().splitlines():
                if line.strip():
                    self.assertIn(line.strip(), prompt, name)

    def test_patient_zero_approved_one_plus_one_shapes_fit(self):
        for production in APPROVED_PRODUCTION:
            for test in APPROVED_TESTS:
                with self.subTest(production=production, test=test):
                    chosen = self.budget_selection((production,), (test,))
                    prompt = scope.proposal_prompt(
                        TASK, chosen, scope.proposal_evidence(self.repo, "main", chosen))
                    self.assertLessEqual(len(prompt.encode()), 2000)

    def test_realistic_selection_prompt_fits(self):
        prompt = scope.selection_prompt(TASK, committed_inventory(self.repo, "main"))
        self.assertLess(len(prompt.encode()), 1300)

    def test_over_budget_evidence_fails_closed_in_the_workflow(self):
        big = selection(production=("src/main/java/lab/format/ReportFormatter.java",),
                        tests=(NEW_TEST,), context=())
        result = self.run_case([big, proposal()])
        self.assertEqual(result["status"], "failed")
        self.assertRegex(result["error"], r"needs \d{4}/2000 bytes; nothing truncated")
        self.assertEqual(len(self.prompts), 1)
        self.assertFalse((self.workdir / "attempt-2" / "proposal-prompt.txt").exists())
        self.assertEqual(result["attempts"][-1]["number"], 2)
        self.assert_no_valid_proposal()

    def test_worst_case_selection_prompt_fails_closed_in_the_workflow(self):
        repo = build_patient_zero(self.root / "wide", fillers=8)
        spec = scope.prepare_spec(repo, "main", "y" * 500)
        result = self.run_case([selection(), proposal()], spec=spec)
        self.assertEqual(result["status"], "failed")
        self.assertRegex(result["error"], r"Scope selection prompt needs \d{4}/2000 bytes; nothing truncated")
        self.assertEqual(self.prompts, [])
        self.assertFalse((self.workdir / "attempt-1" / "selection-prompt.txt").exists())

    def test_context_cap_is_one(self):
        self.assertEqual(scope.MAX_CONTEXT_FILES, 1)
        self.assertEqual(scope.MAX_CONTEXT_FILE_BYTES, 900)
        self.assertEqual(scope.PROMPT_LIMIT, 2000)


class ScopeReviewTests(ScopeHarness):
    def succeeded(self, sel=None, prop=None):
        result = self.run_case([sel or selection(), prop or proposal()])
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.job = result
        return result

    def review_files(self):
        return [self.workdir / n for n in
                ("scope-review.json", "proposed-nullcode-config.json", "proposed-nullcode.patch")]

    def test_flag_is_required_and_never_defaulted(self):
        self.succeeded()
        parameter = pyinspect.signature(repo_scope_review.render_grant).parameters["scope_reviewed"]
        self.assertIs(parameter.default, pyinspect.Parameter.empty)
        for value in (False, None, 1, "true", "--scope-reviewed"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "pass --scope-reviewed"):
                    repo_scope_review.render_grant(self.job, self.artifacts, value)
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                repo_scope_review.main([str(self.job["id"])], self.store, self.artifacts)
        for path in self.review_files():
            self.assertFalse(path.exists(), path)

    def test_review_renders_the_grant_and_writes_nothing_else(self):
        self.succeeded()
        checkout = self.workdir / "repo"
        before_checkout = tree_digest(checkout)
        before_root = set(p for p in self.workdir.rglob("*"))
        out = io.StringIO()
        with redirect_stdout(out):
            repo_scope_review.main([str(self.job["id"]), "--scope-reviewed"], self.store, self.artifacts)
        text = out.getvalue()
        self.assertIn("NOT applied", text)
        self.assertIn("NOT edit authority", text)
        self.assertIn('+    "' + NEW_PROD + '"', text)

        record = json.loads((self.workdir / "scope-review.json").read_text())
        self.assertIs(record["edit_authority"], False)
        self.assertIs(record["applied"], False)
        self.assertIs(record["scope_reviewed"], True)
        self.assertEqual(record["added_editable_files"], [NEW_PROD])
        self.assertEqual(record["added_editable_test_files"], [])
        self.assertTrue(record["configuration_change_required"])

        committed = json.loads((self.repo / ".nullcode.json").read_text())
        proposed = json.loads((self.workdir / "proposed-nullcode-config.json").read_text())
        self.assertEqual(proposed, scope.grant_config(committed, [NEW_PROD], [GRANTED_TEST]))
        patch_text = (self.workdir / "proposed-nullcode.patch").read_text()
        self.assertTrue(patch_text.startswith("--- a/.nullcode.json\n+++ b/.nullcode.json\n"))

        # Only the three review artifacts are new; target and checkout untouched.
        after_root = set(p for p in self.workdir.rglob("*"))
        self.assertEqual(after_root - before_root, set(self.review_files()))
        self.assertEqual(tree_digest(checkout), before_checkout)
        self.assert_source_untouched()

    def test_review_uses_only_read_only_git(self):
        self.succeeded()
        from nullcode.repo import repo_workflow
        calls = []
        real = subprocess.run

        def recording(args, **kwargs):
            if args and args[0] == "git":
                calls.append(list(args))
            return real(args, **kwargs)

        with patch.object(repo_workflow.subprocess, "run", side_effect=recording):
            repo_scope_review.render_grant(self.job, self.artifacts, True)
        used = {a[a.index("-C") + 2] for a in calls}
        self.assertLessEqual(used, {"show", "ls-tree", "cat-file"})
        self.assertFalse(any(a[a.index("-C") + 1] == str(self.repo) for a in calls))

    def test_new_test_scope_is_rendered(self):
        self.succeeded(selection(production=(GRANTED_PROD,), tests=(NEW_TEST,), context=()),
                       proposal(production=(GRANTED_PROD,), tests=(NEW_TEST,), context=()))
        record, patch_text = repo_scope_review.render_grant(self.job, self.artifacts, True)
        self.assertEqual(record["added_editable_files"], [])
        self.assertEqual(record["added_editable_test_files"], [NEW_TEST])
        self.assertIn("+    \"" + NEW_TEST + "\"", patch_text)

    def test_already_granted_scope_needs_no_change(self):
        self.succeeded(selection(production=(GRANTED_PROD,), context=()),
                       proposal(production=(GRANTED_PROD,), context=()))
        record, patch_text = repo_scope_review.render_grant(self.job, self.artifacts, True)
        self.assertFalse(record["configuration_change_required"])
        self.assertEqual(json.loads((self.workdir / "proposed-nullcode-config.json").read_text()),
                         json.loads((self.repo / ".nullcode.json").read_text()))

    def test_tampered_proposal_is_re_derived_not_trusted(self):
        self.succeeded()
        path = self.workdir / "scope-proposal.json"
        original = json.loads(path.read_text())
        tampering = [
            (lambda p: p["production_files"].append({"path": ".nullcode.json", "reason": "x"}), "protected"),
            (lambda p: p["production_files"].append({"path": OVERSIZED, "reason": "x"}), "900-byte"),
            (lambda p: p["test_files"].append({"path": "src/test/java/lab/Nope.java", "reason": "x"}), "uninventoried"),
            (lambda p: p.update(committed_config_sha256="0" * 64), "does not match the proposal's base"),
            (lambda p: p.update(edit_authority=True), "does not match its workflow"),
            (lambda p: p.update(base_commit="0" * 40), "does not match its workflow"),
            (lambda p: p.update(production_files="x"), "malformed"),
        ]
        for mutate, message in tampering:
            with self.subTest(message=message):
                doctored = json.loads(json.dumps(original))
                mutate(doctored)
                path.write_text(json.dumps(doctored))
                with self.assertRaisesRegex(ValueError, message):
                    repo_scope_review.render_grant(self.job, self.artifacts, True)
                for artifact in self.review_files():
                    self.assertFalse(artifact.exists())
        path.write_text(json.dumps(original))
        repo_scope_review.render_grant(self.job, self.artifacts, True)

    def test_only_succeeded_scope_workflows_can_be_reviewed(self):
        failed = self.run_case(["nope"])
        with self.assertRaisesRegex(ValueError, "Only a succeeded scope proposal"):
            repo_scope_review.render_grant(failed, self.artifacts, True)
        other = dict(failed, status="succeeded",
                     repo_spec=json.dumps({"profile": "repo-execute-v1"}))
        with self.assertRaisesRegex(ValueError, "Only repo-scope-v1"):
            repo_scope_review.render_grant(other, self.artifacts, True)


if __name__ == "__main__":
    unittest.main()
