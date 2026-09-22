"""Milestone 7B.1: the recorded API compile-failure policy experiment.

`distinguishing-api-compile-failure` proves the candidate tests reference API
the pinned base does not declare. It does not prove that runtime behavior
differs, because a test-compilation failure aborts the suite before any
assertion executes.

Three policy shapes were available:

    A  allow structural evidence           -> continue
    B  require additional robust evidence  -> continue only with more evidence
    C  structural evidence is insufficient -> reject

This module is the experiment that chose between them, not a description of
the choice. It runs the two fixtures the milestone requires - a legitimate new
API and a deliberately trivial one - through the real workflow, records what
each policy would do with the evidence each produced, and asserts the finding
the decision rests on: the two are indistinguishable at this evidence level.
"""

import json
import pathlib
import unittest

import test_repo_execute_workflow as workflow
from nullcode.repo.repo_execute_workflow import (
    API_COMPILE_POLICY,
    CONTINUE,
    DISTINGUISHING_API_COMPILE_FAILURE,
    EVIDENCE_STRUCTURAL,
    POLICY_ALLOW_STRUCTURAL,
    POLICY_REJECT_STRUCTURAL,
    POLICY_REQUIRE_ADDITIONAL,
    REJECT,
    UNAVAILABLE,
    classify_behavioral_delta,
    evidence_level,
    policy_decision,
)

# ---------------------------------------------------------------------------
# Fixture 1 - a legitimate new API whose behavior the candidate tests exercise
# ---------------------------------------------------------------------------
LEGITIMATE_API_PROD = '''package lab;
import java.util.Locale;
public class Slugs {
    public static String slugify(String text) {
        if (text == null) {
            throw new IllegalArgumentException("Input text cannot be null");
        }
        String lowercased = text.toLowerCase(Locale.ROOT);
        String normalized = lowercased.replaceAll("[^a-z0-9]+", "-");
        return normalized.replaceAll("^-*|-*$", "");
    }

    public static String slugifyUnicode(String text) {
        return slugify(java.text.Normalizer.normalize(
            text, java.text.Normalizer.Form.NFD));
    }
}
'''

LEGITIMATE_CASE = (
    '    @Test void unicode() '
    '{ assertEquals("aeo", Slugs.slugifyUnicode("äëö")); }\n'
)

# ---------------------------------------------------------------------------
# Fixture 2 - a structurally new but semantically empty API
# ---------------------------------------------------------------------------
TRIVIAL_API_PROD = '''package lab;
import java.util.Locale;
public class Slugs {
    public static String slugify(String text) {
        if (text == null) {
            throw new IllegalArgumentException("Input text cannot be null");
        }
        String lowercased = text.toLowerCase(Locale.ROOT);
        String normalized = lowercased.replaceAll("[^a-z0-9]+", "-");
        return normalized.replaceAll("^-*|-*$", "");
    }

    public static String marker() {
        return "";
    }
}
'''

TRIVIAL_CASE = (
    '    @Test void marker() '
    '{ assertEquals("", Slugs.marker()); }\n'
)


def api_compile_failure(symbol, call):
    """A hybrid record shaped exactly as Gradle reports a candidate test that
    calls a method pinned-base production does not declare."""
    log = (
        '> Task :compileJava\n'
        '> Task :compileTestJava FAILED\n'
        f'/work/project/{workflow.TEST_TARGET}:13: error: cannot find symbol\n'
        f'    {call}\n'
        '                                   ^\n'
        f'  symbol:   method {symbol}\n'
        '  location: class lab.Slugs\n'
        '1 error\n'
        'FAILURE: Build failed with an exception.\n'
    )
    return {'passed': False,
            'compile': {'exit_code': 1, 'timed_out': False, 'log': log}}


LEGITIMATE_HYBRID = api_compile_failure(
    'slugifyUnicode(String)', 'assertEquals("aeo", Slugs.slugifyUnicode("aeo"));')
TRIVIAL_HYBRID = api_compile_failure(
    'marker()', 'assertEquals("", Slugs.marker());')

POLICIES = (POLICY_ALLOW_STRUCTURAL, POLICY_REQUIRE_ADDITIONAL,
            POLICY_REJECT_STRUCTURAL)


class ApiCompilePolicyExperiment(unittest.TestCase):
    """Run both fixtures, then record what each policy does with the result."""

    def run_api_fixture(self, production, case, hybrid):
        """One fixture, in its own repository, through the real workflow."""
        harness = workflow.BehavioralDeltaTests()
        harness.setUp()
        self.harness = harness
        harness.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        job_id, result, _ = harness.run_case(
            answers=[workflow.SELECTION, workflow.PLAN, production,
                     harness.added_case_test(case)],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                hybrid,
            ],
        )
        return result, harness.delta_evidence(job_id)

    def test_policy_experiment_decides_the_api_compile_policy(self):
        legitimate_result, legitimate = self.run_api_fixture(
            LEGITIMATE_API_PROD, LEGITIMATE_CASE, LEGITIMATE_HYBRID)
        trivial_result, trivial = self.run_api_fixture(
            TRIVIAL_API_PROD, TRIVIAL_CASE, TRIVIAL_HYBRID)

        # Both fixtures produce the same classification and the same evidence
        # level, under the policy this workflow actually enforces.
        for evidence in (legitimate, trivial):
            self.assertEqual(evidence['classification'],
                             DISTINGUISHING_API_COMPILE_FAILURE)
            self.assertEqual(evidence['evidence_level'], EVIDENCE_STRUCTURAL)

        # THE FINDING the decision rests on: nothing in the evidence separates
        # a legitimate new API from a semantically empty one. The
        # classification, the evidence level, the diagnostic, the compile exit
        # code and the JUnit evidence are identical; a compile failure aborts
        # the suite before any assertion can distinguish them.
        for field in ('classification', 'evidence_level', 'diagnostic',
                      'compile_exit_code', 'tests_exit_code', 'junit',
                      'policy_decision'):
            self.assertEqual(legitimate[field], trivial[field], field)

        # Both therefore succeed under the selected policy...
        self.assertEqual(API_COMPILE_POLICY, POLICY_ALLOW_STRUCTURAL)
        for result in (legitimate_result, trivial_result):
            self.assertEqual(result['status'], 'succeeded', result.get('error'))

        # ...and the decision matrix records what each alternative would cost.
        matrix = {
            policy: {
                'legitimate-new-api': policy_decision(
                    legitimate['classification'], policy),
                'trivial-new-api': policy_decision(
                    trivial['classification'], policy),
            }
            for policy in POLICIES
        }

        self.assertEqual(matrix[POLICY_ALLOW_STRUCTURAL],
                         {'legitimate-new-api': CONTINUE,
                          'trivial-new-api': CONTINUE})
        # Policy B cannot be implemented from existing structured information:
        # separating these two fixtures needs Java/JUnit source semantics,
        # which this milestone excludes and which would be brittle rather than
        # robust. The experiment records that rather than inventing a check.
        self.assertEqual(matrix[POLICY_REQUIRE_ADDITIONAL],
                         {'legitimate-new-api': UNAVAILABLE,
                          'trivial-new-api': UNAVAILABLE})
        # Policy C excludes the weak change only by rejecting the legitimate
        # one with it: one false negative per false positive avoided, on a
        # task shape ("add a method") that is entirely normal for this profile.
        self.assertEqual(matrix[POLICY_REJECT_STRUCTURAL],
                         {'legitimate-new-api': REJECT,
                          'trivial-new-api': REJECT})

        record = {
            'experiment': 'api-compile-failure-policy',
            'selected_policy': API_COMPILE_POLICY,
            'evidence_level': EVIDENCE_STRUCTURAL,
            'fixtures': {
                'legitimate-new-api': {
                    'classification': legitimate['classification'],
                    'evidence_level': legitimate['evidence_level'],
                    'workflow_result': legitimate_result['status'],
                },
                'trivial-new-api': {
                    'classification': trivial['classification'],
                    'evidence_level': trivial['evidence_level'],
                    'workflow_result': trivial_result['status'],
                },
            },
            'decision_matrix': matrix,
            'legitimate_changes_rejected': {
                policy: matrix[policy]['legitimate-new-api'] == REJECT
                for policy in POLICIES
            },
            'weak_changes_admitted': {
                policy: matrix[policy]['trivial-new-api'] == CONTINUE
                for policy in POLICIES
            },
            'finding': (
                'The hybrid evidence produced by a legitimate new API and by a '
                'trivial one is identical: same classification, same '
                'diagnostic, same (absent) JUnit evidence. No deterministic '
                'discriminator exists at this evidence level without parsing '
                'Java/JUnit sources.'
            ),
        }
        (self.harness.root / 'policy-experiment.json').write_text(
            json.dumps(record, indent=2), encoding='utf-8')

    def test_structural_evidence_is_never_reported_as_behavioral(self):
        _, evidence = self.run_api_fixture(
            TRIVIAL_API_PROD, TRIVIAL_CASE, TRIVIAL_HYBRID)
        self.assertEqual(evidence['evidence_level'], EVIDENCE_STRUCTURAL)
        summary = evidence['evidence_summary']
        self.assertIn('Structural evidence only', summary)
        self.assertIn('does NOT prove that runtime behavior differs', summary)
        # The diagnostic describes a compile-time fact, never an observed one.
        self.assertIn('do not compile against pinned-base production',
                      evidence['diagnostic'])

    def test_policy_table_and_classifier_agree_on_every_classification(self):
        overlaid = [workflow.TEST_TARGET]
        classification, _ = classify_behavioral_delta(
            LEGITIMATE_HYBRID, overlaid)
        self.assertEqual(classification, DISTINGUISHING_API_COMPILE_FAILURE)
        self.assertEqual(evidence_level(classification), EVIDENCE_STRUCTURAL)
        self.assertEqual(policy_decision(classification), CONTINUE)

        # A policy name the table does not define is an error, never a silent
        # "allow".
        with self.assertRaises(ValueError):
            policy_decision(DISTINGUISHING_API_COMPILE_FAILURE, 'D-invented')


if __name__ == '__main__':
    unittest.main()
