"""Real Git/SQLite integration tests; inference and Docker verification are simulated."""
import json
import pathlib
import unittest
import uuid
import copy

import nullcode.repo.accepted_workflow as a
from nullcode.core.java_workflow import Store
from nullcode.gradle.gradle_workflow import PROFILE as GRADLE_PROFILE
from nullcode.publish.prepare_acceptance import ASSETS as ACCEPTANCE_ASSETS

BASE_SOURCE = '''package lab;
public class TextStats {
    public static int countWords(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        return text.isBlank() ? 0 : text.trim().split("\\\\s+").length;
    }
}
'''
GOOD = BASE_SOURCE[:-2] + '''    public static int countWordsLongerThan(String text, int minLength) {
        if (text == null || minLength < 0) throw new IllegalArgumentException();
        if (text.isBlank()) return 0;
        int count = 0;
        for (String word : text.trim().split("\\\\s+")) {
            if (word.length() > minLength) count++;
        }
        return count;
    }
}
'''

COMPILE_LOG = '''> Task :compileJava FAILED
/work/project/src/main/java/lab/TextStats.java:12: error: cannot find symbol
                         .stream()
                         ^
  symbol:   method stream()
  location: class String[]
1 error
'''


def compile_failure(result):
    result.update(passed=False, repairable=True, tests=None, junit=None)
    result["compile"] = {"exit_code": 1, "timed_out": False, "log": COMPILE_LOG}
    return result


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.result = compile_failure({"cleanup": {"exit_code": 0, "timed_out": False}})
        self.target = "src/main/java/lab/TextStats.java"

    def test_matches_actual_error(self):
        rule = a.compiler_repair_rule(self.result, self.target)
        self.assertEqual(rule["id"], "javac-string-array-stream-loop-v1")
        self.assertIn("ordinary for loop", rule["guidance"])

    def test_unrelated_errors_do_not_trigger(self):
        changes = [("String[]", "int[]"), ("String[]", "String"),
                   ("stream()", "length()"), ("stream()", "stream(int)"),
                   ("src/main/java/lab/TextStats.java", "src/test/java/lab/TextStatsTest.java"),
                   ("cannot find symbol", "incompatible types")]
        for old, new in changes:
            with self.subTest(new=new):
                result = copy.deepcopy(self.result)
                result["compile"]["log"] = COMPILE_LOG.replace(old, new)
                self.assertIsNone(a.compiler_repair_rule(result, self.target))

    def test_separate_error_blocks_cannot_be_combined(self):
        self.result["compile"]["log"] = COMPILE_LOG.replace("location: class String[]", "location: class Widget") + COMPILE_LOG.replace("method stream()", "method length()")
        self.assertIsNone(a.compiler_repair_rule(self.result, self.target))

    def test_junit_text_cannot_trigger(self):
        self.result["compile"].update(exit_code=0, log="BUILD SUCCESSFUL")
        self.result["tests"] = {"log": COMPILE_LOG}
        self.assertIsNone(a.compiler_repair_rule(self.result, self.target))

    def test_timeout_failed_cleanup_and_nonrepairable_do_not_trigger(self):
        for field, key, value in [("compile", "timed_out", True), ("cleanup", "exit_code", 1),
                                  ("cleanup", "timed_out", True)]:
            result = copy.deepcopy(self.result)
            result[field][key] = value
            self.assertIsNone(a.compiler_repair_rule(result, self.target))
        self.result["repairable"] = False
        self.assertIsNone(a.compiler_repair_rule(self.result, self.target))

    def test_guidance_is_counted_in_budget(self):
        contract = {"target": self.target, "task": "Implement counting."}
        source = GOOD
        base = a.prompt_for(contract, source, "compile error")
        source += " " * (1990-len(base.encode()))
        # Whitespace-only lines are compacted, so use a nonempty comment instead.
        source = GOOD + "\n//" + "x" * (1980-len(base.encode()))
        self.assertLessEqual(len(a.prompt_for(contract, source, "compile error").encode()), 2000)
        with self.assertRaisesRegex(ValueError, "nothing truncated"):
            a.prompt_for(contract, source, "compile error", a.STREAM_LOOP_GUIDANCE)

    def test_guidance_requires_repair(self):
        with self.assertRaisesRegex(ValueError, "requires a repair"):
            a.prompt_for({"target": self.target, "task": "Count."}, GOOD, guidance=a.STREAM_LOOP_GUIDANCE)


class AcceptedTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).parent / "test-results" / uuid.uuid4().hex
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True)
        self.jobs = self.root / "jobs"
        self.store = Store(self.root / "db")
        self.target = "src/main/java/lab/TextStats.java"
        self.test = "src/test/java/lab/TextStatsAcceptanceTest.java"
        files = {self.target: BASE_SOURCE,
                 self.test: (ACCEPTANCE_ASSETS / "TextStatsAcceptanceTest.java").read_text(),
                 ".nullcode.json": json.dumps({"profile": "gradle-junit-v1", "editable_files": [self.target],
                                               "editable_test_files": [self.test], "minimum_tests": 12}),
                 ".nullcode-acceptance.json": (ACCEPTANCE_ASSETS / "contract.json").read_text()}
        for name in ("build.gradle", "settings.gradle", "gradle.properties"):
            files[name] = (GRADLE_PROFILE / name).read_text()
        for name, text in files.items():
            p = self.repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        a.git(self.repo, "init", "-b", "main")
        a.git(self.repo, "add", ".")
        a.git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "fixture")
        self.base = a.git(self.repo, "rev-parse", "HEAD")
        self.spec = a.prepare_spec(self.repo, "main", ".nullcode-acceptance.json", True)
        self.calls = []

    def execute(self, answers, verifier=None):
        ident = self.store.submit(repo_spec=self.spec)
        job = self.store.claim()
        sequence = iter(answers)
        def generate(prompt, record):
            self.calls.append(prompt)
            record(100 + len(self.calls))
            return next(sequence)
        a.run_job(self.store, job, generate_fn=generate,
                  verify_fn=verifier or self.verify, artifacts=self.jobs)
        return self.store.show(ident), self.jobs / f"workflow-{ident}" / "repo"

    def verify(self, checkout, paths, folder, minimum, phase):
        phase("compiling")
        phase("testing")
        passed = "return count;" in (checkout / self.target).read_text()
        return {"passed": passed, "repairable": not passed,
                "snapshot_sha256": a.hashes(checkout, paths),
                "compile": {"exit_code": 0, "timed_out": False},
                "tests": {"exit_code": 0 if passed else 1, "timed_out": False},
                "junit": {"tests": 24, "skipped": 0, "failures": 0 if passed else 1,
                          "diagnostics": "" if passed else "thresholdThree: expected 1 but was 0"},
                "cleanup": {"exit_code": 0, "timed_out": False}}

    def test_success_commits_only_production_and_preserves_tests(self):
        result, checkout = self.execute([GOOD])
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.assertEqual(a.git(checkout, "diff", "--name-only", self.base, "HEAD"), self.target)
        self.assertEqual((checkout / self.test).read_bytes(), (self.repo / self.test).read_bytes())
        self.assertEqual(a.git(self.repo, "rev-parse", "HEAD"), self.base)
        self.assertEqual(a.git(checkout, "remote"), "")

    def test_three_failed_candidates_stop_without_commit(self):
        candidates = [GOOD.replace("return count;", f"return {n};") for n in (0, 1, 2)]
        result, checkout = self.execute(candidates)
        self.assertEqual(result["status"], "failed")
        self.assertIn("two repairs", result["error"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(a.git(checkout, "rev-parse", "HEAD"), self.base)
        self.assertIn("fixed contract", self.calls[1])

    def test_repair_succeeds_with_unchanged_fixed_tests(self):
        result, _ = self.execute([GOOD.replace("return count;", "return 0;"), GOOD])
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.assertEqual(len(self.calls), 2)

    def test_unchanged_repair_stops(self):
        bad = GOOD.replace("return count;", "return 0;")
        result, _ = self.execute([bad, bad])
        self.assertIn("unchanged source", result["error"])
        self.assertEqual(len(self.calls), 2)

    def test_multifile_response_never_written(self):
        answer = "```java\n" + GOOD + "```\n```java\nclass Other {}\n```"
        result, checkout = self.execute([answer]*3)
        self.assertEqual(result["status"], "failed")
        self.assertEqual((checkout / self.target).read_text(), BASE_SOURCE)

    def test_test_tampering_blocks_commit(self):
        def verify(*args):
            result = self.verify(*args)
            (args[0] / self.test).write_text("// deleted test")
            return result
        result, checkout = self.execute([GOOD], verify)
        self.assertIn("protected content changed", result["error"])
        self.assertEqual(a.git(checkout, "rev-parse", "HEAD"), self.base)

    def test_snapshot_tampering_blocks_commit(self):
        def verify(*args):
            result = self.verify(*args)
            result["snapshot_sha256"][self.target] = "wrong"
            return result
        result, _ = self.execute([GOOD], verify)
        self.assertIn("snapshot changed", result["error"])

    def test_skipped_tests_block_commit(self):
        def verify(*args):
            result = self.verify(*args)
            result["junit"]["skipped"] = 1
            return result
        result, _ = self.execute([GOOD], verify)
        self.assertIn("Incomplete acceptance evidence", result["error"])

    def test_failed_cleanup_blocks_commit(self):
        def verify(*args):
            result = self.verify(*args)
            result["cleanup"]["exit_code"] = 1
            return result
        result, _ = self.execute([GOOD], verify)
        self.assertIn("Incomplete acceptance evidence", result["error"])

    def test_missing_method_in_fixed_tests_can_trigger_production_repair(self):
        calls = []
        def verify(*args):
            calls.append(1)
            result = self.verify(*args)
            if len(calls) == 1:
                result.update(passed=False, repairable=False, tests=None, junit=None)
                result["compile"] = {"exit_code": 1, "timed_out": False,
                    "log": ":compileTestJava FAILED\n/work/Test.java:1: error: cannot find symbol\nsymbol: countWordsLongerThan"}
            return result
        result, _ = self.execute([GOOD.replace("return count;", "return 0;"), GOOD], verify)
        self.assertEqual(result["status"], "succeeded", result.get("error"))

    def test_no_review_no_submission(self):
        with self.assertRaisesRegex(ValueError, "acceptance-reviewed"):
            a.prepare_spec(self.repo, "main", ".nullcode-acceptance.json")

    def test_dirty_contract_rejected(self):
        (self.repo / ".nullcode-acceptance.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "clean"):
            a.prepare_spec(self.repo, "main", ".nullcode-acceptance.json", True)

    def test_tampered_queued_hash_rejected(self):
        self.spec["protected_sha256"][self.test] = "bad"
        result, _ = self.execute([])
        self.assertIn("pinned commit", result["error"])
        self.assertEqual(self.calls, [])

    def test_prompt_overflow_rejected_without_clipping(self):
        contract = json.loads((self.repo / ".nullcode-acceptance.json").read_text())
        with self.assertRaisesRegex(ValueError, "nothing truncated"):
            a.prompt_for(contract, "x"*2000)

    def test_compile_rule_reaches_model_and_is_recorded(self):
        calls = []
        def verify(*args):
            calls.append(1)
            result = self.verify(*args)
            return compile_failure(result) if len(calls) == 1 else result
        result, checkout = self.execute([GOOD.replace("return count;", "return 0;"), GOOD], verify)
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.assertNotIn("IMPLEMENTATION GUIDANCE", self.calls[0])
        self.assertTrue(self.calls[1].startswith(a.STREAM_LOOP_GUIDANCE))
        rule = json.loads((checkout.parent / "attempt-2/repair-rule.json").read_text())
        self.assertIn("location: class String[]", rule["evidence"])
        self.assertEqual(result["attempts"][-1]["result"]["attempts"][1]["repair_rule"], rule["id"])

    def test_rule_does_not_add_attempts(self):
        candidates = [GOOD.replace("return count;", f"return {n};") for n in (0, 1, 2)]
        result, checkout = self.execute(candidates, lambda *args: compile_failure(self.verify(*args)))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(sum("IMPLEMENTATION GUIDANCE" in p for p in self.calls), 2)
        self.assertEqual(a.git(checkout, "rev-parse", "HEAD"), self.base)

    def test_rule_clears_after_different_failure(self):
        calls = []
        def verify(*args):
            calls.append(1)
            result = self.verify(*args)
            return compile_failure(result) if len(calls) == 1 else result
        result, _ = self.execute([GOOD.replace("return count;", "return 0;"),
                                  GOOD.replace("return count;", "return 1;"), GOOD], verify)
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.assertIn("IMPLEMENTATION GUIDANCE", self.calls[1])
        self.assertNotIn("IMPLEMENTATION GUIDANCE", self.calls[2])


if __name__ == "__main__":
    unittest.main()
