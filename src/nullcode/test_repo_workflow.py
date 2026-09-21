import json
import pathlib
import sqlite3
import unittest
import uuid
from create_fixture import create
from java_workflow import Store
from repo_workflow import git, prepare_spec, run_repo_job, TARGET, TEST

SOURCE = 'public class Numbers { public static int max(int[] values) { return 42; } }'


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.repo = create(self.root / 'source')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        self.store = Store(self.root / 'db')

    def spec(self):
        return prepare_spec(self.repo, 'main', 'Implement max correctly; reject null and empty arrays.')

    def run_case(self, results):
        job = self.store.submit(repo_spec=self.spec())
        pending = iter(results)
        def generate(prompt, record):
            self.assertLessEqual(len(prompt.encode()), 2000)
            record(10)
            return SOURCE
        def verify(source, folder, phase, test_source):
            self.assertEqual(test_source, (self.repo / TEST).read_text())
            phase('compiling')
            phase('testing')
            return next(pending)
        run_repo_job(self.store, self.store.claim(), generate, verify, self.root / 'jobs')
        return self.store.show(job)

    def test_isolated_branch_commit_and_applicable_diff(self):
        result = self.run_case([{'passed': True}])
        self.assertEqual(result['status'], 'succeeded', result['error'])
        metadata = result['attempts'][0]['result']['repository']
        checkout = pathlib.Path(metadata['checkout'])
        self.assertEqual(git(checkout, 'branch', '--show-current'), 'agent/workflow-1')
        self.assertEqual(git(checkout, 'status', '--porcelain'), '')
        self.assertEqual(git(checkout, 'remote'), '')
        self.assertEqual(git(checkout, 'diff', '--name-only', self.base, 'HEAD'), TARGET)
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        git(self.repo, 'apply', '--check', metadata['diff_path'])

    def test_two_failures_leave_no_success_commit(self):
        bad = {'passed': False, 'repairable': True, 'tests': {'log': 'AssertionError'}}
        result = self.run_case([dict(bad), dict(bad)])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['attempts']), 2)
        checkout = self.root / 'jobs/workflow-1/repo'
        self.assertEqual(git(checkout, 'rev-parse', 'HEAD'), self.base)

    def test_uses_committed_snapshot_not_uncommitted_source_edits(self):
        spec = self.spec()
        (self.repo / TARGET).write_text('Uncommitted changes must stay in the original checkout')
        self.assertEqual(spec['base_commit'], self.base)
        result = self.run_case([{'passed': True}])
        self.assertEqual(result['status'], 'succeeded')
        self.assertIn('Uncommitted', (self.repo / TARGET).read_text())

    def test_missing_profile_files_rejected(self):
        git(self.repo, 'rm', '--', TEST)
        git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-m', 'remove test')
        with self.assertRaises(ValueError):
            self.spec()

    def test_executable_files_rejected(self):
        git(self.repo, 'update-index', '--chmod=+x', TARGET)
        git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-m', 'executable')
        with self.assertRaises(ValueError):
            self.spec()

    def test_existing_database_migrates_without_losing_history(self):
        root = self.root / 'old-db'
        root.mkdir()
        db = sqlite3.connect(root / 'workflows.sqlite3')
        db.execute("CREATE TABLE workflows(id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'queued', created_at TEXT DEFAULT CURRENT_TIMESTAMP, finished_at TEXT, initial_source TEXT, error TEXT)")
        db.execute("INSERT INTO workflows(status,finished_at) VALUES('succeeded','2026-09-20')")
        db.commit()
        db.close()
        migrated = Store(root)
        self.assertEqual(migrated.show(1)['status'], 'succeeded')
        self.assertIsNone(migrated.show(1)['repo_spec'])
        Store(root)  # Idempotent migration.


if __name__ == '__main__':
    unittest.main()
