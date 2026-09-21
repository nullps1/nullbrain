"""Milestone 7B.1: evidence strength, the bounded semantic re-plan, stage
timing, broader repository shapes, and the test-side logic risk.

The 7B suite in `test_repo_execute_workflow.py` still owns the behavioral-delta
gate itself. This module covers what 7B.1 adds on top of it.
"""

import hashlib
import json
import pathlib
import unittest
import uuid

import test_repo_execute_workflow as workflow
from nullcode.core.java_workflow import Store, REJECTED_NO_BEHAVIORAL_DELTA
from nullcode.core.review_java import review_source
from nullcode.fixtures.create_gradle_fixture import create
from nullcode.repo.repo_execute_workflow import (
    DISTINGUISHING_API_COMPILE_FAILURE,
    DISTINGUISHING_TEST_FAILURE,
    EVIDENCE_BEHAVIORAL,
    EVIDENCE_NONE,
    EVIDENCE_STRUCTURAL,
    INFRASTRUCTURE_FAILURE,
    NO_BEHAVIORAL_DELTA,
    SEMANTIC_REPLAN_BUDGET,
    diff_line_counts,
    evidence_level,
    highest_attempt_number,
    hybrid_manifest,
    hybrid_overlay_files,
    hybrid_reverted_files,
    pinned_base_production,
    prepare_spec,
    run_job,
    semantic_diagnosis_prompt,
    semantic_replan_outcome,
    semantic_replan_planning_prompt,
    test_side_logic_observation,
    validate_semantic_diagnosis,
)
from nullcode.repo.repo_workflow import git

PROD = workflow.PROD_TARGET
TEST = workflow.TEST_TARGET
SECOND_PROD = 'src/main/java/lab/TextStats.java'
SECOND_TEST = 'src/test/java/lab/TextStatsTest.java'


def selection(files, reason='multi-file scope'):
    return json.dumps({'files': files, 'reason': reason})


def plan_for(files, summary='Change the selected files.'):
    return json.dumps({
        'summary': summary,
        'files': [{'path': name, 'reason': 'needs work'} for name in files],
        'steps': ['Edit ' + name for name in files],
        'risks': [],
    })


class FlexibleHarness(unittest.TestCase):
    """Like the 7B harness, but without its single-file assumptions.

    Real Git fixture, real checkouts, canned model answers and a canned
    verifier - no Ollama, Docker or Gradle - exactly as the existing suites do.
    """

    task = workflow.TASK

    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.repo = create(self.root / 'source')
        self.store = Store(self.root / 'db')
        self.watch = [PROD, TEST]
        self.observed = []

    def commit_fixture(self, message):
        git(self.repo, 'add', '-A')
        git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
            'commit', '-m', message)

    def configure(self, editable=None, editable_tests=None, minimum=None):
        path = self.repo / '.nullcode.json'
        config = json.loads(path.read_text(encoding='utf-8'))
        if editable is not None:
            config['editable_files'] = editable
        config['editable_test_files'] = editable_tests or [TEST]
        if minimum is not None:
            config['minimum_tests'] = minimum
        path.write_text(json.dumps(config, indent=2), encoding='utf-8')
        self.commit_fixture('Configure repo-execute-v1 scope')
        self.spec = prepare_spec(self.repo, 'main', self.task)

    def run_case(self, answers, verify_results):
        job_id = self.store.submit(repo_spec=self.spec)
        remaining = list(answers)
        pending = iter(verify_results)
        prompts = []
        self.observed = []

        def generate(prompt, record):
            prompts.append(prompt)
            self.assertLessEqual(len(prompt.encode()), 2000)
            record(7)
            return remaining.pop(0)

        def verify(checkout, paths, folder, minimum, phase):
            phase('compiling')
            phase('testing')
            self.observed.append({
                name: (checkout / name).read_text(encoding='utf-8')
                for name in self.watch
            })
            result = dict(next(pending))
            result.setdefault('repairable', False)
            result.setdefault('compile', {'exit_code': 0})
            result.setdefault('tests', {'exit_code': 0})
            result.setdefault('cleanup', {'exit_code': 0})
            result['snapshot_sha256'] = {
                name: hashlib.sha256((checkout / name).read_bytes()).hexdigest()
                for name in paths
            }
            return result

        run_job(self.store, self.store.claim(), generate, verify, self.root / 'jobs')
        return job_id, self.store.show(job_id), prompts

    def workdir(self, job_id):
        return self.root / 'jobs' / f'workflow-{job_id}'

    def evidence(self, job_id, attempt=3):
        return json.loads((self.workdir(job_id) / f'attempt-{attempt}'
                           / 'behavioral-delta.json').read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------
# Evidence strength
# ---------------------------------------------------------------------------


class DeltaHarness(workflow.ExecuteWorkflowHarness):
    """The 7B behavioral-delta fixture helpers, without inheriting its tests."""

    pin_base_production = workflow.BehavioralDeltaTests.pin_base_production
    added_case_test = workflow.BehavioralDeltaTests.added_case_test
    delta_evidence = workflow.BehavioralDeltaTests.delta_evidence
    HYPHEN_CASE = workflow.BehavioralDeltaTests.HYPHEN_CASE
    UNICODE_CASE = workflow.BehavioralDeltaTests.UNICODE_CASE


class EvidenceLevelTests(DeltaHarness):
    """D2/D3: assertion-level and API-level evidence are recorded differently."""

    def test_assertion_level_failure_records_behavioral_evidence(self):
        self.pin_base_production(workflow.GENUINE_BASE_PROD)
        job_id, result, _ = self.run_case(
            answers=[workflow.SELECTION, workflow.PLAN, workflow.GOOD_PROD,
                     self.added_case_test(self.HYPHEN_CASE)],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                workflow.HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        evidence = self.delta_evidence(job_id)
        self.assertEqual(evidence['classification'], DISTINGUISHING_TEST_FAILURE)
        self.assertEqual(evidence['evidence_level'], EVIDENCE_BEHAVIORAL)
        self.assertIn('Behavioral evidence', evidence['evidence_summary'])
        self.assertEqual(evidence['semantic_replan_attempt'], 0)
        self.assertEqual(evidence['semantic_replan_outcome'], 'not-required')

    def test_api_compile_failure_records_structural_evidence(self):
        self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        job_id, result, _ = self.run_case(
            answers=[workflow.SELECTION, workflow.PLAN, workflow.NEW_API_PROD,
                     self.added_case_test(self.UNICODE_CASE)],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                workflow.HYBRID_API_COMPILE_FAILURE,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        evidence = self.delta_evidence(job_id)
        self.assertEqual(evidence['classification'],
                         DISTINGUISHING_API_COMPILE_FAILURE)
        self.assertEqual(evidence['evidence_level'], EVIDENCE_STRUCTURAL)
        # The two distinguishing outcomes must never read as equal quality.
        self.assertNotEqual(evidence['evidence_level'], EVIDENCE_BEHAVIORAL)
        self.assertIn('Structural evidence only', evidence['evidence_summary'])

    def test_evidence_level_table_never_upgrades_an_unknown_outcome(self):
        self.assertEqual(evidence_level(DISTINGUISHING_TEST_FAILURE),
                         EVIDENCE_BEHAVIORAL)
        self.assertEqual(evidence_level(DISTINGUISHING_API_COMPILE_FAILURE),
                         EVIDENCE_STRUCTURAL)
        for classification in (NO_BEHAVIORAL_DELTA, INFRASTRUCTURE_FAILURE,
                               'something-new', None):
            self.assertEqual(evidence_level(classification), EVIDENCE_NONE)

    def test_semantic_replan_outcome_labels(self):
        self.assertEqual(
            semantic_replan_outcome(NO_BEHAVIORAL_DELTA, 0), 'replan-triggered')
        self.assertEqual(
            semantic_replan_outcome(NO_BEHAVIORAL_DELTA, 1),
            'replan-exhausted-no-delta')
        self.assertEqual(
            semantic_replan_outcome(DISTINGUISHING_TEST_FAILURE, 0), 'not-required')
        self.assertEqual(
            semantic_replan_outcome(DISTINGUISHING_TEST_FAILURE, 1),
            'replan-produced-distinguishing-evidence')
        self.assertEqual(
            semantic_replan_outcome(INFRASTRUCTURE_FAILURE, 0), 'not-applicable')


# ---------------------------------------------------------------------------
# Bounded semantic re-plan
# ---------------------------------------------------------------------------


# A first candidate that verifies cleanly but demonstrates no behavioral
# delta. The marker makes it textually identifiable, so a later assertion can
# show the re-planned candidate never saw this source.
SUPERSEDED_PROD = '''package lab;
import java.util.Locale;
public class Slugs {
    // SUPERSEDED-CANDIDATE-MARKER
    public static String slugify(String text) {
        if (text == null) {
            throw new IllegalArgumentException("Input text cannot be null");
        }
        String lowercased = text.toLowerCase(Locale.ROOT);
        String normalized = lowercased.replaceAll("[^a-z0-9]+", "-");
        return normalized.replaceAll("^-*|-*$", "");
    }
}
'''

NO_DELTA_ROUND = [
    {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
    {'passed': True, 'junit': {'tests': 12}},
    workflow.HYBRID_NO_DELTA,
]


class SemanticReplanTests(DeltaHarness):
    """A candidate that demonstrated no behavioral delta gets exactly one
    re-planned replacement, and that replacement earns its way through every
    gate on its own evidence."""

    def first_candidate(self):
        """Answers for a first candidate that will show no behavioral delta."""
        return [workflow.SELECTION, workflow.PLAN, SUPERSEDED_PROD,
                self.added_case_test(self.HYPHEN_CASE)]

    def replan_answers(self, production=workflow.GOOD_PROD, test=None):
        return [workflow.SEMANTIC_DIAGNOSIS, workflow.REPLAN_PLAN, production,
                test if test is not None else self.added_case_test(self.HYPHEN_CASE)]

    def rescued(self, hybrid=workflow.HYBRID_DISTINGUISHING):
        self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        return self.run_case(
            answers=self.first_candidate() + self.replan_answers(),
            verify_results=NO_DELTA_ROUND + [
                {'passed': True, 'junit': {'tests': 14, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                hybrid,
            ],
        )

    def test_one_semantic_replan_rescues_a_no_delta_candidate(self):
        job_id, result, prompts = self.rescued()
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        # Selection, plan, two edits; diagnosis, re-plan, two more edits.
        self.assertEqual(len(prompts), 8)

        evidence = self.delta_evidence(job_id, attempt=8)
        self.assertEqual(evidence['classification'], DISTINGUISHING_TEST_FAILURE)
        self.assertEqual(evidence['evidence_level'], EVIDENCE_BEHAVIORAL)
        self.assertEqual(evidence['semantic_replan_attempt'], 1)
        self.assertEqual(evidence['semantic_replan_outcome'],
                         'replan-produced-distinguishing-evidence')

        # The re-planned candidate is what got committed.
        checkout = self.root / 'jobs' / f'workflow-{job_id}' / 'repo'
        self.assertEqual(git(checkout, 'show', 'HEAD:' + PROD, raw=True),
                         workflow.GOOD_PROD)

    def test_state_history_shows_reinterpretation_not_repair(self):
        job_id, result, _ = self.rescued()
        phases = {attempt['number']: attempt['phase'] for attempt in result['attempts']}
        # The superseded candidate is recorded as superseded, never as a
        # repair, and the re-plan has its own attempt number and phase.
        self.assertEqual(phases[3], 'superseded-no-behavioral-delta')
        self.assertEqual(phases[6], 'behavioral-delta-replanning')
        self.assertEqual(phases[8], 'passed')
        self.assertNotIn(4, phases)  # no ordinary repair was consumed
        self.assertNotIn(5, phases)

        diagnosis = json.loads(
            (self.root / 'jobs' / f'workflow-{job_id}' / 'attempt-6'
             / 'semantic-diagnosis.json').read_text(encoding='utf-8'))
        self.assertEqual(diagnosis['reason'], NO_BEHAVIORAL_DELTA)
        self.assertEqual(diagnosis['superseded_attempt'], 3)
        self.assertIs(diagnosis['prompt_is_advisory'], True)

    def test_replan_telemetry_is_persisted_for_success_and_exhaustion(self):
        job_id, result, _ = self.rescued()
        telemetry = json.loads(
            (self.root / 'jobs' / f'workflow-{job_id}'
             / 'semantic-replan.json').read_text(encoding='utf-8'))
        self.assertEqual(telemetry['semantic_replan_budget'], 1)
        self.assertEqual(telemetry['semantic_replan_attempts'], 1)
        record = telemetry['semantic_replan']
        self.assertEqual(record['attempt'], 1)
        self.assertEqual(record['reason'], NO_BEHAVIORAL_DELTA)
        self.assertEqual(record['resulting_classification'],
                         DISTINGUISHING_TEST_FAILURE)
        self.assertEqual(record['resulting_evidence_level'], EVIDENCE_BEHAVIORAL)
        self.assertEqual(record['final_result'], 'succeeded')
        self.assertIs(record['reached_succeeded'], True)
        self.assertIsInstance(record['duration_ms'], int)
        self.assertGreaterEqual(record['duration_ms'], 0)
        self.assertTrue(record['started_at'].endswith('+00:00'))

        # And the same questions are answerable when the re-plan fails.
        self.setUp()
        self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        job_id, result, _ = self.run_case(
            answers=self.first_candidate() + self.replan_answers(
                production=workflow.WORKFLOW_26_PROD),
            verify_results=NO_DELTA_ROUND + NO_DELTA_ROUND,
        )
        self.assertEqual(result['status'], REJECTED_NO_BEHAVIORAL_DELTA)
        record = json.loads(
            (self.root / 'jobs' / f'workflow-{job_id}'
             / 'semantic-replan.json').read_text(encoding='utf-8'))['semantic_replan']
        self.assertEqual(record['resulting_classification'], NO_BEHAVIORAL_DELTA)
        self.assertEqual(record['final_result'], REJECTED_NO_BEHAVIORAL_DELTA)
        self.assertIs(record['reached_succeeded'], False)

    def test_repeated_no_delta_terminates_fail_closed_within_budget(self):
        self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        job_id, result, prompts = self.run_case(
            answers=self.first_candidate() + self.replan_answers(
                production=workflow.WORKFLOW_26_PROD),
            verify_results=NO_DELTA_ROUND + NO_DELTA_ROUND,
        )
        self.assertEqual(result['status'], REJECTED_NO_BEHAVIORAL_DELTA)
        self.assertIsNotNone(result['finished_at'])
        # Exactly one re-plan: no second diagnosis, no third candidate.
        self.assertEqual(len(prompts), 8)
        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        self.assertTrue((workdir / 'attempt-6').is_dir())
        self.assertFalse((workdir / 'attempt-11').exists())
        self.assertFalse((workdir / 'repository.json').exists())
        checkout = workdir / 'repo'
        self.assertEqual(git(checkout, 'rev-parse', 'HEAD'),
                         self.spec['base_commit'])

    def test_replanned_candidate_reruns_every_gate_on_its_own_evidence(self):
        """The replacement candidate proves itself from scratch: its own
        compile/test run, its own baseline regression, its own added-coverage
        comparison and its own counterfactual."""
        cases = {
            'candidate verification': (
                [{'passed': False, 'repairable': False}],
                'not repairable',
            ),
            'baseline regression': (
                [{'passed': True, 'junit': {'tests': 14, 'failures': 0}},
                 {'passed': False, 'repairable': False}],
                'breaks the original test suite',
            ),
            'added coverage': (
                [{'passed': True, 'junit': {'tests': 12, 'failures': 0}},
                 {'passed': True, 'junit': {'tests': 12}}],
                'no new cases were added',
            ),
            'behavioral delta infrastructure': (
                [{'passed': True, 'junit': {'tests': 14, 'failures': 0}},
                 {'passed': True, 'junit': {'tests': 12}},
                 {'passed': False}],
                'Behavioral-delta',
            ),
        }
        for name, (second_round, message) in cases.items():
            with self.subTest(gate=name):
                self.setUp()
                self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
                _, result, _ = self.run_case(
                    answers=self.first_candidate() + self.replan_answers(),
                    verify_results=NO_DELTA_ROUND + second_round,
                )
                self.assertEqual(result['status'], 'failed')
                self.assertIn(message, result['error'])

    def test_replanned_candidate_starts_from_pinned_base_not_the_old_one(self):
        job_id, result, prompts = self.rescued()
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        base_production = git(self.repo, 'show', 'main:' + PROD, raw=True)
        # Verification rounds: candidate, baseline, hybrid, then the same
        # three for the re-planned candidate.
        self.assertEqual(len(self.observed_production_sources), 6)
        self.assertEqual(self.observed_production_sources[0], SUPERSEDED_PROD)
        self.assertEqual(self.observed_production_sources[3], workflow.GOOD_PROD)

        # The re-planned edit prompt showed base content, not the superseded
        # candidate's source: nothing of the discarded candidate carries over.
        replanned_edit = prompts[6]
        self.assertIn('public static String slugify', replanned_edit)
        self.assertNotIn('SUPERSEDED-CANDIDATE-MARKER', replanned_edit)
        # And the diagnosis prompt was built from the pinned base sources.
        self.assertIn(base_production.splitlines()[2].strip(), prompts[4])

    def test_previous_candidate_evidence_is_not_reused(self):
        job_id, result, _ = self.rescued()
        final = next(attempt['result'] for attempt in result['attempts']
                     if (attempt.get('result') or {}).get('profile') == 'repo-execute-v1')
        # 14 and 12 are the re-planned round's counts; the superseded round
        # reported 13 and 12.
        self.assertEqual(final['candidate_verification']['junit']['tests'], 14)
        self.assertEqual(final['behavioral_delta']['classification'],
                         DISTINGUISHING_TEST_FAILURE)
        self.assertEqual(final['semantic_replan_attempt'], 1)
        self.assertEqual(final['semantic_replan']['attempt'], 1)
        # The superseded candidate's own record is kept as history, and is
        # still marked failed rather than contributing to the final result.
        superseded = next(attempt for attempt in result['attempts']
                          if attempt['number'] == 3)
        self.assertIs(superseded['result']['passed'], False)
        self.assertEqual(superseded['result']['stage'], 'behavioral-delta')

    def test_ordinary_repair_budget_is_unchanged_and_separate(self):
        """Two repairs remain available to each candidate generation, and
        spending them does not spend the semantic budget or vice versa."""
        self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        repairable = {'passed': False, 'repairable': True,
                      'junit': {'tests': 13, 'failures': 1,
                                'diagnostics': 'expected x but was y'}}
        job_id, result, prompts = self.run_case(
            answers=[
                workflow.SELECTION, workflow.PLAN, SUPERSEDED_PROD,
                self.added_case_test(self.HYPHEN_CASE),
                workflow.REPAIR_PROD_FILE, workflow.GOOD_PROD_REPAIRED,
            ] + self.replan_answers(),
            verify_results=[
                repairable,
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                workflow.HYBRID_NO_DELTA,
                {'passed': True, 'junit': {'tests': 14, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                workflow.HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        phases = {attempt['number']: attempt['phase'] for attempt in result['attempts']}
        # Repair 1 of the first generation, then the semantic re-plan.
        self.assertIn(4, phases)
        self.assertEqual(phases[3], 'superseded-no-behavioral-delta')
        self.assertEqual(phases[6], 'behavioral-delta-replanning')
        self.assertEqual(SEMANTIC_REPLAN_BUDGET, 1)

    def test_a_replanned_candidate_still_gets_two_ordinary_repairs(self):
        self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        repairable = {'passed': False, 'repairable': True,
                      'junit': {'tests': 13, 'failures': 1,
                                'diagnostics': 'expected x but was y'}}
        job_id, result, prompts = self.run_case(
            answers=self.first_candidate() + [
                workflow.SEMANTIC_DIAGNOSIS, workflow.REPLAN_PLAN,
                workflow.GOOD_PROD, self.added_case_test(self.HYPHEN_CASE),
                workflow.REPAIR_PROD_FILE, workflow.GOOD_PROD_REPAIRED,
                workflow.REPAIR_PROD_FILE, workflow.WORKFLOW_26_PROD,
            ],
            verify_results=NO_DELTA_ROUND + [repairable, repairable, repairable],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('two bounded repairs', result['error'])
        # Repair attempts of the re-planned generation are 9 and 10.
        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        self.assertEqual(highest_attempt_number(workdir), 10)

    def test_invalid_semantic_diagnosis_fails_closed(self):
        for name, answer in {
            'not json': 'I think the base already does this.',
            'missing behavior': '{"diagnosis": "already supported"}',
            'empty behavior': '{"diagnosis": "d", "behavior": "   "}',
            'wrong type': '{"diagnosis": "d", "behavior": 42}',
        }.items():
            with self.subTest(answer=name):
                self.setUp()
                self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
                _, result, _ = self.run_case(
                    answers=self.first_candidate() + [answer],
                    verify_results=NO_DELTA_ROUND,
                )
                self.assertEqual(result['status'], 'failed')
                self.assertNotEqual(result['status'], 'succeeded')

    def test_the_advisory_prompt_enforces_nothing_deterministic_does(self):
        """The diagnostic prompt asks the model not to widen scope. That ask
        is not what stops it: a re-planned candidate that ignores every word
        of the advice is rejected by the same deterministic checks as any
        other candidate."""
        prompt = semantic_diagnosis_prompt(
            workflow.TASK, [PROD, TEST], self.repo)
        self.assertIn('Do not widen file scope', prompt)
        self.assertIn('Do not weaken or delete tests', prompt)

        # A re-plan that names an unselected file is rejected by plan
        # validation, not by the model's cooperation.
        self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        widened = plan_for([PROD, TEST])
        widened = json.loads(widened)
        widened['files'].append({'path': SECOND_PROD, 'reason': 'widen scope'})
        _, result, _ = self.run_case(
            answers=self.first_candidate() + [workflow.SEMANTIC_DIAGNOSIS,
                                              json.dumps(widened)],
            verify_results=NO_DELTA_ROUND,
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('unselected file', result['error'])

    def test_semantic_prompt_builders_respect_the_controller_limit(self):
        diagnosis = semantic_diagnosis_prompt(workflow.TASK, [PROD, TEST], self.repo)
        planning = semantic_replan_planning_prompt(
            workflow.TASK, [PROD, TEST], 'Collapse separator runs', self.repo)
        for prompt in (diagnosis, planning):
            self.assertLessEqual(len(prompt.encode()), 2000)
        self.assertIn('Collapse separator runs', planning)
        # An empty behavior is a construction error, not a silent prompt.
        with self.assertRaises(ValueError):
            semantic_replan_planning_prompt(workflow.TASK, [PROD, TEST], '  ', self.repo)

    def test_validate_semantic_diagnosis_bounds_advisory_text(self):
        diagnosis, behavior = validate_semantic_diagnosis(
            {'diagnosis': 'a\n b', 'behavior': 'c  d'})
        self.assertEqual((diagnosis, behavior), ('a b', 'c d'))
        long = validate_semantic_diagnosis(
            {'diagnosis': 'x' * 900, 'behavior': 'y' * 900})
        self.assertEqual(len(long[0]), 400)
        self.assertEqual(len(long[1]), 300)
        for bad in ({}, {'diagnosis': 'd'}, {'behavior': 'b'}, [], None,
                    {'diagnosis': '', 'behavior': 'b'}):
            with self.assertRaises(ValueError):
                validate_semantic_diagnosis(bad)


# ---------------------------------------------------------------------------
# Stage timing
# ---------------------------------------------------------------------------


TIMING_KEYS = (
    'candidate_verification',
    'baseline_verification',
    'behavioral_delta_prepare',
    'behavioral_delta_compile',
    'behavioral_delta_test',
    'behavioral_delta_total',
    'semantic_replan',
    'workflow_total',
)


class TimingTests(DeltaHarness):
    """Every stage the milestone asks about is measured, on a monotonic clock,
    and persisted for terminal states - including the ones that fail."""

    def timing(self, job_id):
        return json.loads((self.root / 'jobs' / f'workflow-{job_id}'
                           / 'timing.json').read_text(encoding='utf-8'))

    def test_successful_run_records_every_required_stage(self):
        self.pin_base_production(workflow.GENUINE_BASE_PROD)
        job_id, result, _ = self.run_case(
            answers=[workflow.SELECTION, workflow.PLAN, workflow.GOOD_PROD,
                     self.added_case_test(self.HYPHEN_CASE)],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                workflow.HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        record = self.timing(job_id)
        self.assertEqual(record['clock'], 'time.monotonic_ns')
        self.assertIn('No performance threshold', record['note'])
        timings = record['timing_ms']
        for key in TIMING_KEYS:
            with self.subTest(stage=key):
                self.assertIn(key, timings)
                self.assertIsInstance(timings[key], int)
                self.assertGreaterEqual(timings[key], 0)

        parts = sum(timings['behavioral_delta_' + name]
                    for name in ('prepare', 'compile', 'test'))
        # Each part is floored to whole milliseconds independently.
        self.assertLessEqual(abs(timings['behavioral_delta_total'] - parts), 3)
        self.assertGreaterEqual(timings['workflow_total'],
                                timings['behavioral_delta_total'])

        # The final workflow result carries the same measurements.
        final = next(attempt['result'] for attempt in result['attempts']
                     if (attempt.get('result') or {}).get('profile') == 'repo-execute-v1')
        for key in ('candidate_verification', 'baseline_verification',
                    'behavioral_delta_total', 'workflow_total'):
            self.assertIn(key, final['timing_ms'])

        # And the counterfactual's own artifact carries its own split.
        evidence = self.delta_evidence(job_id)
        self.assertEqual(sorted(evidence['timing_ms']),
                         ['compile', 'prepare', 'test', 'total'])

    def test_timing_is_recorded_for_rejected_and_failed_runs_too(self):
        self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        job_id, result, _ = self.run_case(
            answers=[workflow.SELECTION, workflow.PLAN, workflow.WORKFLOW_26_PROD,
                     self.added_case_test(self.HYPHEN_CASE),
                     workflow.SEMANTIC_DIAGNOSIS, workflow.REPLAN_PLAN,
                     workflow.WORKFLOW_26_PROD,
                     self.added_case_test(self.HYPHEN_CASE)],
            verify_results=NO_DELTA_ROUND + NO_DELTA_ROUND,
        )
        self.assertEqual(result['status'], REJECTED_NO_BEHAVIORAL_DELTA)
        record = self.timing(job_id)
        self.assertEqual(record['final_result'], REJECTED_NO_BEHAVIORAL_DELTA)
        # Both counterfactual runs are accounted for in one accumulated stage.
        self.assertGreaterEqual(record['timing_ms']['behavioral_delta_total'], 0)
        self.assertGreater(record['timing_ms']['semantic_replan'], 0)

        self.setUp()
        self.pin_base_production(workflow.GENUINE_BASE_PROD)
        job_id, result, _ = self.run_case(
            answers=[workflow.SELECTION, workflow.PLAN, workflow.GOOD_PROD,
                     self.added_case_test(self.HYPHEN_CASE)],
            verify_results=[{'passed': False, 'repairable': False}],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.timing(job_id)['final_result'], 'failed')


# ---------------------------------------------------------------------------
# D5: multi-file repository shapes
# ---------------------------------------------------------------------------


TEXTSTATS_EDIT = """package lab;
public class TextStats {
    // Whitespace-separated word counting.
    public static int countWords(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        return text.isBlank() ? 0 : text.trim().split("\\\\s+").length;
    }
}
"""

TEXTSTATS_TEST_EDIT = """package lab;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class TextStatsTest {
    @Test void words() { assertEquals(3, TextStats.countWords("one two three")); }
    @Test void whitespace() { assertEquals(2, TextStats.countWords("  one\\t two\\n")); }
    @Test void empty() { assertEquals(0, TextStats.countWords("   ")); }
    @Test void tabs() { assertEquals(2, TextStats.countWords("a\\tb")); }
    @Test void nullInput() { assertThrows(IllegalArgumentException.class, () -> TextStats.countWords(null)); }
}
"""


class MultiFileHybridTests(FlexibleHarness):
    """The hybrid state must be correct for every selected file, not just for
    a single production/test pair."""

    def setUp(self):
        super().setUp()
        self.watch = [PROD, SECOND_PROD, TEST, SECOND_TEST]

    def test_two_production_files_are_both_pinned_back_to_base(self):
        self.configure(editable_tests=[TEST])
        files = [PROD, SECOND_PROD, TEST]
        job_id, result, _ = self.run_case(
            answers=[selection(files), plan_for(files), workflow.GOOD_PROD,
                     TEXTSTATS_EDIT, workflow.GOOD_TEST],
            verify_results=[
                {'passed': True, 'junit': {'tests': 14, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                workflow.HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        manifest = self.evidence(job_id)['manifest']
        self.assertEqual(sorted(manifest['base_production_files']),
                         sorted([PROD, SECOND_PROD]))
        self.assertEqual(list(manifest['overlaid_candidate_test_files']), [TEST])
        self.assertEqual(manifest['hybrid_snapshot_sha256'],
                         manifest['expected_snapshot_sha256'])

        # The hybrid run saw base content for BOTH production files and the
        # candidate's test source.
        hybrid_round = self.observed[2]
        for name in (PROD, SECOND_PROD):
            self.assertEqual(
                hybrid_round[name],
                git(self.repo, 'show', 'main:' + name, raw=True), name)
        self.assertEqual(hybrid_round[TEST], workflow.GOOD_TEST)

        # Both candidate production files are restored and committed.
        checkout = self.workdir(job_id) / 'repo'
        self.assertEqual(git(checkout, 'show', 'HEAD:' + PROD, raw=True),
                         workflow.GOOD_PROD)
        self.assertEqual(git(checkout, 'show', 'HEAD:' + SECOND_PROD, raw=True),
                         TEXTSTATS_EDIT)

    def test_two_test_files_are_both_overlaid(self):
        self.configure(editable_tests=[TEST, SECOND_TEST])
        files = [PROD, TEST, SECOND_TEST]
        job_id, result, _ = self.run_case(
            answers=[selection(files), plan_for(files), workflow.GOOD_PROD,
                     workflow.GOOD_TEST, TEXTSTATS_TEST_EDIT],
            verify_results=[
                {'passed': True, 'junit': {'tests': 14, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                workflow.HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        manifest = self.evidence(job_id)['manifest']
        self.assertEqual(sorted(manifest['overlaid_candidate_test_files']),
                         sorted([TEST, SECOND_TEST]))
        self.assertEqual(manifest['base_production_files'], [PROD])
        self.assertEqual(manifest['hybrid_snapshot_sha256'],
                         manifest['expected_snapshot_sha256'])

        hybrid_round = self.observed[2]
        self.assertEqual(hybrid_round[TEST], workflow.GOOD_TEST)
        self.assertEqual(hybrid_round[SECOND_TEST], TEXTSTATS_TEST_EDIT)
        self.assertEqual(hybrid_round[PROD],
                         git(self.repo, 'show', 'main:' + PROD, raw=True))

    def test_hybrid_construction_with_two_production_and_two_test_files(self):
        """The approved selection is capped at three files, so the 2+2 shape
        is exercised directly against the construction the workflow uses: a
        real checkout, the real revert, and the real manifest arithmetic."""
        self.configure(editable_tests=[TEST, SECOND_TEST])
        base = self.spec['base_commit']
        checkout = self.root / 'checkout'
        git(self.root, 'clone', '--no-hardlinks', '--', str(self.repo), str(checkout))
        git(checkout, 'checkout', '-b', 'hybrid-fixture', base)

        paths = [row.split('\t', 1)[1]
                 for row in git(checkout, 'ls-tree', '-r', base).splitlines()]
        original = {name: hashlib.sha256((checkout / name).read_bytes()).hexdigest()
                    for name in paths}

        selected = [PROD, SECOND_PROD, TEST, SECOND_TEST]
        candidate = {PROD: workflow.GOOD_PROD, SECOND_PROD: TEXTSTATS_EDIT,
                     TEST: workflow.GOOD_TEST, SECOND_TEST: TEXTSTATS_TEST_EDIT}
        for name, source in candidate.items():
            (checkout / name).write_text(source, encoding='utf-8')

        tests = [TEST, SECOND_TEST]
        overlaid = hybrid_overlay_files(selected, tests)
        reverted = hybrid_reverted_files(selected, tests)
        self.assertEqual(overlaid, tests)
        self.assertEqual(reverted, [PROD, SECOND_PROD])

        overlaid_hashes = {
            name: hashlib.sha256((checkout / name).read_bytes()).hexdigest()
            for name in overlaid}
        expected = hybrid_manifest(original, overlaid_hashes)

        with pinned_base_production(checkout, base, reverted):
            # This is the state the verifier would snapshot.
            actual = {name: hashlib.sha256((checkout / name).read_bytes()).hexdigest()
                      for name in paths}
            for name in reverted:
                self.assertEqual(
                    (checkout / name).read_text(encoding='utf-8'),
                    git(checkout, 'show', base + ':' + name, raw=True), name)
            for name in overlaid:
                self.assertEqual((checkout / name).read_text(encoding='utf-8'),
                                 candidate[name], name)

        self.assertEqual(actual, expected)
        # No candidate production content can appear in the hybrid state.
        for name in reverted:
            self.assertEqual(actual[name], original[name])
            self.assertNotEqual(
                actual[name],
                hashlib.sha256(candidate[name].encode()).hexdigest())

        # Candidate production is restored afterwards, for every file.
        for name, source in candidate.items():
            self.assertEqual((checkout / name).read_text(encoding='utf-8'), source)

    def test_candidate_production_is_restored_even_when_the_hybrid_raises(self):
        self.configure(editable_tests=[TEST])
        base = self.spec['base_commit']
        checkout = self.root / 'checkout'
        git(self.root, 'clone', '--no-hardlinks', '--', str(self.repo), str(checkout))
        git(checkout, 'checkout', '-b', 'hybrid-fixture', base)
        (checkout / PROD).write_text(workflow.GOOD_PROD, encoding='utf-8')

        with self.assertRaisesRegex(RuntimeError, 'verifier exploded'):
            with pinned_base_production(checkout, base, [PROD]):
                raise RuntimeError('verifier exploded')

        self.assertEqual((checkout / PROD).read_text(encoding='utf-8'),
                         workflow.GOOD_PROD)


# ---------------------------------------------------------------------------
# D6: a meaningfully larger repository and suite
# ---------------------------------------------------------------------------


def widget_production(index):
    return (
        "package lab;\n"
        f"public class Widget{index} {{\n"
        "    public static int value(int n) {\n"
        f"        return n + {index};\n"
        "    }\n"
        "}\n"
    )


def widget_test(index):
    cases = "\n".join(
        f"    @Test void case{case}() "
        f"{{ assertEquals({index + case}, Widget{index}.value({case})); }}"
        for case in range(6)
    )
    return (
        "package lab;\n"
        "import org.junit.jupiter.api.Test;\n"
        "import static org.junit.jupiter.api.Assertions.*;\n"
        f"class Widget{index}Test {{\n"
        f"{cases}\n"
        "}\n"
    )


class LargerSuiteTests(FlexibleHarness):
    """The Slugs lab is two production files and twelve cases. This runs the
    same orchestration against eight production files, eight test files and a
    48-case suite, and records what it cost."""

    WIDGETS = range(1, 7)

    def build_larger_lab(self):
        for index in self.WIDGETS:
            (self.repo / f'src/main/java/lab/Widget{index}.java').write_text(
                widget_production(index), encoding='utf-8')
            (self.repo / f'src/test/java/lab/Widget{index}Test.java').write_text(
                widget_test(index), encoding='utf-8')
        self.commit_fixture('Grow the lab to eight production and test files')
        self.configure(
            editable=[PROD, SECOND_PROD] + [
                f'src/main/java/lab/Widget{index}.java' for index in self.WIDGETS],
            editable_tests=[TEST, SECOND_TEST] + [
                f'src/test/java/lab/Widget{index}Test.java' for index in self.WIDGETS],
            minimum=48,
        )

    def test_larger_repository_runs_the_same_gates_and_is_measured(self):
        self.build_larger_lab()
        files = [PROD, TEST]
        job_id, result, _ = self.run_case(
            answers=[selection(files), plan_for(files), workflow.GOOD_PROD,
                     workflow.GOOD_TEST],
            verify_results=[
                {'passed': True, 'junit': {'tests': 49, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 48}},
                workflow.HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        evidence = self.evidence(job_id)
        manifest = evidence['manifest']
        tracked = git(self.repo, 'ls-tree', '-r', '--name-only',
                      self.spec['base_commit']).splitlines()
        self.assertGreaterEqual(len(tracked), 20)
        # Every tracked path is pinned in the hybrid manifest, not just the
        # selected ones, and the hybrid the verifier built matches exactly.
        self.assertEqual(sorted(manifest['expected_snapshot_sha256']),
                         sorted(tracked))
        self.assertEqual(manifest['hybrid_snapshot_sha256'],
                         manifest['expected_snapshot_sha256'])
        self.assertEqual(evidence['evidence_level'], EVIDENCE_BEHAVIORAL)

        # Cost and size, recorded rather than thresholded.
        workdir = self.workdir(job_id)
        timing = json.loads((workdir / 'timing.json').read_text(encoding='utf-8'))
        artifacts = [path for path in workdir.rglob('*')
                     if path.is_file() and 'repo/.git' not in path.as_posix()]
        report = {
            'tracked_files': len(tracked),
            'suite_cases': 48,
            'editable_production_files': 8,
            'editable_test_files': 8,
            'timing_ms': timing['timing_ms'],
            'artifact_files': len(artifacts),
            'artifact_bytes': sum(path.stat().st_size for path in artifacts),
            'note': ('Canned verifier: durations measure orchestration only, '
                     'not Gradle. Recorded for scale comparison, not as a '
                     'performance threshold.'),
        }
        (self.root / 'larger-suite-report.json').write_text(
            json.dumps(report, indent=2), encoding='utf-8')
        self.assertGreater(report['artifact_bytes'], 0)
        self.assertGreaterEqual(timing['timing_ms']['workflow_total'], 0)

    def test_larger_repository_still_rejects_a_no_delta_candidate(self):
        self.build_larger_lab()
        files = [PROD, TEST]
        _, result, _ = self.run_case(
            answers=[selection(files), plan_for(files), workflow.GOOD_PROD,
                     workflow.GOOD_TEST, workflow.SEMANTIC_DIAGNOSIS,
                     plan_for(files), SUPERSEDED_PROD, workflow.GOOD_TEST],
            verify_results=[
                {'passed': True, 'junit': {'tests': 49, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 48}},
                workflow.HYBRID_NO_DELTA,
                {'passed': True, 'junit': {'tests': 49, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 48}},
                workflow.HYBRID_NO_DELTA,
            ],
        )
        self.assertEqual(result['status'], REJECTED_NO_BEHAVIORAL_DELTA)


# ---------------------------------------------------------------------------
# E: the test-side logic risk
# ---------------------------------------------------------------------------


# Adversarial fixture: the candidate test computes its own expected value with
# a helper that reimplements the production algorithm. The assertion is then
# "production agrees with this copy of production" rather than "production
# does what the task asked for". The counterfactual still distinguishes base
# from candidate - the helper is identical in both runs, so only production
# content differs - but nothing here proves the assertion is meaningful.
TAUTOLOGICAL_TEST = """package lab;
import java.util.Locale;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class SlugsTest {
    private static String expectedSlug(String text) {
        String lowercased = text.toLowerCase(Locale.ROOT);
        String normalized = lowercased.replaceAll("[^a-z0-9]+", "-");
        return normalized.replaceAll("^-*|-*$", "");
    }
    @Test void basic() { assertEquals("hello-world", Slugs.slugify("Hello World")); }
    @Test void spaces() { assertEquals("hello-world", Slugs.slugify("  Hello   World  ")); }
    @Test void punctuation() { assertEquals("hello-world", Slugs.slugify("Hello, World!")); }
    @Test void repeatedSeparators() { assertEquals("a-b", Slugs.slugify("a---___b")); }
    @Test void digits() { assertEquals("java-21", Slugs.slugify("Java 21")); }
    @Test void empty() { assertEquals("", Slugs.slugify("")); }
    @Test void punctuationOnly() { assertEquals("", Slugs.slugify(" !!! ")); }
    @Test void nullInput() { assertThrows(IllegalArgumentException.class, () -> Slugs.slugify(null)); }
    @Test void hyphens() { assertEquals(expectedSlug("--Hello-World--"), Slugs.slugify("--Hello-World--")); }
}
"""


class TestSideLogicTests(DeltaHarness):
    """Deliberate adversarial coverage for a risk this milestone does NOT
    fully mitigate. These tests pin the current, honest behavior: the gate
    bounds the risk, the deterministic review does not catch it, and the
    limitation is recorded rather than papered over."""

    def run_tautological_case(self):
        self.pin_base_production(workflow.GENUINE_BASE_PROD)
        return self.run_case(
            answers=[workflow.SELECTION, workflow.PLAN, workflow.GOOD_PROD,
                     TAUTOLOGICAL_TEST],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                workflow.HYBRID_DISTINGUISHING,
            ],
        )

    def test_a_test_side_reimplementation_is_not_rejected(self):
        """Known limitation, asserted so it cannot regress silently into a
        claim the system does not support."""
        job_id, result, _ = self.run_tautological_case()
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertEqual(self.delta_evidence(job_id)['evidence_level'],
                         EVIDENCE_BEHAVIORAL)

    def test_the_deterministic_review_does_not_catch_it(self):
        """Evidence for the documented limitation: the targeted review rules
        are about the Numbers profile, and none of them reads a test helper."""
        review = review_source(TAUTOLOGICAL_TEST, 'diff')
        self.assertEqual(review['status'], 'passed')
        self.assertEqual(review['findings'], [])

    def test_candidate_tests_are_byte_identical_in_both_worlds(self):
        """What the counterfactual DOES bound: the candidate test sources are
        the same bytes in the candidate run and in the hybrid run, so any
        difference in outcome is caused by production content - even when the
        test file contains helpers."""
        job_id, result, _ = self.run_tautological_case()
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        candidate_round, _, hybrid_round = self.observed_test_sources
        self.assertEqual(candidate_round, TAUTOLOGICAL_TEST)
        self.assertEqual(hybrid_round, TAUTOLOGICAL_TEST)

        evidence = self.delta_evidence(job_id)
        self.assertEqual(
            evidence['manifest']['overlaid_candidate_test_files'][TEST],
            hashlib.sha256(TAUTOLOGICAL_TEST.encode()).hexdigest())

    def test_the_advisory_observation_is_recorded_and_gates_nothing(self):
        job_id, result, _ = self.run_tautological_case()
        final = next(attempt['result'] for attempt in result['attempts']
                     if (attempt.get('result') or {}).get('profile') == 'repo-execute-v1')
        observation = final['review']['test_side_logic']
        self.assertIs(observation['advisory'], True)
        self.assertIs(observation['gating'], False)
        self.assertGreater(observation['added_lines']['test'], 0)
        self.assertGreater(observation['test_added_line_share'], 0)
        self.assertIn('nothing is rejected on these numbers', observation['note'])
        # It is an observation, not a rule: the workflow succeeded with it.
        self.assertEqual(result['status'], 'succeeded')

    def test_diff_line_counts_reads_only_the_diff(self):
        patch = (
            'diff --git a/a.java b/a.java\n'
            '--- a/a.java\n'
            '+++ b/a.java\n'
            '@@ -1,2 +1,3 @@\n'
            ' context\n'
            '-removed\n'
            '+added one\n'
            '+added two\n'
            'diff --git a/b.java b/b.java\n'
            '--- a/b.java\n'
            '+++ b/b.java\n'
            '@@ -0,0 +1 @@\n'
            '+only\n'
        )
        added, removed = diff_line_counts(patch)
        self.assertEqual(added, {'a.java': 2, 'b.java': 1})
        self.assertEqual(removed, {'a.java': 1, 'b.java': 0})
        self.assertEqual(diff_line_counts(''), ({}, {}))
        self.assertEqual(diff_line_counts(None), ({}, {}))

    def test_test_side_observation_counts_only_selected_files(self):
        patch = (
            'diff --git a/' + PROD + ' b/' + PROD + '\n'
            '--- a/' + PROD + '\n'
            '+++ b/' + PROD + '\n'
            '+one\n'
            'diff --git a/' + TEST + ' b/' + TEST + '\n'
            '--- a/' + TEST + '\n'
            '+++ b/' + TEST + '\n'
            '+one\n+two\n+three\n'
            'diff --git a/' + SECOND_TEST + ' b/' + SECOND_TEST + '\n'
            '--- a/' + SECOND_TEST + '\n'
            '+++ b/' + SECOND_TEST + '\n'
            '+outside the selection\n'
        )
        observation = test_side_logic_observation(
            patch, [PROD, TEST], [TEST, SECOND_TEST])
        self.assertEqual(observation['added_lines'],
                         {'production': 1, 'test': 3})
        self.assertEqual(observation['test_added_line_share'], 0.75)
        # Nothing changed produces no share rather than a fabricated zero.
        self.assertIsNone(
            test_side_logic_observation('', [PROD, TEST], [TEST])
            ['test_added_line_share'])


if __name__ == '__main__':
    unittest.main()
