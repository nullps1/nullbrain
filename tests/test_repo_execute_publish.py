import copy
import json
import pathlib
import unittest
from unittest.mock import patch

import test_repo_execute_workflow as workflow
from nullcode.publish.publish_workflow import prepare, deliver
from nullcode.repo.repo_workflow import git


class RepoExecutePublishTests(unittest.TestCase):
    def make_workflow(self, repaired=False, three_files=False):
        harness = workflow.RepoExecuteWorkflowTests()
        harness.setUp()
        answers = [workflow.SELECTION, workflow.PLAN, workflow.GOOD_PROD, workflow.GOOD_TEST]
        if three_files:
            extra = 'src/main/java/lab/TextStats.java'
            selection = json.loads(workflow.SELECTION)
            selection['files'].append(extra)
            plan = json.loads(workflow.PLAN)
            plan['files'].append({'path': extra, 'reason': 'Document retained word counting'})
            source = (harness.repo / extra).read_text().replace(
                'public class TextStats', '// Whitespace-separated word counting.\npublic class TextStats')
            answers = [json.dumps(selection), json.dumps(plan), workflow.GOOD_PROD,
                       source, workflow.GOOD_TEST]

        def evidence(count):
            return {'passed': True, 'image_id': 'sha256:' + 'a' * 64,
                    'junit': {'tests': count, 'failures': 0, 'skipped': 0}}

        verifications = [evidence(13), evidence(12)]
        if repaired:
            answers += [json.dumps({'file': workflow.PROD_TARGET, 'reason': 'Fix production'}),
                        workflow.GOOD_PROD_REPAIRED]
            verifications.insert(0, {'passed': False, 'repairable': True,
                                     'junit': {'diagnostics': 'wrong result'}})
        _, self.job, _ = harness.run_case(answers, verifications)
        self.assertEqual(self.job['status'], 'succeeded', self.job.get('error'))
        self.artifacts = harness.root / 'jobs'
        self.root = self.artifacts / f"workflow-{self.job['id']}"
        self.checkout = self.root / 'repo'
        self.final = next(a['result'] for a in self.job['attempts']
                          if (a.get('result') or {}).get('profile') == 'repo-execute-v1')

    def test_preview_is_local_and_uses_final_record_after_repair(self):
        self.make_workflow(repaired=True)
        self.assertGreater(self.job['attempts'][-1]['number'], 3)
        with patch('nullcode.publish.publish_workflow.gh_api', side_effect=AssertionError('network')):
            plan = prepare(self.job, self.artifacts)
        self.assertIn('13 candidate, 12 original suite', plan['body'])
        for name in (workflow.PROD_TARGET, workflow.TEST_TARGET):
            self.assertIn(name, plan['body'])
        self.assertEqual(plan['commit'], git(self.checkout, 'rev-parse', 'HEAD'))
        self.assertIn('not GitHub CI', plan['body'])

    def test_three_file_workflow_prepares_all_reviewed_files(self):
        self.make_workflow(three_files=True)
        plan = prepare(self.job, self.artifacts)
        self.assertEqual(plan['body'].count(': targeted review passed.'), 3)

    def test_final_evidence_gates_fail_closed(self):
        self.make_workflow()
        mutations = [
            ('candidate_verification', 'passed', False),
            ('baseline_verification', 'passed', False),
            ('candidate_verification', 'image_id', 'latest'),
            ('baseline_verification', 'image_id', 'sha256:' + 'b' * 64),
            ('candidate_verification', 'snapshot_sha256', {}),
            ('baseline_verification', 'snapshot_sha256', {}),
        ]
        for record, key, value in mutations:
            with self.subTest(record=record, key=key):
                original = self.final[record][key]
                self.final[record][key] = value
                with self.assertRaises(ValueError):
                    prepare(self.job, self.artifacts)
                self.final[record][key] = original
        for record in ('candidate_verification', 'baseline_verification'):
            for stage in ('compile', 'tests', 'cleanup'):
                for evidence in ({'exit_code': 1}, {'exit_code': 0, 'timed_out': True}, {}):
                    with self.subTest(record=record, stage=stage, evidence=evidence):
                        original = self.final[record][stage]
                        self.final[record][stage] = evidence
                        with self.assertRaises(ValueError):
                            prepare(self.job, self.artifacts)
                        self.final[record][stage] = original
        for record in ('candidate_verification', 'baseline_verification'):
            for report in ({}, {'tests': True, 'failures': 0, 'skipped': 0},
                           {'tests': 13, 'failures': 1, 'skipped': 0}):
                with self.subTest(record=record, report=report):
                    original = self.final[record]['junit']
                    self.final[record]['junit'] = report
                    with self.assertRaises(ValueError):
                        prepare(self.job, self.artifacts)
                    self.final[record]['junit'] = original

    def test_added_coverage_and_review_hashes_required(self):
        self.make_workflow()
        report = self.final['candidate_verification']['junit']
        for tests, skipped in ((12, 0), (13, 1)):
            report.update(tests=tests, skipped=skipped)
            with self.assertRaisesRegex(ValueError, 'increase'):
                prepare(self.job, self.artifacts)
        report.update(tests=13, skipped=0)
        for name in (workflow.PROD_TARGET, workflow.TEST_TARGET):
            for key, bad in (('source_sha256', 'wrong'), ('diff_sha256', 'wrong'),
                             ('status', 'changes_requested'), ('findings', ['finding'])):
                with self.subTest(name=name, key=key):
                    review = self.final['review']['files'][name]
                    original = review[key]
                    review[key] = bad
                    with self.assertRaises(ValueError):
                        prepare(self.job, self.artifacts)
                    review[key] = original

    def test_metadata_plan_dirty_checkout_and_duplicate_final_rejected(self):
        self.make_workflow()
        metadata_path = self.root / 'repository.json'
        original = metadata_path.read_text()
        data = json.loads(original)
        data['commit'] = 'b' * 40
        metadata_path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'authoritative'):
            prepare(self.job, self.artifacts)
        metadata_path.write_text(original)
        plan_path = self.root / 'plan.json'
        original_plan = plan_path.read_text()
        plan_path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Plan artifact'):
            prepare(self.job, self.artifacts)
        plan_path.write_text(original_plan)
        self.job['attempts'].append(copy.deepcopy(next(a for a in self.job['attempts']
                                                     if a.get('result') is self.final)))
        with self.assertRaisesRegex(ValueError, 'authoritative'):
            prepare(self.job, self.artifacts)
        self.job['attempts'].pop()
        (self.checkout / 'extra.txt').write_text('unverified')
        with self.assertRaisesRegex(ValueError, 'changed after verification'):
            prepare(self.job, self.artifacts)

    def test_baseline_must_represent_original_tests(self):
        self.make_workflow()
        self.final['baseline_verification']['snapshot_sha256'] = dict(
            self.final['candidate_verification']['snapshot_sha256'])
        with self.assertRaisesRegex(ValueError, 'original tests'):
            prepare(self.job, self.artifacts)

    def test_extra_committed_file_and_changed_head_rejected(self):
        self.make_workflow()
        with (self.checkout / '.gitignore').open('a') as output:
            output.write('\nunverified/\n')
        git(self.checkout, 'add', '.gitignore')
        git(self.checkout, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
            'commit', '--amend', '--no-edit')
        with self.assertRaisesRegex(ValueError, 'changed after verification'):
            prepare(self.job, self.artifacts)
        # Even updating the recorded commit cannot authorize the extra file.
        self.final['repository']['commit'] = git(self.checkout, 'rev-parse', 'HEAD')
        (self.root / 'repository.json').write_text(json.dumps(self.final['repository']))
        with self.assertRaisesRegex(ValueError, 'diff does not match'):
            prepare(self.job, self.artifacts)

    def test_publication_keeps_draft_only_and_second_base_check(self):
        self.make_workflow()
        plan = prepare(self.job, self.artifacts)
        calls = []
        def api(endpoint, **kwargs):
            if endpoint.endswith('/pulls'):
                return []
            return {'object': {'sha': plan['base_commit']}}
        def remote(repo, *args):
            calls.append(args)
            return '' if len(calls) < 3 else plan['commit'] + '\trefs/heads/' + plan['head']
        def create(args):
            self.assertIn('--draft', args)
            self.assertIn('--body-file', args)
            return 'https://github.com/owner/repo/pull/1'
        self.assertEqual(deliver(plan, 'owner/repo', api, remote, create),
                         'https://github.com/owner/repo/pull/1')
        pushes = [c for c in calls if 'push' in c]
        self.assertEqual(len(pushes), 1)
        self.assertNotIn('--force', pushes[0])
        refs = iter((plan['base_commit'], 'advanced'))
        def moving_api(endpoint, **kwargs):
            return [] if endpoint.endswith('/pulls') else {'object': {'sha': next(refs)}}
        with self.assertRaisesRegex(ValueError, 'advanced during publication'):
            deliver(plan, 'owner/repo', moving_api,
                    lambda *args: plan['commit'] + '\tref',
                    lambda args: self.fail('must not create PR'))


if __name__ == '__main__':
    unittest.main()
