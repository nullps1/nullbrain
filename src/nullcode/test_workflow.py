import pathlib
import uuid
import unittest
from unittest.mock import patch
from java_workflow import Store, run_job, clip, SPEC, verify

SOURCE = 'public class Numbers { public static int max(int[] values) { return 0; } }'


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.store = Store(self.root / 'db')
        self.artifacts = self.root / 'jobs'

    def run_workflow(self, results, initial=None):
        job_id = self.store.submit(initial)
        prompts = []
        def generate(prompt, callback):
            prompts.append(prompt)
            callback(100 + len(prompts))
            return SOURCE
        pending = iter(results)
        def verify(source, folder, phase):
            self.assertTrue(folder.is_dir())
            phase('compiling')
            phase('testing')
            return next(pending)
        run_job(self.store, self.store.claim(), generate, verify, self.artifacts)
        return self.store.show(job_id), prompts

    def test_success_needs_verification(self):
        result, prompts = self.run_workflow([{'passed': True}])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(len(result['attempts']), 1)
        self.assertEqual(result['attempts'][0]['inference_job'], 101)

    def test_compile_failure_repaired_once(self):
        result, prompts = self.run_workflow([
            {'passed': False, 'repairable': True, 'compile': {'log': 'cannot find symbol'}},
            {'passed': True}])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(len(result['attempts']), 2)
        self.assertIn('cannot find symbol', prompts[1])
        self.assertLessEqual(len(prompts[1].encode()), 2000)
        self.assertEqual(result['attempts'][0]['phase'], 'failed')

    def test_failed_repair_stops_after_two_attempts(self):
        failure = {'passed': False, 'repairable': True, 'tests': {'log': 'AssertionError'}}
        result, prompts = self.run_workflow([failure, failure])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(prompts), 2)
        self.assertEqual(len(result['attempts']), 2)

    def test_infrastructure_failure_is_not_sent_for_model_repair(self):
        result, prompts = self.run_workflow([{'passed': False, 'repairable': False}])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(prompts), 1)

    def test_repair_demo_skips_initial_inference(self):
        result, prompts = self.run_workflow([
            {'passed': False, 'repairable': True, 'tests': {'log': 'expected 8 got 0'}},
            {'passed': True}], initial=SOURCE)
        self.assertEqual(len(prompts), 1)
        self.assertIsNone(result['attempts'][0]['inference_job'])
        self.assertEqual(result['status'], 'succeeded')

    def test_restart_preserves_queued_and_marks_active_interrupted(self):
        active = self.store.submit()
        queued = self.store.submit()
        self.store.claim()
        self.store.recover()
        self.assertEqual(self.store.show(active)['status'], 'interrupted')
        self.assertEqual(self.store.show(queued)['status'], 'queued')

    def test_inference_exception_is_recorded(self):
        job = self.store.submit()
        def broken(*args):
            raise RuntimeError('Ollama unavailable')
        run_job(self.store, self.store.claim(), broken, artifacts=self.artifacts)
        result = self.store.show(job)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Ollama unavailable', result['error'])

    def test_repair_budget_holds_for_multibyte_text(self):
        text = '界' * 2000
        prompt = SPEC + '\nFix the previous attempt using this diagnostic. Return the complete corrected class.\nPrevious source (may be shortened):\n' + clip(text, 900) + '\nDiagnostic:\n' + clip(text, 500)
        self.assertLessEqual(len(prompt.encode()), 2000)

    def test_queue_limit(self):
        for _ in range(16):
            self.store.submit()
        with self.assertRaises(ValueError):
            self.store.submit()

    def check_verifier(self, outputs):
        folder = self.root / 'verify'
        folder.mkdir()
        phases = []
        with patch('java_workflow.subprocess.check_output', return_value='sha256:test'), \
             patch('java_workflow.command', side_effect=outputs):
            return verify(SOURCE, folder, phases.append), phases

    def test_verifier_stops_after_compile_failure(self):
        ok = {'exit_code': 0, 'log': '', 'timed_out': False}
        bad = {'exit_code': 1, 'log': 'compiler error', 'timed_out': False}
        result, phases = self.check_verifier([ok, bad, ok])
        self.assertIsNone(result['tests'])
        self.assertEqual(phases, ['compiling'])
        self.assertTrue(result['repairable'])

    def test_verifier_requires_test_completion_marker(self):
        ok = {'exit_code': 0, 'log': '', 'timed_out': False}
        result, _ = self.check_verifier([ok, ok, ok, ok])
        self.assertFalse(result['passed'])

    def test_verifier_timeout_is_not_repaired(self):
        ok = {'exit_code': 0, 'log': '', 'timed_out': False}
        timeout = {'exit_code': 124, 'log': '', 'timed_out': True}
        result, _ = self.check_verifier([ok, ok, timeout, ok])
        self.assertFalse(result['passed'])
        self.assertFalse(result['repairable'])

    def test_verifier_records_separate_successful_stages(self):
        ok = {'exit_code': 0, 'log': '', 'timed_out': False}
        passed = {'exit_code': 0, 'log': 'RESULT: 8/8 checks passed', 'timed_out': False}
        result, phases = self.check_verifier([ok, ok, passed, ok])
        self.assertTrue(result['passed'])
        self.assertEqual(phases, ['compiling', 'testing'])


if __name__ == '__main__':
    unittest.main()
