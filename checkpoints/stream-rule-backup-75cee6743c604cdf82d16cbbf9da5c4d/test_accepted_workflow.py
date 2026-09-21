"""Real Git/SQLite integration tests; inference and Docker verification are simulated."""
import json
import pathlib
import sys
import unittest
import uuid

# The uploaded archive omits this existing dependency. Use the original local
# workspace copy for development only; installed Pi runs use its own module.
if not (pathlib.Path(__file__).parent / "validate_java.py").exists():
    sys.path.append(str(pathlib.Path(__file__).parent.parent / "nullcode"))

import accepted_workflow as a
from java_workflow import Store

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


class AcceptedTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).parent / "test-results" / uuid.uuid4().hex
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True)
        self.jobs = self.root / "jobs"
        self.store = Store(self.root / "db")
        self.target = "src/main/java/lab/TextStats.java"
        self.test = "src/test/java/lab/TextStatsAcceptanceTest.java"
        assets = pathlib.Path(__file__).parent
        files = {self.target: BASE_SOURCE,
                 self.test: (assets / "acceptance/TextStatsAcceptanceTest.java").read_text(),
                 ".nullcode.json": json.dumps({"profile": "gradle-junit-v1", "editable_files": [self.target],
                                               "editable_test_files": [self.test], "minimum_tests": 12}),
                 ".nullcode-acceptance.json": (assets / "acceptance/contract.json").read_text()}
        for name in ("build.gradle", "settings.gradle", "gradle.properties"):
            files[name] = (assets / "gradle_profile" / name).read_text()
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


if __name__ == "__main__":
    unittest.main()
