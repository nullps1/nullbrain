"""Milestone 7B.2: typed repair-target routing.

A repair-selection reply names a fault domain ("production" or "test") as well
as a file, and the two must agree. The validator checks that agreement
deterministically; it never parses `reason`, never corrects a contradiction,
and a rejected reply ends the workflow - there is no second routing call.

Workflow 32 (live, Patient Zero) is the motivating shape: correct production,
a wrong test expectation, and a routing reply whose reason blamed the test
while its target named the production file.
"""

import itertools
import json
import unittest

import test_behavioral_delta_evidence as evidence
import test_patient_zero_compat as patient_zero
import test_repo_execute_workflow as workflow
from nullcode.repo.repo_execute_workflow import (
    FAULT_DOMAINS,
    SEMANTIC_REPLAN_BUDGET,
    highest_attempt_number,
    repair_selection_prompt,
    validate_repair_selection,
    verification_diagnostic,
)
from nullcode.repo.repo_workflow import git

PROD = workflow.PROD_TARGET                      # src/main/java/lab/Slugs.java
TEST = workflow.TEST_TARGET                      # src/test/java/lab/SlugsTest.java
TEXT_STATS = 'src/main/java/lab/TextStats.java'
TEXT_STATS_TEST = 'src/test/java/lab/TextStatsTest.java'

W32_TASK = 'Add a small tested behavior improvement consistent with the existing project API.'

# The generated production method was correct: it counts every character of
# the punctuation set.
W32_PROD = r'''package lab;
public class TextStats {
    private static final String PUNCTUATION = ".,!?;:'\"()[]{}<>/\\-_+=&*^%$#@~`";

    public static int countWords(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        return text.isBlank() ? 0 : text.trim().split("\\s+").length;
    }

    public static int countPunctuation(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        int count = 0;
        for (char c : text.toCharArray()) {
            if (PUNCTUATION.indexOf(c) >= 0) count++;
        }
        return count;
    }
}
'''

# A production edit that really is wrong, for the valid production-domain path.
W32_PROD_BROKEN = W32_PROD.replace('count++;', 'count += 2;')

W32_TEST_HEAD = r'''package lab;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class TextStatsTest {
    @Test void words() { assertEquals(3, TextStats.countWords("one two three")); }
    @Test void whitespace() { assertEquals(2, TextStats.countWords("  one\t two\n")); }
    @Test void empty() { assertEquals(0, TextStats.countWords("   ")); }
    @Test void nullInput() { assertThrows(IllegalArgumentException.class, () -> TextStats.countWords(null)); }
    @Test void countPunctuation() {
        assertEquals(3, TextStats.countPunctuation("!!!"));
        assertEquals(1, TextStats.countPunctuation("one!two"));
        assertEquals(0, TextStats.countPunctuation("onetwo"));
'''

# The generated test's one inconsistent expectation: 4 for a 31-character set.
W32_TEST = W32_TEST_HEAD + r'''        assertEquals(4, TextStats.countPunctuation(".,!?;:'\"()[]{}<>/\\-_+=&*^%$#@~`"));
    }
}
'''

W32_TEST_FIXED = W32_TEST.replace('assertEquals(4,', 'assertEquals(31,')

W32_FAILURE = {
    'passed': False,
    'repairable': True,
    'tests': {
        'exit_code': 1,
        'log': ('TextStatsTest > countPunctuation() FAILED\n'
                '    org.opentest4j.AssertionFailedError: expected: <4> but was: <31>\n'),
    },
    'junit': {
        'tests': 13, 'failures': 1, 'skipped': 0,
        'diagnostics': 'countPunctuation(): expected: <4> but was: <31>',
    },
}

PASSED_13 = {'passed': True, 'junit': {'tests': 13, 'failures': 0}}
BASELINE_12 = {'passed': True, 'junit': {'tests': 12}}


def route(domain, file, reason='evidence'):
    return json.dumps({'fault_domain': domain, 'file': file, 'reason': reason})


# The two contradictions the milestone is about, verbatim in shape.
W32_CONTRADICTION = json.dumps({
    'fault_domain': 'test',
    'file': TEXT_STATS,
    'reason': 'The test expectation is incorrect.',
})

W32_MIRROR = json.dumps({
    'fault_domain': 'production',
    'file': TEXT_STATS_TEST,
    'reason': 'The implementation is incorrect.',
})

# Every malformed domain the design names. None may be normalized into validity.
MALFORMED_REPLIES = {
    'missing fault_domain': {'file': TEXT_STATS_TEST, 'reason': 'r'},
    'null fault_domain': {'fault_domain': None, 'file': TEXT_STATS_TEST, 'reason': 'r'},
    'non-string fault_domain': {'fault_domain': 1, 'file': TEXT_STATS_TEST, 'reason': 'r'},
    'list fault_domain': {'fault_domain': ['test'], 'file': TEXT_STATS_TEST, 'reason': 'r'},
    'empty fault_domain': {'fault_domain': '', 'file': TEXT_STATS_TEST, 'reason': 'r'},
    'capitalized "Test"': {'fault_domain': 'Test', 'file': TEXT_STATS_TEST, 'reason': 'r'},
    'leading space " test"': {'fault_domain': ' test', 'file': TEXT_STATS_TEST, 'reason': 'r'},
    'trailing space "test "': {'fault_domain': 'test ', 'file': TEXT_STATS_TEST, 'reason': 'r'},
    '"both"': {'fault_domain': 'both', 'file': TEXT_STATS_TEST, 'reason': 'r'},
    '"unknown"': {'fault_domain': 'unknown', 'file': TEXT_STATS_TEST, 'reason': 'r'},
    'alias "tests"': {'fault_domain': 'tests', 'file': TEXT_STATS_TEST, 'reason': 'r'},
    'alias "implementation"': {'fault_domain': 'implementation', 'file': TEXT_STATS, 'reason': 'r'},
    # The pre-7B.2 reply shape. No default domain is derived from the file.
    'legacy file + reason': {'file': TEXT_STATS_TEST, 'reason': 'r'},
    'legacy production file + reason': {'file': TEXT_STATS, 'reason': 'r'},
}


# ---------------------------------------------------------------------------
# The validator on its own
# ---------------------------------------------------------------------------


class RoutingValidatorTests(unittest.TestCase):
    PRODUCTION = ['src/main/java/lab/Slugs.java', TEXT_STATS]
    TESTS = [TEST, TEXT_STATS_TEST]

    def validate(self, data, candidates, required=None):
        return validate_repair_selection(
            data, candidates, self.PRODUCTION, self.TESTS,
            required_domain=required, repair_number=1)

    def test_vocabulary_is_exactly_production_and_test(self):
        self.assertEqual(FAULT_DOMAINS, ('production', 'test'))

    def test_valid_test_domain_routing(self):
        self.assertEqual(
            self.validate({'fault_domain': 'test', 'file': TEXT_STATS_TEST,
                           'reason': ' wrong expectation '},
                          [TEXT_STATS, TEXT_STATS_TEST]),
            ('test', TEXT_STATS_TEST, 'wrong expectation'))

    def test_valid_production_domain_routing(self):
        self.assertEqual(
            self.validate({'fault_domain': 'production', 'file': TEXT_STATS,
                           'reason': 'off by one'},
                          [TEXT_STATS, TEXT_STATS_TEST]),
            ('production', TEXT_STATS, 'off by one'))

    def test_workflow_32_contradiction_is_rejected(self):
        with self.assertRaisesRegex(
                ValueError,
                "routing is contradictory: fault_domain 'test' but "
                + TEXT_STATS + " is not a selected test file"):
            self.validate(json.loads(W32_CONTRADICTION), [TEXT_STATS, TEXT_STATS_TEST])

    def test_mirror_contradiction_is_rejected(self):
        with self.assertRaisesRegex(
                ValueError,
                "routing is contradictory: fault_domain 'production' but "
                + TEXT_STATS_TEST + " is not a selected production file"):
            self.validate(json.loads(W32_MIRROR), [TEXT_STATS, TEXT_STATS_TEST])

    def test_malformed_domains_are_rejected_not_normalized(self):
        for name, data in MALFORMED_REPLIES.items():
            with self.subTest(reply=name):
                with self.assertRaisesRegex(ValueError, 'fault_domain'):
                    self.validate(data, [TEXT_STATS, TEXT_STATS_TEST])

    def test_non_object_reply_is_rejected(self):
        for data in (None, [], 'test', 3):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, 'JSON object'):
                    self.validate(data, [TEXT_STATS, TEXT_STATS_TEST])

    def test_unapproved_file_still_fails_in_either_domain(self):
        for domain in FAULT_DOMAINS:
            for name in ('src/main/java/lab/Other.java', 'build.gradle', None, 7):
                with self.subTest(domain=domain, file=name):
                    with self.assertRaisesRegex(ValueError, 'unapproved file'):
                        self.validate({'fault_domain': domain, 'file': name,
                                       'reason': 'r'},
                                      [TEXT_STATS, TEXT_STATS_TEST])

    def test_selected_but_not_offered_file_is_unapproved(self):
        # Offered candidates, not the whole selection, bound the choice.
        with self.assertRaisesRegex(ValueError, 'unapproved file'):
            self.validate({'fault_domain': 'production', 'file': TEXT_STATS,
                           'reason': 'r'}, [TEXT_STATS_TEST])

    def test_insufficient_test_count_requires_the_test_domain(self):
        # Only selected test files are offered for this failure class.
        offered = [TEXT_STATS_TEST]
        self.assertEqual(
            self.validate({'fault_domain': 'test', 'file': TEXT_STATS_TEST,
                           'reason': 'restore cases'}, offered, required='test'),
            ('test', TEXT_STATS_TEST, 'restore cases'))
        with self.assertRaisesRegex(
                ValueError, "requires fault_domain 'test'"):
            self.validate({'fault_domain': 'production', 'file': TEXT_STATS_TEST,
                           'reason': 'r'}, offered, required='test')
        with self.assertRaisesRegex(ValueError, 'unapproved file'):
            self.validate({'fault_domain': 'production', 'file': TEXT_STATS,
                           'reason': 'r'}, offered, required='test')

    def test_two_production_one_test_selection(self):
        offered = [PROD, TEXT_STATS, TEXT_STATS_TEST]
        for name in (PROD, TEXT_STATS):
            with self.subTest(file=name):
                self.assertEqual(
                    self.validate({'fault_domain': 'production', 'file': name,
                                   'reason': 'r'}, offered)[1], name)
                with self.assertRaisesRegex(ValueError, 'contradictory'):
                    self.validate({'fault_domain': 'test', 'file': name,
                                   'reason': 'r'}, offered)
        self.assertEqual(
            self.validate({'fault_domain': 'test', 'file': TEXT_STATS_TEST,
                           'reason': 'r'}, offered)[1], TEXT_STATS_TEST)
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            self.validate({'fault_domain': 'production', 'file': TEXT_STATS_TEST,
                           'reason': 'r'}, offered)

    def test_one_production_two_test_selection(self):
        offered = [TEXT_STATS, TEST, TEXT_STATS_TEST]
        for name in (TEST, TEXT_STATS_TEST):
            with self.subTest(file=name):
                self.assertEqual(
                    self.validate({'fault_domain': 'test', 'file': name,
                                   'reason': 'r'}, offered)[1], name)
                with self.assertRaisesRegex(ValueError, 'contradictory'):
                    self.validate({'fault_domain': 'production', 'file': name,
                                   'reason': 'r'}, offered)
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            self.validate({'fault_domain': 'test', 'file': TEXT_STATS,
                           'reason': 'r'}, offered)

    def test_file_is_required_even_when_the_domain_has_one_file(self):
        for data in ({'fault_domain': 'test', 'reason': 'r'},
                     {'fault_domain': 'test', 'file': '', 'reason': 'r'}):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, 'unapproved file'):
                    self.validate(data, [TEXT_STATS, TEXT_STATS_TEST])

    def test_empty_reason_is_still_rejected(self):
        for reason in (None, '', '   ', 5):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, 'requires a reason'):
                    self.validate({'fault_domain': 'test', 'file': TEXT_STATS_TEST,
                                   'reason': reason}, [TEXT_STATS, TEXT_STATS_TEST])

    def test_reason_is_evidence_only_and_never_parsed(self):
        # Honest limit: a reply whose typed fields agree is accepted even when
        # its prose contradicts them. Nothing keyword-matches the reason.
        self.assertEqual(
            self.validate({'fault_domain': 'production', 'file': TEXT_STATS,
                           'reason': 'The test expectation is incorrect.'},
                          [TEXT_STATS, TEXT_STATS_TEST])[0],
            'production')
        # ...and prose agreeing with the file does not rescue a contradiction.
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            self.validate({'fault_domain': 'test', 'file': TEXT_STATS,
                           'reason': 'The implementation is wrong.'},
                          [TEXT_STATS, TEXT_STATS_TEST])


# ---------------------------------------------------------------------------
# Routing inside run_job, on the Workflow 32 shape
# ---------------------------------------------------------------------------


class Workflow32Harness(evidence.FlexibleHarness):
    task = W32_TASK

    def setUp(self):
        super().setUp()
        self.watch = [TEXT_STATS, TEXT_STATS_TEST]
        self.configure(editable_tests=[TEXT_STATS_TEST])

    def first_round(self):
        return [
            evidence.selection([TEXT_STATS, TEXT_STATS_TEST]),
            evidence.plan_for([TEXT_STATS, TEXT_STATS_TEST],
                              'Add a method to count punctuation marks in the text.'),
            W32_PROD,
            W32_TEST,
        ]

    def repair_dir(self, job_id, attempt=4):
        return self.workdir(job_id) / f'attempt-{attempt}'

    def assert_rejected_before_repair(self, job_id, result, prompts, reply,
                                      message, attempt=4, prompt_count=5,
                                      verifications=1):
        self.assertEqual(result['status'], 'failed')
        self.assertIn(message, result['error'])
        # Selection, plan, two edits, one routing call - and nothing after it:
        # no repair-edit prompt, no repair inference, no reselection.
        self.assertEqual(len(prompts), prompt_count)
        self.assertIn('fault_domain', prompts[-1])
        # No verification ran for the rejected repair.
        self.assertEqual(len(self.observed), verifications)

        repair = self.repair_dir(job_id, attempt)
        for absent in ('repair-prompt.txt', 'repair-answer.txt',
                       'repair-selection.json', 'candidate-verification',
                       'result.json'):
            self.assertFalse((repair / absent).exists(), absent)

        # Evidence preserves the rejected routing reply exactly.
        self.assertEqual((repair / 'selection-answer.txt').read_text(encoding='utf-8'),
                         reply)
        routing = json.loads((repair / 'repair-routing.json').read_text(encoding='utf-8'))
        self.assertIs(routing['accepted'], False)
        self.assertEqual(routing['response'], json.loads(reply))
        self.assertIn(message, routing['error'])

        # No file was written and nothing was committed.
        checkout = self.workdir(job_id) / 'repo'
        self.assertEqual((checkout / TEXT_STATS).read_text(encoding='utf-8'), W32_PROD)
        self.assertEqual((checkout / TEXT_STATS_TEST).read_text(encoding='utf-8'), W32_TEST)
        self.assertEqual(git(checkout, 'rev-parse', 'HEAD'), self.spec['base_commit'])
        self.assertFalse((self.workdir(job_id) / 'repository.json').exists())
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.spec['base_commit'])

        error_row = next(a for a in result['attempts'] if a['number'] == attempt)
        self.assertEqual(error_row['phase'], 'error')


class Workflow32RegressionTests(Workflow32Harness):
    def test_workflow_32_contradiction_fails_closed_before_any_repair(self):
        # A valid routing reply and a repair are queued after the
        # contradiction. Neither may be consumed: there is no second routing
        # call and no repair edit.
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + [
                W32_CONTRADICTION,
                route('test', TEXT_STATS_TEST), W32_TEST_FIXED,
            ],
            verify_results=[W32_FAILURE, PASSED_13, BASELINE_12,
                            workflow.HYBRID_DISTINGUISHING],
        )
        self.assert_rejected_before_repair(
            job_id, result, prompts, W32_CONTRADICTION,
            "routing is contradictory: fault_domain 'test' but "
            + TEXT_STATS + " is not a selected test file")
        # The routing prompt carried the real Workflow 32 diagnostic.
        self.assertIn('expected: <4> but was: <31>', prompts[4])

    def test_mirror_contradiction_fails_closed_before_any_repair(self):
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + [
                W32_MIRROR,
                route('production', TEXT_STATS), W32_PROD_BROKEN,
            ],
            verify_results=[W32_FAILURE, PASSED_13],
        )
        self.assert_rejected_before_repair(
            job_id, result, prompts, W32_MIRROR,
            "routing is contradictory: fault_domain 'production' but "
            + TEXT_STATS_TEST + " is not a selected production file")

    def test_consistent_test_routing_repairs_the_test_and_succeeds(self):
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + [
                route('test', TEXT_STATS_TEST, 'Expected count is wrong.'),
                W32_TEST_FIXED,
            ],
            verify_results=[W32_FAILURE, PASSED_13, BASELINE_12,
                            workflow.HYBRID_DISTINGUISHING],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertEqual(len(prompts), 6)
        # The repair edit targeted the test file.
        self.assertIn('TARGET:', prompts[5])
        self.assertIn('class TextStatsTest', prompts[5])

        repair = self.repair_dir(job_id)
        selection = json.loads((repair / 'repair-selection.json').read_text(encoding='utf-8'))
        self.assertEqual(selection, {
            'repair_number': 1, 'fault_domain': 'test',
            'file': TEXT_STATS_TEST, 'reason': 'Expected count is wrong.',
        })
        record = json.loads((repair / 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(record['fault_domain'], 'test')
        self.assertEqual(record['repair_target'], TEXT_STATS_TEST)
        self.assertEqual(record['repair_reason'], 'Expected count is wrong.')
        routing = json.loads((repair / 'repair-routing.json').read_text(encoding='utf-8'))
        self.assertIs(routing['accepted'], True)
        self.assertIsNone(routing['error'])
        self.assertEqual(routing['offered'], {
            'production': [TEXT_STATS], 'test': [TEXT_STATS_TEST]})

        checkout = self.workdir(job_id) / 'repo'
        self.assertEqual(
            git(checkout, 'show', 'HEAD:' + TEXT_STATS_TEST, raw=True), W32_TEST_FIXED)
        self.assertEqual(git(checkout, 'show', 'HEAD:' + TEXT_STATS, raw=True), W32_PROD)

    def test_consistent_production_routing_repairs_production(self):
        job_id, result, prompts = self.run_case(
            answers=[
                *self.first_round()[:2], W32_PROD_BROKEN, W32_TEST_FIXED,
                route('production', TEXT_STATS, 'Double counting.'),
                W32_PROD,
            ],
            verify_results=[W32_FAILURE, PASSED_13, BASELINE_12,
                            workflow.HYBRID_DISTINGUISHING],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        selection = json.loads(
            (self.repair_dir(job_id) / 'repair-selection.json').read_text(encoding='utf-8'))
        self.assertEqual(selection['fault_domain'], 'production')
        self.assertEqual(selection['file'], TEXT_STATS)
        self.assertIn('class TextStats {', prompts[5])

    def test_malformed_routing_replies_fail_closed_in_the_workflow(self):
        for name, data in MALFORMED_REPLIES.items():
            with self.subTest(reply=name):
                self.setUp()
                reply = json.dumps(data)
                job_id, result, prompts = self.run_case(
                    answers=self.first_round() + [reply, W32_TEST_FIXED],
                    verify_results=[W32_FAILURE, PASSED_13],
                )
                self.assert_rejected_before_repair(
                    job_id, result, prompts, reply, 'fault_domain')

    def test_unapproved_file_still_fails_with_a_valid_domain(self):
        reply = route('production', PROD)  # approved, but not selected
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + [reply, workflow.GOOD_PROD],
            verify_results=[W32_FAILURE, PASSED_13],
        )
        self.assert_rejected_before_repair(
            job_id, result, prompts, reply, 'unapproved file')

    def test_unparseable_routing_reply_is_preserved_and_fails(self):
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + ['The test is wrong.'],
            verify_results=[W32_FAILURE],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(prompts), 5)
        routing = json.loads((self.repair_dir(job_id) / 'repair-routing.json')
                             .read_text(encoding='utf-8'))
        self.assertIs(routing['accepted'], False)
        self.assertIsNone(routing['response'])
        self.assertTrue(routing['error'])

    def test_insufficient_test_count_requires_test_domain_in_the_workflow(self):
        short = dict(workflow.SHORT_COUNT)
        reply = route('production', TEXT_STATS_TEST)
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + [reply, W32_TEST_FIXED],
            verify_results=[short, PASSED_13],
        )
        self.assert_rejected_before_repair(
            job_id, result, prompts, reply, "requires fault_domain 'test'")
        # The narrowed candidate set is intact: production is not offered.
        self.assertEqual(prompts[4].split('Production files: ')[1].splitlines()[0], '[]')
        self.assertIn(TEXT_STATS_TEST, prompts[4].split('Test files: ')[1].splitlines()[0])
        routing = json.loads((self.repair_dir(job_id) / 'repair-routing.json')
                             .read_text(encoding='utf-8'))
        self.assertEqual(routing['required_domain'], 'test')
        self.assertEqual(routing['offered'], {'production': [], 'test': [TEXT_STATS_TEST]})

    def test_insufficient_test_count_with_test_domain_proceeds(self):
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + [
                route('test', TEXT_STATS_TEST, 'Restore the dropped cases.'),
                W32_TEST_FIXED,
            ],
            verify_results=[dict(workflow.SHORT_COUNT), PASSED_13, BASELINE_12,
                            workflow.HYBRID_DISTINGUISHING],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        selection = json.loads((self.repair_dir(job_id) / 'repair-selection.json')
                               .read_text(encoding='utf-8'))
        self.assertEqual(selection['fault_domain'], 'test')

    def test_exactly_two_code_repairs_remain_the_maximum(self):
        still_failing = dict(W32_FAILURE)
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + [
                route('test', TEXT_STATS_TEST), W32_TEST.replace('(4,', '(5,'),
                route('test', TEXT_STATS_TEST), W32_TEST.replace('(4,', '(6,'),
                # Queued but must never be consumed: no third routing call or
                # third repair edit exists.
                route('test', TEXT_STATS_TEST), W32_TEST_FIXED,
            ],
            verify_results=[W32_FAILURE, still_failing, still_failing,
                            PASSED_13, BASELINE_12, workflow.HYBRID_DISTINGUISHING],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('two bounded repairs', result['error'])
        # Selection, plan, two edits, and exactly two rounds of routing + edit.
        self.assertEqual(len(prompts), 8)
        self.assertEqual(len(self.observed), 3)
        self.assertEqual(highest_attempt_number(self.workdir(job_id)), 5)
        for attempt, number in ((4, 1), (5, 2)):
            record = json.loads((self.repair_dir(job_id, attempt) / 'result.json')
                                .read_text(encoding='utf-8'))
            self.assertEqual(record['repair_number'], number)
            self.assertEqual(record['fault_domain'], 'test')

    def test_routing_failure_on_the_second_repair_does_not_reselect(self):
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + [
                route('test', TEXT_STATS_TEST), W32_TEST.replace('(4,', '(5,'),
                W32_CONTRADICTION,
                route('test', TEXT_STATS_TEST), W32_TEST_FIXED,
            ],
            verify_results=[W32_FAILURE, dict(W32_FAILURE), PASSED_13,
                            BASELINE_12, workflow.HYBRID_DISTINGUISHING],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Repair 2 routing is contradictory', result['error'])
        self.assertEqual(len(prompts), 7)
        self.assertEqual(len(self.observed), 2)
        self.assertFalse((self.repair_dir(job_id, 5) / 'repair-prompt.txt').exists())
        routing = json.loads((self.repair_dir(job_id, 5) / 'repair-routing.json')
                             .read_text(encoding='utf-8'))
        self.assertEqual(routing['repair_number'], 2)
        self.assertEqual(routing['response'], json.loads(W32_CONTRADICTION))

    def test_repair_history_in_the_failure_record_carries_fault_domain(self):
        # Repair 1 is routed and applied; its re-verification then fails in a
        # way that is not repairable, so the candidate failure record lists
        # the repair history.
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + [
                route('test', TEXT_STATS_TEST, 'Expected count is wrong.'),
                W32_TEST.replace('(4,', '(5,'),
            ],
            verify_results=[W32_FAILURE, {'passed': False, 'repairable': False}],
        )
        self.assertEqual(result['status'], 'failed')
        record = json.loads((self.workdir(job_id) / 'attempt-3' / 'result.json')
                            .read_text(encoding='utf-8'))
        self.assertEqual(record['repairs'], [{
            'repair_number': 1,
            'fault_domain': 'test',
            'repair_target': TEXT_STATS_TEST,
            'repair_reason': 'Expected count is wrong.',
            'passed': False,
        }])

    def test_unchanged_source_after_consistent_routing_stays_terminal(self):
        # Workflow 32's later failure point is unchanged: a consistent route
        # whose repair returns the same source ends the workflow, with no
        # reselection and no further repair.
        job_id, result, prompts = self.run_case(
            answers=self.first_round() + [
                route('production', TEXT_STATS), W32_PROD,
                route('test', TEXT_STATS_TEST), W32_TEST_FIXED,
            ],
            verify_results=[W32_FAILURE, PASSED_13],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Repair 1 returned unchanged source', result['error'])
        self.assertEqual(len(prompts), 6)
        self.assertEqual(len(self.observed), 1)
        selection = json.loads((self.repair_dir(job_id) / 'repair-selection.json')
                               .read_text(encoding='utf-8'))
        self.assertEqual(selection['fault_domain'], 'production')


class MultiFileRoutingTests(evidence.FlexibleHarness):
    """2 production + 1 test, and 1 production + 2 tests, inside run_job."""

    def setUp(self):
        super().setUp()
        self.watch = [PROD, TEST, TEXT_STATS, TEXT_STATS_TEST]
        self.configure(editable_tests=[TEST, TEXT_STATS_TEST])

    def run_shape(self, selected, edits, reply, follow=()):
        return self.run_case(
            answers=[evidence.selection(selected), evidence.plan_for(selected)]
            + edits + [reply] + list(follow),
            verify_results=[W32_FAILURE, PASSED_13, BASELINE_12,
                            workflow.HYBRID_DISTINGUISHING],
        )

    def test_two_production_one_test(self):
        selected = [PROD, TEXT_STATS, TEXT_STATS_TEST]
        edits = [workflow.GOOD_PROD, W32_PROD, W32_TEST]
        # A test-domain reply naming either production file is contradictory.
        for name in (PROD, TEXT_STATS):
            with self.subTest(file=name):
                self.setUp()
                job_id, result, prompts = self.run_shape(
                    selected, edits, route('test', name))
                self.assertEqual(result['status'], 'failed')
                self.assertIn('contradictory', result['error'])
                self.assertEqual(len(prompts), 6)
                self.assertEqual(
                    json.loads(prompts[5].split('Production files: ')[1].splitlines()[0]),
                    [PROD, TEXT_STATS])
                self.assertEqual(
                    json.loads(prompts[5].split('Test files: ')[1].splitlines()[0]),
                    [TEXT_STATS_TEST])
        # A consistent production reply proceeds to the repair and succeeds.
        self.setUp()
        job_id, result, prompts = self.run_shape(
            selected, [workflow.GOOD_PROD, W32_PROD_BROKEN, W32_TEST_FIXED],
            route('production', TEXT_STATS), [W32_PROD])
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertEqual(len(prompts), 7)
        # A consistent test reply passes routing. Its repair-edit prompt must
        # then carry both production files as reference context, which in
        # this shape exceeds the unchanged 2000-byte limit: it fails closed
        # there, as the Patient Zero compatibility tests already document.
        # Routing grants nothing that bypasses that limit.
        self.setUp()
        job_id, result, prompts = self.run_shape(
            selected, edits, route('test', TEXT_STATS_TEST), [W32_TEST_FIXED])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('nothing truncated', result['error'])
        self.assertEqual(len(prompts), 6)
        repair = self.workdir(job_id) / 'attempt-4'
        self.assertEqual(
            json.loads((repair / 'repair-selection.json').read_text(encoding='utf-8'))
            ['fault_domain'], 'test')
        self.assertFalse((repair / 'repair-prompt.txt').exists())

    def test_one_production_two_tests(self):
        selected = [TEXT_STATS, TEST, TEXT_STATS_TEST]
        edits = [W32_PROD, workflow.GOOD_TEST, W32_TEST]
        for name in (TEST, TEXT_STATS_TEST):
            with self.subTest(file=name):
                self.setUp()
                job_id, result, prompts = self.run_shape(
                    selected, edits, route('production', name))
                self.assertEqual(result['status'], 'failed')
                self.assertIn('contradictory', result['error'])
                self.assertEqual(len(prompts), 6)
                self.assertEqual(
                    json.loads(prompts[5].split('Test files: ')[1].splitlines()[0]),
                    [TEST, TEXT_STATS_TEST])
        self.setUp()
        job_id, result, prompts = self.run_shape(
            selected, edits, route('test', TEXT_STATS_TEST), [W32_TEST_FIXED])
        self.assertEqual(result['status'], 'succeeded', result.get('error'))


class ReplanRoutingTests(evidence.DeltaHarness):
    """Typed routing applies identically to the re-planned candidate's repairs."""

    first_candidate = evidence.SemanticReplanTests.first_candidate
    REPAIRABLE = {'passed': False, 'repairable': True,
                  'junit': {'tests': 13, 'failures': 1,
                            'diagnostics': 'expected x but was y'}}

    def replanned(self, reply, follow=()):
        self.pin_base_production(workflow.WORKFLOW_26_BASE_PROD)
        return self.run_case(
            answers=self.first_candidate() + [
                workflow.SEMANTIC_DIAGNOSIS, workflow.REPLAN_PLAN,
                workflow.GOOD_PROD, self.added_case_test(self.HYPHEN_CASE),
                reply,
            ] + list(follow),
            verify_results=evidence.NO_DELTA_ROUND + [
                self.REPAIRABLE,
                {'passed': True, 'junit': {'tests': 14, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                workflow.HYBRID_DISTINGUISHING,
            ],
        )

    def test_contradiction_after_a_semantic_replan_fails_closed(self):
        reply = route('test', PROD, 'The test is wrong.')
        job_id, result, prompts = self.replanned(
            reply, [workflow.REPAIR_PROD_FILE, workflow.GOOD_PROD_REPAIRED])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Repair 1 routing is contradictory', result['error'])
        # First candidate (4), diagnosis + re-plan + two edits (4), routing (1).
        self.assertEqual(len(prompts), 9)
        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        self.assertEqual(highest_attempt_number(workdir), 9)
        repair = workdir / 'attempt-9'
        self.assertFalse((repair / 'repair-prompt.txt').exists())
        routing = json.loads((repair / 'repair-routing.json').read_text(encoding='utf-8'))
        self.assertEqual(routing['response'], json.loads(reply))
        self.assertEqual(SEMANTIC_REPLAN_BUDGET, 1)

    def test_consistent_routing_after_a_semantic_replan_repairs(self):
        job_id, result, prompts = self.replanned(
            workflow.REPAIR_PROD_FILE, [workflow.GOOD_PROD_REPAIRED])
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        repair = self.root / 'jobs' / f'workflow-{job_id}' / 'attempt-9'
        selection = json.loads((repair / 'repair-selection.json').read_text(encoding='utf-8'))
        self.assertEqual(selection['fault_domain'], 'production')
        self.assertEqual(
            json.loads((repair / 'result.json').read_text(encoding='utf-8'))['fault_domain'],
            'production')


# ---------------------------------------------------------------------------
# Prompt budget and advisory wording
# ---------------------------------------------------------------------------


W32_DIAGNOSTIC = verification_diagnostic(W32_FAILURE, 12)


class RepairSelectionPromptTests(unittest.TestCase):
    PRODUCTION = patient_zero.APPROVED_PRODUCTION
    TESTS = patient_zero.APPROVED_TESTS

    def build(self, candidates, task=W32_TASK, diagnostic=W32_DIAGNOSTIC,
              summary='Add a method to count punctuation marks in the text.'):
        return repair_selection_prompt(
            task, {'summary': summary}, list(candidates), diagnostic,
            self.PRODUCTION, self.TESTS)

    def test_patient_zero_one_plus_one_shapes_fit(self):
        shapes = list(itertools.product(self.PRODUCTION, self.TESTS))
        self.assertEqual(len(shapes), 15)
        for shape in shapes:
            with self.subTest(shape=shape):
                self.assertLessEqual(len(self.build(shape).encode()), 2000)
                # The same with the Patient Zero compatibility task/diagnostic.
                self.assertLessEqual(
                    len(self.build(shape, patient_zero.TASK,
                                   patient_zero.DIAGNOSTIC).encode()), 2000)

    def test_patient_zero_three_file_shapes_fit(self):
        shapes = [
            (p,) + pair for p in self.PRODUCTION
            for pair in itertools.combinations(self.TESTS, 2)
        ] + [
            pair + (t,) for pair in itertools.combinations(self.PRODUCTION, 2)
            for t in self.TESTS
        ]
        for shape in shapes:
            with self.subTest(shape=shape):
                self.assertLessEqual(len(self.build(shape).encode()), 2000)

    def test_worst_case_inputs_fail_closed_rather_than_truncate(self):
        # 500-byte task (the prepare_spec ceiling), 700-byte diagnostic (the
        # verification_diagnostic cap), a 220-character plan summary, and the
        # three longest approved paths.
        longest = sorted(self.PRODUCTION + self.TESTS, key=len)[-3:]
        with self.assertRaisesRegex(ValueError, 'exceeds controller limit'):
            self.build(longest, 'T' * 500, 'D' * 700, 'S' * 220)

    def test_prompt_asks_for_the_typed_schema(self):
        prompt = self.build([TEXT_STATS, TEXT_STATS_TEST])
        self.assertIn('"fault_domain":"production|test"', prompt)
        self.assertIn('"file":"listed/path.java"', prompt)
        self.assertIn('"reason":', prompt)
        self.assertIn('Production files: ["' + TEXT_STATS + '"]', prompt)
        self.assertIn('Test files: ["' + TEXT_STATS_TEST + '"]', prompt)
        self.assertIn('a JUnit expected value may itself be wrong', prompt)
        self.assertIn('strict > N', prompt)

    def test_prompt_is_advisory_the_validator_rejects(self):
        """The prompt tells the model to list the file under its domain. That
        request is not the control: the same contradiction is rejected by the
        validator whatever the prompt said."""
        prompt = self.build([TEXT_STATS, TEXT_STATS_TEST])
        self.assertIn('file must be listed under that domain', prompt)
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            validate_repair_selection(
                json.loads(W32_CONTRADICTION), [TEXT_STATS, TEXT_STATS_TEST],
                self.PRODUCTION, self.TESTS)


if __name__ == '__main__':
    unittest.main()
