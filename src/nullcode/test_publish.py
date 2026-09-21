import copy
import pathlib
import unittest
import uuid
from create_fixture import create
from java_workflow import Store
from repo_workflow import prepare_spec, run_repo_job
from publish_workflow import prepare, deliver, validate_repo
from test_review import CLEAN


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.repo = create(self.root / 'source')
        self.store = Store(self.root / 'db')
        spec = prepare_spec(self.repo, 'main', 'Implement max correctly; reject null and empty arrays')
        job = self.store.submit(repo_spec=spec)
        def generate(prompt, record):
            record(1)
            return CLEAN
        def verify(source, folder, phase, test_source):
            (folder / 'Numbers.java').write_text(source, encoding='utf-8')
            (folder / 'NumbersTest.java').write_text(test_source, encoding='utf-8')
            return {'passed': True, 'image_id': 'sha256:test',
                    'compile': {'exit_code': 0, 'timed_out': False},
                    'tests': {'exit_code': 0, 'timed_out': False, 'log': 'RESULT: 8/8 checks passed'},
                    'cleanup': {'exit_code': 0}}
        run_repo_job(self.store, self.store.claim(), generate, verify, self.root / 'jobs')
        self.job = self.store.show(job)
        self.assertEqual(self.job['status'], 'succeeded', self.job['error'])
        self.plan = prepare(self.job, self.root / 'jobs')

    def test_valid_evidence_prepares_draft(self):
        self.assertIn('All 8 fixed functional checks passed', self.plan['body'])
        self.assertTrue(self.plan['head'].startswith('agent/workflow-1-'))

    def test_dirty_checkout_is_rejected(self):
        (pathlib.Path(self.plan['checkout']) / 'extra.txt').write_text('unverified')
        with self.assertRaisesRegex(ValueError, 'changed after verification'):
            prepare(self.job, self.root / 'jobs')

    def test_changed_evidence_hash_is_rejected(self):
        changed = copy.deepcopy(self.job)
        changed['attempts'][-1]['result']['review']['source_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'hashes'):
            prepare(changed, self.root / 'jobs')

    def test_pre_review_workflow_is_rejected(self):
        changed = copy.deepcopy(self.job)
        changed['attempts'][-1]['result'].pop('review')
        with self.assertRaisesRegex(ValueError, 'review'):
            prepare(changed, self.root / 'jobs')

    def test_changed_test_snapshot_is_rejected(self):
        (self.root / 'jobs/workflow-1/attempt-1/NumbersTest.java').write_text('modified')
        with self.assertRaisesRegex(ValueError, 'tests'):
            prepare(self.job, self.root / 'jobs')

    def test_stale_remote_base_stops_before_push(self):
        with self.assertRaisesRegex(ValueError, 'Remote main differs'):
            deliver(self.plan, 'owner/repo', api=lambda *a, **k: {'object': {'sha': 'new-base'}},
                    git_fn=lambda *a: self.fail('must not push'))

    def test_matching_existing_draft_is_reused(self):
        def api(endpoint, **kwargs):
            if endpoint.endswith('/pulls'):
                return [{'head': {'sha': self.plan['commit']}, 'state': 'open', 'draft': True,
                         'html_url': 'https://github.com/owner/repo/pull/1'}]
            return {'object': {'sha': self.plan['base_commit']}}
        self.assertEqual(deliver(self.plan, 'owner/repo', api=api,
            git_fn=lambda *a: self.fail('must not push')), 'https://github.com/owner/repo/pull/1')

    def test_new_publication_pushes_only_task_ref_and_creates_draft(self):
        calls = []
        def api(endpoint, **kwargs):
            return [] if endpoint.endswith('/pulls') else {'object': {'sha': self.plan['base_commit']}}
        def git(repo, *args):
            calls.append(args)
            if 'ls-remote' in args:
                return '' if len(calls) == 1 else self.plan['commit'] + '\trefs/heads/' + self.plan['head']
            return ''
        def create(args):
            self.assertIn('--draft', args)
            self.assertIn('--body-file', args)
            return 'https://github.com/owner/repo/pull/1'
        deliver(self.plan, 'owner/repo', api=api, git_fn=git, create_fn=create)
        pushes = [args for args in calls if 'push' in args]
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0][-1], self.plan['commit'] + ':refs/heads/' + self.plan['head'])
        self.assertNotIn('--force', pushes[0])

    def test_conflicting_remote_branch_is_not_overwritten(self):
        def api(endpoint, **kwargs):
            return [] if endpoint.endswith('/pulls') else {'object': {'sha': self.plan['base_commit']}}
        with self.assertRaisesRegex(ValueError, 'different commit'):
            deliver(self.plan, 'owner/repo', api=api, git_fn=lambda *a: 'other\trefs/heads/task')

    def test_repo_argument_cannot_be_url_or_option(self):
        for value in ['https://github.com/owner/repo', '--help', '../repo', 'owner/repo/extra']:
            with self.assertRaises(ValueError):
                validate_repo(value)


if __name__ == '__main__':
    unittest.main()
