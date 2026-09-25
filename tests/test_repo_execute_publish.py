import copy
import json
import pathlib
import unittest
from unittest.mock import patch

import test_repo_execute_workflow as workflow
from nullcode.publish import repo_execute_publish
from nullcode.publish.publish_workflow import prepare, deliver
from nullcode.repo.repo_workflow import git


class RepoExecutePublishTests(unittest.TestCase):
    def make_workflow(self, repaired=False, three_files=False, production=workflow.GOOD_PROD):
        harness = workflow.RepoExecuteWorkflowTests()
        harness.setUp()
        answers = [workflow.SELECTION, workflow.PLAN, production, workflow.GOOD_TEST]
        if three_files:
            extra = 'src/main/java/lab/TextStats.java'
            selection = json.loads(workflow.SELECTION)
            selection['files'].append(extra)
            plan = json.loads(workflow.PLAN)
            plan['files'].append({'path': extra, 'reason': 'Document retained word counting'})
            source = (harness.repo / extra).read_text().replace(
                'public class TextStats', '// Whitespace-separated word counting.\npublic class TextStats')
            answers = [json.dumps(selection), json.dumps(plan), production,
                       source, workflow.GOOD_TEST]

        def evidence(count):
            return {'passed': True, 'image_id': 'sha256:' + 'a' * 64,
                    'junit': {'tests': count, 'failures': 0, 'skipped': 0}}

        # Candidate, original-suite regression, and the hybrid counterfactual
        # (candidate tests against pinned-base production, which must not
        # fully pass).
        verifications = [evidence(13), evidence(12), workflow.HYBRID_DISTINGUISHING]
        if repaired:
            answers += [json.dumps({'fault_domain': 'production', 'file': workflow.PROD_TARGET, 'reason': 'Fix production'}),
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

    def test_publisher_revalidates_the_approved_test_scope(self):
        """inspect() validates editable_files only, so the publisher must check
        editable_test_files itself rather than trusting the committed field."""
        self.make_workflow()
        real_inspect = repo_execute_publish.inspect

        def doctored(value):
            def inspect(checkout, commit):
                paths, config = real_inspect(checkout, commit)
                config = dict(config)
                config.pop('editable_test_files', None)
                if value is not None:
                    config['editable_test_files'] = value
                return paths, config
            return inspect

        rejected = [
            None,                                              # absent
            [],                                                # empty
            workflow.TEST_TARGET,                              # not a list
            [workflow.PROD_TARGET],                            # production, not a test
            ['src/test/java/lab/Absent.java'],                 # not committed
            [workflow.TEST_TARGET, workflow.TEST_TARGET],      # duplicated
            ['build.gradle'],                                  # protected build config
            [workflow.TEST_TARGET] + [f'src/test/java/lab/T{n}.java' for n in range(8)],
        ]
        for value in rejected:
            with self.subTest(editable_test_files=value):
                with patch.object(repo_execute_publish, 'inspect', doctored(value)):
                    with self.assertRaises(ValueError):
                        prepare(self.job, self.artifacts)
        # The unmodified configuration still publishes.
        with patch.object(repo_execute_publish, 'inspect', doctored([workflow.TEST_TARGET])):
            self.assertEqual(prepare(self.job, self.artifacts)['commit'],
                             git(self.checkout, 'rev-parse', 'HEAD'))

    def test_no_behavioral_delta_workflow_cannot_be_published(self):
        """A Workflow 26-style task never reaches 'succeeded', so the existing
        status gate already blocks publication - confirm it, end to end."""
        harness = workflow.BehavioralDeltaTests()
        harness.setUp()
        harness.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        image = 'sha256:' + 'a' * 64
        candidate = {'passed': True, 'image_id': image,
                     'junit': {'tests': 13, 'failures': 0, 'skipped': 0}}
        baseline = {'passed': True, 'image_id': image,
                    'junit': {'tests': 12, 'failures': 0, 'skipped': 0}}
        # The bounded semantic re-plan runs first and also shows no delta.
        _, job, _ = harness.run_case(
            answers=[workflow.SELECTION, workflow.PLAN, workflow.WORKFLOW_26_PROD,
                     harness.added_case_test(harness.HYPHEN_CASE),
                     workflow.SEMANTIC_DIAGNOSIS, workflow.REPLAN_PLAN,
                     workflow.WORKFLOW_26_PROD,
                     harness.added_case_test(harness.HYPHEN_CASE)],
            verify_results=[
                candidate, baseline, workflow.HYBRID_NO_DELTA,
                candidate, baseline, workflow.HYBRID_NO_DELTA,
            ],
        )
        self.assertEqual(job['status'], workflow.REJECTED_NO_BEHAVIORAL_DELTA)
        with self.assertRaisesRegex(ValueError, 'successful'):
            prepare(job, harness.root / 'jobs')

    def test_lone_carriage_return_matches_the_producer_review_hash(self):
        """The producer hashes sources read through Python text mode, which folds
        a lone CR; reading committed bytes must normalize the same way."""
        production = workflow.GOOD_PROD.replace(
            'public class Slugs {', '// Locale-independent.\rpublic class Slugs {')
        self.make_workflow(production=production)
        self.assertIn(b'\r', git(self.checkout, 'show', 'HEAD:' + workflow.PROD_TARGET,
                                 binary=True))
        plan = prepare(self.job, self.artifacts)
        self.assertIn(workflow.PROD_TARGET, plan['body'])


class PublishedEvidenceMetadataTests(unittest.TestCase):
    """Milestone 7B.1: the preview states which kind of novelty evidence the
    workflow actually has. Eligibility is unchanged - still status ==
    'succeeded', still draft-only, still human acceptance."""

    def build(self, production, case, hybrid, replan=False):
        harness = workflow.BehavioralDeltaTests()
        harness.setUp()
        harness.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        image = 'sha256:' + 'a' * 64
        candidate = {'passed': True, 'image_id': image,
                     'junit': {'tests': 13, 'failures': 0, 'skipped': 0}}
        baseline = {'passed': True, 'image_id': image,
                    'junit': {'tests': 12, 'failures': 0, 'skipped': 0}}
        answers = [workflow.SELECTION, workflow.PLAN, production,
                   harness.added_case_test(case)]
        verifications = [candidate, baseline, hybrid]
        if replan:
            answers = [workflow.SELECTION, workflow.PLAN,
                       workflow.WORKFLOW_26_PROD,
                       harness.added_case_test(workflow.BehavioralDeltaTests.HYPHEN_CASE),
                       workflow.SEMANTIC_DIAGNOSIS, workflow.REPLAN_PLAN,
                       production, harness.added_case_test(case)]
            verifications = [candidate, baseline, workflow.HYBRID_NO_DELTA,
                             candidate, baseline, hybrid]
        _, job, _ = harness.run_case(answers, verifications)
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.harness = job, harness.root / 'jobs'
        self.final = next(attempt['result'] for attempt in job['attempts']
                          if (attempt.get('result') or {}).get('profile')
                          == 'repo-execute-v1')
        return job, harness.root / 'jobs'

    def test_behavioral_evidence_is_labelled_as_behavioral(self):
        job, artifacts = self.build(
            workflow.GOOD_PROD, workflow.BehavioralDeltaTests.HYPHEN_CASE,
            workflow.HYBRID_DISTINGUISHING)
        body = prepare(job, artifacts)['body']
        self.assertIn('distinguishing-test-failure', body)
        self.assertIn('evidence level **behavioral**', body)
        self.assertIn('Behavioral evidence', body)
        self.assertNotIn('Structural evidence only', body)

    def test_structural_evidence_says_so_and_does_not_claim_behavior(self):
        job, artifacts = self.build(
            workflow.NEW_API_PROD, workflow.BehavioralDeltaTests.UNICODE_CASE,
            workflow.HYBRID_API_COMPILE_FAILURE)
        body = prepare(job, artifacts)['body']
        self.assertIn('distinguishing-api-compile-failure', body)
        self.assertIn('evidence level **structural**', body)
        self.assertIn('does NOT prove that runtime behavior differs', body)
        # Still a draft for a human, with the same caveats as before.
        self.assertIn('Draft for human review', body)

    def test_a_replanned_candidate_says_it_replaced_an_empty_one(self):
        job, artifacts = self.build(
            workflow.GOOD_PROD, workflow.BehavioralDeltaTests.HYPHEN_CASE,
            workflow.HYBRID_DISTINGUISHING, replan=True)
        body = prepare(job, artifacts)['body']
        self.assertIn('semantic re-plan 1', body)
        self.assertIn('re-verified', body)

    def test_missing_or_doctored_delta_evidence_is_not_publishable(self):
        job, artifacts = self.build(
            workflow.GOOD_PROD, workflow.BehavioralDeltaTests.HYPHEN_CASE,
            workflow.HYBRID_DISTINGUISHING)
        delta = self.final['behavioral_delta']
        mutations = [
            ('classification', workflow.NO_BEHAVIORAL_DELTA),
            ('classification', 'something-else'),
            ('distinguishing', False),
            # An evidence level that disagrees with its own classification.
            ('evidence_level', 'behavioral-ish'),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                original = delta[key]
                delta[key] = value
                with self.assertRaises(ValueError):
                    prepare(job, artifacts)
                delta[key] = original
        # Structural evidence must not be relabelled as behavioral either.
        delta['classification'] = workflow.DISTINGUISHING_API_COMPILE_FAILURE
        with self.assertRaisesRegex(ValueError, 'not distinguishing'):
            prepare(job, artifacts)
        delta['classification'] = workflow.DISTINGUISHING_TEST_FAILURE

        original = self.final.pop('behavioral_delta')
        with self.assertRaisesRegex(ValueError, 'Missing behavioral-delta'):
            prepare(job, artifacts)
        self.final['behavioral_delta'] = original

        # A hybrid snapshot that is not the state the producer recorded.
        manifest = self.final['behavioral_delta']['manifest']
        kept = manifest['hybrid_snapshot_sha256']
        manifest['hybrid_snapshot_sha256'] = {'src/main/java/lab/Slugs.java': 'x'}
        with self.assertRaisesRegex(ValueError, 'Hybrid counterfactual snapshot'):
            prepare(job, artifacts)
        manifest['hybrid_snapshot_sha256'] = kept
        self.assertIn('evidence level', prepare(job, artifacts)['body'])


if __name__ == '__main__':
    unittest.main()
