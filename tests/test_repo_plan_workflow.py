import pathlib
import unittest
import uuid

from nullcode.core.java_workflow import Store
from nullcode.fixtures.create_gradle_fixture import create
from nullcode.repo.repo_plan_workflow import prepare_spec, run_job
from nullcode.repo.repo_workflow import git

TASK = 'Implement Slugs.slugify while keeping TextStats passing.'

SELECTION = (
    '{"files": ["src/main/java/lab/Slugs.java", "src/test/java/lab/SlugsTest.java"], '
    '"reason": "The implementation and its existing test"}'
)

PLAN = (
    '{"summary": "Implement slugify using Locale.ROOT and hyphen collapsing.", '
    '"files": [{"path": "src/main/java/lab/Slugs.java", "reason": "Needs implementation"}, '
    '{"path": "src/test/java/lab/SlugsTest.java", "reason": "Already covers the behavior"}], '
    '"steps": ["Lowercase with Locale.ROOT", "Collapse separators to one hyphen", '
    '"Trim leading and trailing hyphens"], '
    '"risks": ["Unicode edge cases outside ASCII"]}'
)


class RepoPlanWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.repo = create(self.root / 'source')
        self.store = Store(self.root / 'db')
        self.spec = prepare_spec(self.repo, 'main', TASK)

    def run_case(self, answers):
        job_id = self.store.submit(repo_spec=self.spec)
        remaining = list(answers)
        prompts = []

        def generate(prompt, record):
            prompts.append(prompt)
            self.assertLessEqual(len(prompt.encode()), 2000)
            record(7)
            return remaining.pop(0)

        run_job(self.store, self.store.claim(), generate, self.root / 'jobs')
        return job_id, self.store.show(job_id), prompts

    def test_planning_prompt_forbids_code_in_steps(self):
        from nullcode.repo.repo_plan_workflow import planning_prompt
        prompt = planning_prompt(
            TASK,
            ["src/main/java/lab/Slugs.java"],
            "FILE: src/main/java/lab/Slugs.java\nclass Slugs {}",
        )
        self.assertIn("Steps must be short prose only", prompt)
        self.assertIn("do not include code, code fences", prompt)
        self.assertLessEqual(len(prompt.encode("utf-8")), 2000)

    def test_task_byte_limit_enforced_before_any_job_is_submitted(self):
        with self.assertRaisesRegex(ValueError, 'planning limit'):
            prepare_spec(self.repo, 'main', 'x' * 501)

    def test_success_produces_a_validated_plan_and_leaves_repo_clean(self):
        job_id, result, prompts = self.run_case([SELECTION, PLAN])
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertEqual(len(result['attempts']), 2)

        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        plan = (workdir / 'plan.json').read_text(encoding='utf-8')
        self.assertIn('Locale.ROOT', plan)

        selected = (workdir / 'candidate-files.json').read_text(encoding='utf-8')
        self.assertIn('SlugsTest.java', selected)

        # Read-only: nothing was ever added or committed on the checkout.
        checkout = workdir / 'repo'
        self.assertEqual(git(checkout, 'status', '--porcelain'), '')
        self.assertEqual(
            git(checkout, 'rev-parse', 'HEAD'),
            self.spec['base_commit'],
        )

        # Nothing in the original repository moved either.
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_selection_rejects_a_file_outside_the_committed_inventory(self):
        bad_selection = '{"files": ["src/main/java/lab/DoesNotExist.java"], "reason": "n/a"}'
        _, result, prompts = self.run_case([bad_selection, PLAN])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('unapproved file', result['error'])
        # The planning call never happened; only one answer was consumed.
        self.assertEqual(len(prompts), 1)

    def test_plan_rejects_a_file_the_model_never_selected(self):
        bad_plan = (
            '{"summary": "Implement slugify.", '
            '"files": [{"path": "src/main/java/lab/TextStats.java", "reason": "unrelated"}], '
            '"steps": ["Do the work"], "risks": []}'
        )
        _, result, prompts = self.run_case([SELECTION, bad_plan])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('unselected file', result['error'])
        self.assertEqual(len(prompts), 2)

    def test_malformed_selection_answer_is_rejected_before_any_file_is_read(self):
        _, result, prompts = self.run_case(['not json at all', PLAN])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('did not return a JSON object', result['error'])
        self.assertEqual(len(prompts), 1)

    def test_repository_mutation_during_planning_fails_the_job(self):
        job_id = self.store.submit(repo_spec=self.spec)
        remaining = [SELECTION, PLAN]
        checkout = self.root / 'jobs' / f'workflow-{job_id}' / 'repo'

        def generate(prompt, record):
            record(7)
            answer = remaining.pop(0)
            if answer is PLAN:
                # Simulate something touching the checkout mid-workflow; the
                # read-only guarantee is a real git-status check, not a
                # promise the code just happens to keep.
                (checkout / 'unexpected.txt').write_text('surprise', encoding='utf-8')
            return answer

        run_job(self.store, self.store.claim(), generate, self.root / 'jobs')
        result = self.store.show(job_id)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('changed during read-only', result['error'])


if __name__ == '__main__':
    unittest.main()
