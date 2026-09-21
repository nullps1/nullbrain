import json
import pathlib
import unittest
import uuid
from create_fixture import create
from java_workflow import Store
from repo_workflow import git, prepare_spec, run_repo_job
from review_java import REVIEW_DEMO, review_source

CLEAN = '''public class Numbers {
    public static int max(int[] values) {
        if (values == null || values.length == 0) throw new IllegalArgumentException();
        int max = Integer.MIN_VALUE;
        for (int value : values) if (value > max) max = value;
        return max;
    }
}
'''


class RuleTests(unittest.TestCase):
    def test_catches_observed_redundant_checks(self):
        result = review_source(REVIEW_DEMO, 'diff')
        self.assertEqual(result['status'], 'changes_requested')
        self.assertEqual(len(result['findings']), 2)
        self.assertEqual({f['rule'] for f in result['findings']}, {'int-range-check'})

    def test_clean_minimum_initialization_is_allowed(self):
        self.assertEqual(review_source(CLEAN, '')['status'], 'passed')

    def test_comments_and_literals_do_not_trigger_findings(self):
        source = CLEAN + '\n// int value; value > Integer.MAX_VALUE\n/* static void main( */\n'
        source += 'class Comment { String x = "System.out.println( int value; value < Integer.MIN_VALUE"; }'
        self.assertEqual(review_source(source, '')['status'], 'passed')

    def test_long_range_checks_are_not_flagged(self):
        source = 'class X { boolean check(long value) { return value > Integer.MAX_VALUE; } }'
        self.assertEqual(review_source(source, '')['status'], 'passed')

    def test_demo_and_console_output_are_flagged(self):
        result = review_source('class X { public static void main(String[] args) { System.out.println(1); } }', '')
        self.assertEqual({f['rule'] for f in result['findings']}, {'demo-main', 'console-output'})


class GateTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.repo = create(self.root / 'source')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        self.store = Store(self.root / 'db')

    def run_case(self, answers, verification, demo=False):
        spec = prepare_spec(self.repo, 'main', 'Implement max; reject null and empty arrays; preserve tests.')
        spec['review_demo'] = demo
        job = self.store.submit(repo_spec=spec)
        pending_answers = iter(answers)
        pending_checks = iter(verification)
        self.calls = 0
        self.prompts = []
        def generate(prompt, record):
            self.prompts.append(prompt)
            self.assertLessEqual(len(prompt.encode()), 2000)
            record(20 + len(self.prompts))
            return next(pending_answers)
        def verify(source, folder, phase, test_source):
            self.calls += 1
            phase('compiling')
            phase('testing')
            return dict(next(pending_checks))
        run_repo_job(self.store, self.store.claim(), generate, verify, self.root / 'jobs')
        return self.store.show(job)

    def test_review_correction_is_retested_before_commit(self):
        result = self.run_case([REVIEW_DEMO, CLEAN], [{'passed': True}, {'passed': True}])
        self.assertEqual(result['status'], 'succeeded', result['error'])
        self.assertEqual(self.calls, 2)
        first, second = result['attempts']
        self.assertTrue(first['result']['verification_passed'])
        self.assertFalse(first['result']['passed'])
        self.assertEqual(first['result']['review']['status'], 'changes_requested')
        self.assertEqual(second['result']['review']['status'], 'passed')
        self.assertIn('Remove the impossible', self.prompts[1])
        checkout = self.root / 'jobs/workflow-1/repo'
        self.assertEqual(git(checkout, 'rev-list', '--count', self.base + '..HEAD'), '1')

    def test_repeated_review_failure_creates_no_commit(self):
        result = self.run_case([REVIEW_DEMO, REVIEW_DEMO], [{'passed': True}, {'passed': True}])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['attempts']), 2)
        self.assertEqual(git(self.root / 'jobs/workflow-1/repo', 'rev-parse', 'HEAD'), self.base)

    def test_build_repair_and_review_correction_have_separate_budgets(self):
        result = self.run_case([CLEAN, REVIEW_DEMO, CLEAN], [
            {'passed': False, 'repairable': True, 'compile': {'log': 'compiler error'}},
            {'passed': True}, {'passed': True}])
        self.assertEqual(result['status'], 'succeeded', result['error'])
        self.assertEqual(self.calls, 3)

    def test_correction_with_failed_tests_is_never_committed(self):
        bad = {'passed': False, 'repairable': True, 'tests': {'log': 'AssertionError'}}
        result = self.run_case([REVIEW_DEMO, CLEAN, CLEAN], [{'passed': True}, bad, bad])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(git(self.root / 'jobs/workflow-1/repo', 'rev-parse', 'HEAD'), self.base)

    def test_review_demo_skips_first_generation(self):
        result = self.run_case([CLEAN], [{'passed': True}, {'passed': True}], demo=True)
        self.assertEqual(result['status'], 'succeeded')
        self.assertIsNone(result['attempts'][0]['inference_job'])
        self.assertEqual(len(self.prompts), 1)


if __name__ == '__main__':
    unittest.main()
