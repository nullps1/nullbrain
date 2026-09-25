import hashlib
import json
import pathlib
import unittest
import uuid

from nullcode.core.java_workflow import Store
from nullcode.fixtures.create_gradle_fixture import create
from nullcode.repo.repo_execute_workflow import (
    DISTINGUISHING_API_COMPILE_FAILURE,
    DISTINGUISHING_TEST_FAILURE,
    INFRASTRUCTURE_FAILURE,
    NO_BEHAVIORAL_DELTA,
    REJECTED_NO_BEHAVIORAL_DELTA,
    candidate_repairable,
    classify_behavioral_delta,
    distinguishing_compile_failure,
    edit_prompt,
    executed_test_count,
    hybrid_overlay_files,
    insufficient_test_count,
    javac_errors,
    prepare_spec,
    run_job,
    verification_diagnostic,
)
from nullcode.repo.repo_workflow import git

TASK = 'Implement Slugs.slugify; keep TextStats passing.'

PROD_TARGET = 'src/main/java/lab/Slugs.java'
TEST_TARGET = 'src/test/java/lab/SlugsTest.java'

GOOD_PROD = '''package lab;
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
}
'''

# A second, still-correct implementation, used as the "repaired" candidate so
# the identical-source check has something genuinely different to compare.
GOOD_PROD_REPAIRED = '''package lab;
import java.util.Locale;

public class Slugs {
    public static String slugify(String text) {
        if (text == null) {
            throw new IllegalArgumentException("Input text cannot be null");
        }
        String result = text.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]+", "-");
        while (result.startsWith("-")) result = result.substring(1);
        while (result.endsWith("-")) result = result.substring(0, result.length() - 1);
        return result;
    }
}
'''

GOOD_TEST = '''package lab;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class SlugsTest {
    @Test void basic() { assertEquals("hello-world", Slugs.slugify("Hello World")); }
    @Test void spaces() { assertEquals("hello-world", Slugs.slugify("  Hello   World  ")); }
    @Test void punctuation() { assertEquals("hello-world", Slugs.slugify("Hello, World!")); }
    @Test void repeatedSeparators() { assertEquals("a-b", Slugs.slugify("a---___b")); }
    @Test void digits() { assertEquals("java-21", Slugs.slugify("Java 21")); }
    @Test void empty() { assertEquals("", Slugs.slugify("")); }
    @Test void punctuationOnly() { assertEquals("", Slugs.slugify(" !!! ")); }
    @Test void nullInput() { assertThrows(IllegalArgumentException.class, () -> Slugs.slugify(null)); }
    @Test void alreadySlug() { assertEquals("already-a-slug", Slugs.slugify("already-a-slug")); }
}
'''

SELECTION = (
    '{"files": ["' + PROD_TARGET + '", "' + TEST_TARGET + '"], '
    '"reason": "Production implementation and its test"}'
)

PLAN = (
    '{"summary": "Implement slugify.", '
    '"files": [{"path": "' + PROD_TARGET + '", "reason": "Needs implementation"}, '
    '{"path": "' + TEST_TARGET + '", "reason": "Add one more edge case"}], '
    '"steps": ["Lowercase with Locale.ROOT", "Collapse separators", "Trim hyphens"], '
    '"risks": []}'
)


# Workflow 25's actual shape: the model returned a single new test case in
# place of the complete baseline suite. Compiles, runs green, executes far
# fewer cases than minimum_tests.
REDUCED_TEST = '''package lab;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class SlugsTest {
    @Test void leadingTrailingHyphens() { assertEquals("hello-world", Slugs.slugify("-Hello-World-")); }
}
'''

# Two further still-too-small suites, used to exhaust the repair budget.
# Each must differ from the last: repairs returning identical source are
# rejected before re-verification.
REDUCED_TEST_TWO = '''package lab;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class SlugsTest {
    @Test void leadingTrailingHyphens() { assertEquals("hello-world", Slugs.slugify("-Hello-World-")); }
    @Test void basic() { assertEquals("hello-world", Slugs.slugify("Hello World")); }
}
'''

REDUCED_TEST_THREE = '''package lab;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class SlugsTest {
    @Test void leadingTrailingHyphens() { assertEquals("hello-world", Slugs.slugify("-Hello-World-")); }
    @Test void basic() { assertEquals("hello-world", Slugs.slugify("Hello World")); }
    @Test void digits() { assertEquals("java-21", Slugs.slugify("Java 21")); }
}
'''

# Gradle succeeded, JUnit reported no failures, but only 5 cases ran against
# a floor of 12. gradle_workflow.verify() reports this as passed=False with
# its shared repairable flag clear.
SHORT_COUNT = {
    'passed': False,
    'repairable': False,
    'junit': {'tests': 5, 'failures': 0, 'skipped': 0, 'diagnostics': ''},
}

# ------------------------------------------------------------------
# Hybrid counterfactual evidence: pinned-base production + candidate tests.
# ------------------------------------------------------------------

# The distinguishing signal - the candidate suite fails against the base, so
# it genuinely exercises behavior the base does not satisfy.
HYBRID_DISTINGUISHING = {
    'passed': False,
    'repairable': True,
    'tests': {'exit_code': 1},
    'junit': {
        'tests': 13, 'failures': 1, 'skipped': 0,
        'diagnostics': 'leadingTrailingHyphens: expected: <hello-world> but was: <--hello-world-->',
    },
}

# Workflow 26's shape - the candidate suite ALSO passes against the base, so
# nothing proves the production edit was needed.
HYBRID_NO_DELTA = {
    'passed': True,
    'junit': {'tests': 13, 'failures': 0, 'skipped': 0},
}

# A candidate test calling a method the pinned base does not declare. This is
# source incompatibility, not a broken build environment.
HYBRID_API_COMPILE_LOG = (
    '> Task :compileJava\n'
    '> Task :compileTestJava FAILED\n'
    '/work/project/src/test/java/lab/SlugsTest.java:12: error: cannot find symbol\n'
    '    @Test void unicode() { assertEquals("aeo", Slugs.slugifyUnicode("aeo")); }\n'
    '                                                    ^\n'
    '  symbol:   method slugifyUnicode(String)\n'
    '  location: class lab.Slugs\n'
    '1 error\n'
    'FAILURE: Build failed with an exception.\n'
)

HYBRID_API_COMPILE_FAILURE = {
    'passed': False,
    'compile': {'exit_code': 1, 'timed_out': False, 'log': HYBRID_API_COMPILE_LOG},
}

# A semantic re-plan is NOT a repair: it answers a candidate that verified
# cleanly but demonstrated no behavioral delta. The diagnosis reply names a
# different behavior to aim at; the plan that follows is validated against the
# same selected files as the first one.
SEMANTIC_DIAGNOSIS = (
    '{"diagnosis": "The pinned base already trims leading and trailing '
    'hyphens, so the added case proves nothing.", '
    '"behavior": "Collapse runs of separators inside the slug as well."}'
)

REPLAN_PLAN = (
    '{"summary": "Collapse internal separator runs.", '
    '"files": [{"path": "' + PROD_TARGET + '", "reason": "Collapse separators"}, '
    '{"path": "' + TEST_TARGET + '", "reason": "Cover the collapsed runs"}], '
    '"steps": ["Collapse separator runs", "Keep the trimming behavior"], '
    '"risks": []}'
)

# Milestone 7B.2: repair routing replies are typed. Each canned reply names the
# fault domain that agrees with its file, so the tests below keep protecting
# what they protected before; the routing rules themselves are covered in
# test_repair_routing.py.
REPAIR_TEST_FILE = (
    '{"fault_domain": "test", "file": "' + TEST_TARGET + '", '
    '"reason": "Baseline tests were dropped"}'
)
REPAIR_PROD_FILE = (
    '{"fault_domain": "production", "file": "' + PROD_TARGET + '", '
    '"reason": "Blame the implementation"}'
)


def add_editable_test_files(repo, test_files):
    """Extend the Milestone-6 fixture's config for the 7B profile, which needs
    editable_test_files declared in addition to editable_files."""
    config_path = repo / '.nullcode.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    config['editable_test_files'] = test_files
    config_path.write_text(json.dumps(config, indent=2), encoding='utf-8')
    git(repo, 'add', '.nullcode.json')
    git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
        'commit', '-m', 'Declare editable test files for repo-execute-v1')


class ExecuteWorkflowHarness(unittest.TestCase):
    """Shared fixture and canned-model/canned-verifier driver."""

    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.repo = create(self.root / 'source')
        add_editable_test_files(self.repo, [TEST_TARGET])
        self.store = Store(self.root / 'db')
        self.spec = prepare_spec(self.repo, 'main', TASK)
        self.observed_test_sources = []
        self.observed_production_sources = []

    def run_case(self, answers, verify_results):
        job_id = self.store.submit(repo_spec=self.spec)
        self.observed_test_sources = []
        self.observed_production_sources = []
        remaining_answers = list(answers)
        pending_verify = iter(verify_results)
        prompts = []

        def generate(prompt, record):
            prompts.append(prompt)
            self.assertLessEqual(len(prompt.encode()), 2000)
            record(7)
            return remaining_answers.pop(0)

        def verify(checkout, paths, folder, minimum, phase):
            self.assertEqual(minimum, 12)
            phase('compiling')
            phase('testing')
            # Record the test source each verification round actually saw, so
            # Stage 5's independence from the repaired content is assertable.
            self.observed_test_sources.append(
                (checkout / TEST_TARGET).read_text(encoding='utf-8')
            )
            # Same for production: Stage 6's hybrid must see BASE production,
            # never the candidate's.
            self.observed_production_sources.append(
                (checkout / PROD_TARGET).read_text(encoding='utf-8')
            )
            result = dict(next(pending_verify))
            result.setdefault('repairable', False)
            # setdefault, not update: a case may inject its own infrastructure
            # evidence (timeout, failed cleanup, compile failure).
            result.setdefault('compile', {'exit_code': 0})
            result.setdefault('tests', {'exit_code': 0})
            result.setdefault('cleanup', {'exit_code': 0})
            result['snapshot_sha256'] = {
                p: hashlib.sha256((checkout / p).read_bytes()).hexdigest()
                for p in paths
            }
            # Simulate a verifier whose snapshot shows candidate production
            # inside the hybrid counterfactual state.
            if result.pop('leak_candidate_production', False):
                result['snapshot_sha256'][PROD_TARGET] = hashlib.sha256(
                    b'candidate production leaked into the hybrid'
                ).hexdigest()
            return result

        run_job(self.store, self.store.claim(), generate, verify, self.root / 'jobs')
        return job_id, self.store.show(job_id), prompts


class RepoExecuteWorkflowTests(ExecuteWorkflowHarness):
    def test_prepare_spec_requires_editable_test_files_configured(self):
        bare = create(self.root / 'bare-source')
        with self.assertRaisesRegex(ValueError, 'editable_test_files'):
            prepare_spec(bare, 'main', TASK)

    def test_success_edits_both_files_verifies_and_commits(self):
        job_id, result, prompts = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD, GOOD_TEST],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},  # candidate: 13
                {'passed': True, 'junit': {'tests': 12}},  # baseline: original 12
                HYBRID_DISTINGUISHING,  # hybrid: candidate tests fail on base
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertEqual(len(prompts), 4)

        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        repository = json.loads((workdir / 'repository.json').read_text(encoding='utf-8'))
        checkout = pathlib.Path(repository['checkout'])

        changed = set(git(checkout, 'diff', '--name-only',
                           self.spec['base_commit'], 'HEAD').splitlines())
        self.assertEqual(changed, {PROD_TARGET, TEST_TARGET})

        # Committing must not have touched the source repository itself.
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.spec['base_commit'])

    def test_selection_requires_both_a_production_and_a_test_file(self):
        prod_only = '{"files": ["' + PROD_TARGET + '"], "reason": "n/a"}'
        _, result, prompts = self.run_case(
            answers=[prod_only, PLAN, GOOD_PROD, GOOD_TEST],
            verify_results=[{'passed': True}, {'passed': True}],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('2 or 3', result['error'])
        self.assertEqual(len(prompts), 1)

    def test_unchanged_test_edit_is_rejected_as_a_non_edit(self):
        # The model "edits" the test file but returns it byte-for-byte
        # identical to what's already committed - the scope check requires
        # every selected file to show a real diff.
        original_test = git(self.repo, 'show', 'main:' + TEST_TARGET, raw=True)
        _, result, prompts = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD, original_test],
            verify_results=[{'passed': True}, {'passed': True}],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('real edit', result['error'])

    def test_repairable_failure_is_fixed_within_the_two_attempt_budget(self):
        job_id, result, prompts = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, GOOD_TEST,
                '{"fault_domain": "production", "file": "' + PROD_TARGET + '", "reason": "Production logic is wrong"}',
                GOOD_PROD_REPAIRED,
            ],
            verify_results=[
                {'passed': False, 'repairable': True,
                 'junit': {'diagnostics': 'expected hello-world but was --hello-world--'}},
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertEqual(len(prompts), 6)

    def test_repair_selecting_a_file_outside_the_selection_is_rejected(self):
        _, result, prompts = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, GOOD_TEST,
                '{"fault_domain": "production", "file": "src/main/java/lab/TextStats.java", "reason": "unrelated file"}',
            ],
            verify_results=[
                {'passed': False, 'repairable': True,
                 'junit': {'diagnostics': 'expected hello-world but was HELLO-WORLD'}},
            ],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('unapproved file', result['error'])

    def test_repair_returning_identical_source_is_rejected_without_reverification(self):
        _, result, prompts = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, GOOD_TEST,
                '{"fault_domain": "production", "file": "' + PROD_TARGET + '", "reason": "try again"}',
                GOOD_PROD,  # identical to the candidate already in place
            ],
            verify_results=[
                {'passed': False, 'repairable': True,
                 'junit': {'diagnostics': 'expected hello-world but was HELLO-WORLD'}},
            ],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('unchanged source', result['error'])

    def test_non_repairable_failure_stops_without_a_repair_attempt(self):
        _, result, prompts = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD, GOOD_TEST],
            verify_results=[{'passed': False, 'repairable': False}],
        )
        self.assertEqual(result['status'], 'failed')
        # Selection, planning, and one edit call per file - no repair call.
        self.assertEqual(len(prompts), 4)
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_baseline_regression_failure_blocks_the_commit(self):
        # The edited test now passes against the edited production code, but
        # the ORIGINAL test - restored during Stage 6 - would not. That must
        # block the commit even though the candidate verification passed.
        job_id, result, prompts = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD, GOOD_TEST],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},  # candidate: passes
                {'passed': False, 'repairable': False},  # baseline: fails
            ],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('breaks the original test suite', result['error'])

        # Nothing should have been committed.
        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        checkout = workdir / 'repo'
        self.assertEqual(
            git(checkout, 'rev-parse', 'HEAD'),
            self.spec['base_commit'],
        )

    def same_count_test_edit(self):
        """A test-file edit that changes content but adds zero new @Test
        cases relative to whatever is currently committed at 'main' for
        TEST_TARGET - the exact shape of the confirmed defect reproduction."""
        original = git(self.repo, 'show', 'main:' + TEST_TARGET, raw=True)
        edited = original.replace('"Hello World"', '"Hello World!"')
        assert edited != original
        assert edited.count('@Test') == original.count('@Test')
        return edited

    def add_extra_baseline_test(self):
        """Commit a 13th baseline test on top of the fixture WITHOUT raising
        minimum_tests (still 12) - reproducing the reported defect scenario
        where the real original suite has already grown past the configured
        floor."""
        path = self.repo / 'src/test/java/lab/TextStatsTest.java'
        content = path.read_text(encoding='utf-8')
        content = content.replace(
            '    @Test void nullInput()',
            '    @Test void anotherCase() { assertEquals(1, TextStats.countWords("x")); }\n'
            '    @Test void nullInput()',
        )
        path.write_text(content, encoding='utf-8')
        git(self.repo, 'add', '.')
        git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
            'commit', '-m', 'Add a 13th baseline test without raising minimum_tests')
        self.spec = prepare_spec(self.repo, 'main', TASK)

    def test_p2_reproduction_stale_minimum_no_longer_allows_a_no_op_test_edit(self):
        # Regression test for a confirmed defect: minimum_tests (12) can be
        # stale relative to the real original suite (13, after this commit).
        # A candidate that touches the test file without adding cases must
        # be rejected even though 13 > minimum_tests - comparison must be
        # against the actual baseline count, not the configured floor.
        self.add_extra_baseline_test()
        _, result, prompts = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD, self.same_count_test_edit()],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},  # candidate: 13
                {'passed': True, 'junit': {'tests': 13}},  # baseline: also 13 - no increase
            ],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('no new cases were added', result['error'])

    def test_repair_cannot_bypass_the_added_coverage_comparison(self):
        # A repaired candidate is held to the same real-baseline comparison
        # as an unrepaired one - a repair must not exempt it.
        self.add_extra_baseline_test()
        _, result, prompts = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, self.same_count_test_edit(),
                '{"fault_domain": "production", "file": "' + PROD_TARGET + '", "reason": "Production logic is wrong"}',
                GOOD_PROD_REPAIRED,
            ],
            verify_results=[
                {'passed': False, 'repairable': True,
                 'junit': {'diagnostics': 'expected hello-world but was --hello-world--'}},
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},  # repaired: 13
                {'passed': True, 'junit': {'tests': 13}},  # baseline: also 13 - no increase
            ],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('no new cases were added', result['error'])

    def test_missing_baseline_count_evidence_fails_closed(self):
        # If the baseline regression run doesn't report a usable case count,
        # the workflow must fail rather than silently fall back to comparing
        # against the configured minimum_tests floor.
        _, result, prompts = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD, GOOD_TEST],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},  # candidate: 13
                {'passed': True},  # baseline: no junit data at all
            ],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Missing or unusable', result['error'])


    # ------------------------------------------------------------------
    # Insufficient executed-test-count repair classification (Workflow 25)
    # ------------------------------------------------------------------

    def test_insufficient_test_count_enters_bounded_repair(self):
        # The defect: compile clean, Gradle test task exit 0, zero JUnit
        # failures, executed count below minimum_tests. gradle_workflow's
        # shared repairable flag stays False, so the workflow used to
        # terminate immediately instead of repairing the selected test file.
        job_id, result, prompts = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, REDUCED_TEST,
                REPAIR_TEST_FILE, GOOD_TEST,
            ],
            verify_results=[
                SHORT_COUNT,
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        # Selection, plan, two edits, repair-selection, repair-edit.
        self.assertEqual(len(prompts), 6)

        repair = self.root / 'jobs' / f'workflow-{job_id}' / 'attempt-4'
        self.assertTrue(repair.is_dir())
        selection = json.loads(
            (repair / 'repair-selection.json').read_text(encoding='utf-8')
        )
        self.assertEqual(selection['file'], TEST_TARGET)

    def test_insufficient_count_repair_is_offered_only_selected_test_files(self):
        # For this failure class the production file cannot restore deleted
        # coverage, so it must not be offered as a repair target - and a
        # model that names it anyway is rejected rather than obeyed.
        _, result, prompts = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, REDUCED_TEST,
                REPAIR_PROD_FILE,
            ],
            verify_results=[SHORT_COUNT],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('unapproved file', result['error'])

        repair_prompt = prompts[4]
        self.assertIn(TEST_TARGET, repair_prompt)
        self.assertNotIn(PROD_TARGET, repair_prompt)
        self.assertEqual(
            repair_prompt.split('Production files: ')[1].splitlines()[0],
            '[]',
        )

    def test_insufficient_count_repair_still_rejects_unselected_files(self):
        # Narrowing removes an already-approved choice. It must not create a
        # path to anything outside the original selection.
        _, result, _ = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, REDUCED_TEST,
                '{"fault_domain": "production", "file": "src/main/java/lab/TextStats.java", "reason": "no"}',
            ],
            verify_results=[SHORT_COUNT],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('unapproved file', result['error'])

    def test_general_junit_failure_repair_still_offers_every_selected_file(self):
        # The narrowing is specific to the insufficient-count reason. A
        # conventional failing JUnit run must still be repairable in the
        # production file.
        _, result, prompts = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, GOOD_TEST,
                REPAIR_PROD_FILE, GOOD_PROD_REPAIRED,
            ],
            verify_results=[
                {'passed': False, 'repairable': True,
                 'junit': {'tests': 13, 'failures': 1,
                           'diagnostics': 'expected hello-world but was --hello-world--'}},
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertIn(PROD_TARGET, prompts[4])

    def test_infrastructure_failures_are_never_reclassified_as_repairable(self):
        # Every one of these carries a below-minimum executed count, so each
        # would be misread as a repairable coverage shortfall if the new
        # classifier looked at counts alone.
        low = {'tests': 5, 'failures': 0, 'skipped': 0, 'diagnostics': ''}

        cases = {
            'docker startup failure': {
                'infrastructure_error': {'exit_code': 125, 'log': 'docker: no such image'},
                'junit': low,
            },
            'test run timed out': {
                'tests': {'exit_code': 124, 'timed_out': True, 'log': ''},
                'junit': low,
            },
            'container cleanup failure': {
                'cleanup': {'exit_code': 1, 'log': 'docker rm: permission denied'},
                'junit': low,
            },
            'compile failure': {
                'compile': {'exit_code': 1, 'log': ':compileJava FAILED'},
                'junit': low,
            },
            'malformed junit evidence': {
                'junit': {'tests': 'five', 'failures': 0, 'skipped': 0},
            },
            'missing junit evidence': {},
        }

        for name, extra in cases.items():
            with self.subTest(failure=name):
                verification = {'passed': False, 'repairable': False}
                verification.update(extra)
                _, result, prompts = self.run_case(
                    answers=[SELECTION, PLAN, GOOD_PROD, REDUCED_TEST],
                    verify_results=[verification],
                )
                self.assertEqual(result['status'], 'failed')
                self.assertIn('not repairable', result['error'])
                # Selection, plan, two edits - no repair inference at all.
                self.assertEqual(len(prompts), 4)

    def test_insufficient_count_repair_uses_the_existing_two_attempt_budget(self):
        # Two repairs, then stop. The newly repairable class gets the same
        # allowance as every other, not an extra one.
        _, result, prompts = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, REDUCED_TEST,
                REPAIR_TEST_FILE, REDUCED_TEST_TWO,
                REPAIR_TEST_FILE, REDUCED_TEST_THREE,
            ],
            verify_results=[
                SHORT_COUNT,
                dict(SHORT_COUNT, junit={'tests': 6, 'failures': 0, 'skipped': 0}),
                dict(SHORT_COUNT, junit={'tests': 7, 'failures': 0, 'skipped': 0}),
            ],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('two bounded repairs', result['error'])
        # Selection, plan, two edits, and exactly two repair rounds of two.
        self.assertEqual(len(prompts), 8)
        # Candidate verification ran three times; no fourth attempt.
        self.assertEqual(len(self.observed_test_sources), 3)

    def test_deleted_baseline_tests_with_green_remainder_never_pass(self):
        # Every remaining test is green at each round; only the count is
        # short. Becoming repairable must not make this acceptable - the
        # floor still rejects it at candidate verification, every time.
        _, result, _ = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, REDUCED_TEST,
                REPAIR_TEST_FILE, REDUCED_TEST_TWO,
                REPAIR_TEST_FILE, REDUCED_TEST_THREE,
            ],
            verify_results=[
                SHORT_COUNT,
                dict(SHORT_COUNT, junit={'tests': 11, 'failures': 0, 'skipped': 0}),
                dict(SHORT_COUNT, junit={'tests': 11, 'failures': 0, 'skipped': 0}),
            ],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertNotEqual(result['status'], 'succeeded')

    def test_full_repair_path_restores_tests_and_still_runs_baseline_regression(self):
        # End-to-end regression for the discovered bug: candidate drops
        # tests -> repair restores the originals and adds the new case ->
        # candidate passes -> Stage 5 STILL re-verifies against the real
        # base-commit test bytes, not the repaired ones.
        job_id, result, prompts = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, REDUCED_TEST,
                REPAIR_TEST_FILE, GOOD_TEST,
            ],
            verify_results=[
                SHORT_COUNT,
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        original_test = git(self.repo, 'show', 'main:' + TEST_TARGET, raw=True)
        original_prod = git(self.repo, 'show', 'main:' + PROD_TARGET, raw=True)
        self.assertEqual(len(self.observed_test_sources), 4)
        self.assertEqual(self.observed_test_sources[0], REDUCED_TEST)
        self.assertEqual(self.observed_test_sources[1], GOOD_TEST)
        # Stage 5 discarded the repaired suite and injected real base bytes.
        self.assertEqual(self.observed_test_sources[2], original_test)
        # Stage 6 does the inverse: repaired candidate tests against pinned
        # base production.
        self.assertEqual(self.observed_test_sources[3], GOOD_TEST)
        self.assertEqual(self.observed_production_sources[1], GOOD_PROD)
        self.assertEqual(self.observed_production_sources[3], original_prod)

        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        self.assertTrue(
            (workdir / 'attempt-3' / 'baseline-test-verification').is_dir()
        )

        # The repaired file is what got committed, and scope never widened.
        repository = json.loads(
            (workdir / 'repository.json').read_text(encoding='utf-8')
        )
        checkout = pathlib.Path(repository['checkout'])
        self.assertEqual(
            git(checkout, 'show', 'HEAD:' + TEST_TARGET, raw=True),
            GOOD_TEST,
        )
        changed = set(git(checkout, 'diff', '--name-only',
                          self.spec['base_commit'], 'HEAD').splitlines())
        self.assertEqual(changed, {PROD_TARGET, TEST_TARGET})

    def test_repaired_coverage_still_faces_the_added_coverage_comparison(self):
        # A repair that reaches the floor but adds nothing over the real
        # baseline is still rejected - the fix must not shortcut Stage 5's
        # comparison.
        _, result, _ = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, REDUCED_TEST,
                REPAIR_TEST_FILE, GOOD_TEST,
            ],
            verify_results=[
                SHORT_COUNT,
                {'passed': True, 'junit': {'tests': 12, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
            ],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('no new cases were added', result['error'])

    # ------------------------------------------------------------------
    # Classifier and diagnostic units
    # ------------------------------------------------------------------

    def test_executed_test_count_excludes_skipped_cases(self):
        self.assertEqual(executed_test_count({'tests': 13, 'skipped': 8}), 5)
        self.assertEqual(executed_test_count({'tests': 12}), 12)
        self.assertIsNone(executed_test_count({'tests': 'twelve'}))
        self.assertIsNone(executed_test_count({'tests': 12, 'skipped': None}))
        self.assertIsNone(executed_test_count({}))

    def test_skipping_tests_cannot_satisfy_the_floor(self):
        # 13 declared cases, 8 of them skipped: 5 executed, still short.
        result = {
            'passed': False, 'repairable': False,
            'compile': {'exit_code': 0}, 'tests': {'exit_code': 0},
            'cleanup': {'exit_code': 0},
            'junit': {'tests': 13, 'failures': 0, 'skipped': 8},
        }
        self.assertTrue(insufficient_test_count(result, 12))

    def test_insufficient_test_count_classifier_boundaries(self):
        base = {
            'passed': False, 'repairable': False,
            'compile': {'exit_code': 0}, 'tests': {'exit_code': 0},
            'cleanup': {'exit_code': 0},
            'junit': {'tests': 5, 'failures': 0, 'skipped': 0},
        }
        self.assertTrue(insufficient_test_count(base, 12))
        # Exactly at the floor is not a shortfall.
        self.assertFalse(
            insufficient_test_count(dict(base, junit={'tests': 12, 'failures': 0}), 12)
        )
        # A passing run is never a repair candidate.
        self.assertFalse(insufficient_test_count(dict(base, passed=True), 12))
        # Real JUnit failures are the shared flag's business, not this one.
        self.assertFalse(
            insufficient_test_count(
                dict(base, junit={'tests': 5, 'failures': 2, 'skipped': 0}), 12
            )
        )
        # An already-gone container is tolerated exactly as verify() does.
        tolerated = dict(base, cleanup={'exit_code': 1, 'log': 'Error: No such container: x'})
        self.assertTrue(insufficient_test_count(tolerated, 12))

    def test_candidate_repairable_needs_a_selected_test_file(self):
        short = {
            'passed': False, 'repairable': False,
            'compile': {'exit_code': 0}, 'tests': {'exit_code': 0},
            'cleanup': {'exit_code': 0},
            'junit': {'tests': 5, 'failures': 0, 'skipped': 0},
        }
        self.assertTrue(candidate_repairable(short, 12, [TEST_TARGET]))
        # No editable test file in scope: nothing in range can restore
        # coverage, so this stays terminal.
        self.assertFalse(candidate_repairable(short, 12, []))
        # The shared flag still wins on its own terms.
        self.assertTrue(
            candidate_repairable({'passed': False, 'repairable': True}, 12, [])
        )

    def test_insufficient_count_diagnostic_is_actionable(self):
        result = {
            'passed': False, 'repairable': False,
            'compile': {'exit_code': 0}, 'tests': {'exit_code': 0, 'log': ''},
            'junit': {'tests': 5, 'failures': 0, 'skipped': 0, 'diagnostics': ''},
        }
        diagnostic = verification_diagnostic(result, 12)
        self.assertIn('5', diagnostic)
        self.assertIn('12', diagnostic)
        self.assertNotIn('without a concise diagnostic', diagnostic)

        # Called without a minimum - as accepted-java-v1 does - behavior is
        # exactly what it was before.
        self.assertIn(
            'without a concise diagnostic',
            verification_diagnostic(result),
        )

    def test_test_file_prompts_ask_the_model_to_preserve_existing_tests(self):
        # Quality guidance, not a gate: it discourages the replacement-suite
        # behavior that caused the incident, while the count and regression
        # checks stay the actual enforcement.
        plan = json.loads(PLAN)
        related = PROD_TARGET + ':\n' + GOOD_PROD

        test_prompt = edit_prompt(TASK, plan, TEST_TARGET, GOOD_TEST, related)
        self.assertIn('Keep every existing @Test method', test_prompt)

        # Production edits are unaffected.
        prod_prompt = edit_prompt(TASK, plan, PROD_TARGET, GOOD_PROD)
        self.assertNotIn('@Test method', prod_prompt)

        # The instruction must not eat the controller budget: a realistic
        # 7B-sized test edit still needs real headroom under 2000 bytes.
        self.assertLess(len(test_prompt.encode()), 1800)


# ------------------------------------------------------------------
# Workflow 26 regression fixture
# ------------------------------------------------------------------
# Pinned base 6749c9b46552147e33c14a73b80b699924667b46 ALREADY trimmed
# leading and trailing hyphens. Candidate commit
# 4ed2d63c1b4f7d750aadf80c3d66b389126b064b claimed to add that support and
# shipped a file that was behaviorally identical to the base and had only
# lost its indentation, plus a test the base already satisfied. Every gate
# that existed at the time passed it.
WORKFLOW_26_BASE_PROD = '''package lab;
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
}
'''

WORKFLOW_26_PROD = '''package lab;
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
}
'''

# The genuine counterpart: a base that does NOT trim, and a candidate that
# adds the trimming its new test asserts.
GENUINE_BASE_PROD = '''package lab;
import java.util.Locale;
public class Slugs {
    public static String slugify(String text) {
        if (text == null) {
            throw new IllegalArgumentException("Input text cannot be null");
        }
        return text.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]+", "-");
    }
}
'''

# A candidate introducing a brand-new API its tests call. The pinned base
# does not declare it, so the hybrid cannot even compile the test sources.
NEW_API_PROD = '''package lab;
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


class BehavioralDeltaTests(ExecuteWorkflowHarness):
    """Milestone 7B hardening: candidate tests must distinguish candidate
    production from pinned-base production before the workflow succeeds."""

    def pin_base_production(self, source):
        """Commit `source` as the pinned base production implementation."""
        (self.repo / PROD_TARGET).write_text(source, encoding='utf-8')
        git(self.repo, 'add', '.')
        git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
            'commit', '-m', 'Pin base production for the behavioral-delta fixture')
        self.spec = prepare_spec(self.repo, 'main', TASK)

    def added_case_test(self, case):
        """The committed test suite plus exactly one new @Test method."""
        original = git(self.repo, 'show', 'main:' + TEST_TARGET, raw=True)
        return original[:original.rindex('}')] + case + '}\n'

    HYPHEN_CASE = (
        '    @Test void leadingTrailingHyphens() '
        '{ assertEquals("hello-world", Slugs.slugify("--Hello-World--")); }\n'
    )

    UNICODE_CASE = (
        '    @Test void unicode() '
        '{ assertEquals("aeo", Slugs.slugifyUnicode("\u00e4\u00eb\u00f6")); }\n'
    )

    def delta_evidence(self, job_id, attempt=3):
        """Behavioral-delta evidence for a candidate generation: attempt 3 is
        the first candidate, attempt 8 the one a semantic re-plan produced."""
        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        return json.loads(
            (workdir / f'attempt-{attempt}' / 'behavioral-delta.json').read_text(
                encoding='utf-8')
        )

    # ------------------------------------------------------------------
    # Fixture 1: Workflow 26 - no behavioral delta
    # ------------------------------------------------------------------

    def test_workflow_26_fixture_is_rejected_with_no_behavioral_delta(self):
        # One bounded semantic re-plan runs first; when the replacement
        # candidate ALSO shows no delta, the budget is spent and the workflow
        # terminates on the same terminal state as before.
        self.pin_base_production(WORKFLOW_26_BASE_PROD)
        job_id, result, _ = self.run_case(
            answers=[SELECTION, PLAN, WORKFLOW_26_PROD,
                     self.added_case_test(self.HYPHEN_CASE),
                     SEMANTIC_DIAGNOSIS, REPLAN_PLAN, WORKFLOW_26_PROD,
                     self.added_case_test(self.HYPHEN_CASE)],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_NO_DELTA,  # the added test also passes against base
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_NO_DELTA,  # and so does the re-planned candidate's
            ],
        )

        self.assertEqual(result['status'], REJECTED_NO_BEHAVIORAL_DELTA)
        self.assertNotEqual(result['status'], 'succeeded')
        # A distinct terminal state, not a generic failure.
        self.assertNotEqual(result['status'], 'failed')
        self.assertIsNotNone(result['finished_at'])
        self.assertIn('pinned-base production', result['error'])
        self.assertIn('behavioral delta', result['error'])

        evidence = self.delta_evidence(job_id, attempt=8)
        self.assertEqual(evidence['classification'], NO_BEHAVIORAL_DELTA)
        self.assertFalse(evidence['distinguishing'])
        self.assertEqual(evidence['semantic_replan_attempt'], 1)
        self.assertEqual(evidence['semantic_replan_outcome'],
                         'replan-exhausted-no-delta')

        # Queryable from the stored attempt record, not only from the log.
        final = result['attempts'][-1]
        self.assertEqual(final['phase'], REJECTED_NO_BEHAVIORAL_DELTA)
        self.assertEqual(final['result']['stage'], 'behavioral-delta')
        self.assertIs(final['result']['passed'], False)

    def test_no_behavioral_delta_never_commits_or_publishes(self):
        self.pin_base_production(WORKFLOW_26_BASE_PROD)
        job_id, result, _ = self.run_case(
            answers=[SELECTION, PLAN, WORKFLOW_26_PROD,
                     self.added_case_test(self.HYPHEN_CASE),
                     SEMANTIC_DIAGNOSIS, REPLAN_PLAN, WORKFLOW_26_PROD,
                     self.added_case_test(self.HYPHEN_CASE)],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_NO_DELTA,
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_NO_DELTA,
            ],
        )
        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        checkout = workdir / 'repo'
        # No commit, no repository.json for the publisher to consume.
        self.assertEqual(git(checkout, 'rev-parse', 'HEAD'), self.spec['base_commit'])
        self.assertFalse((workdir / 'repository.json').exists())
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    # ------------------------------------------------------------------
    # Fixture 2: genuine behavioral change - must be allowed through
    # ------------------------------------------------------------------

    def test_genuine_behavioral_change_passes_the_gate_and_commits(self):
        self.pin_base_production(GENUINE_BASE_PROD)
        job_id, result, _ = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD,
                     self.added_case_test(self.HYPHEN_CASE)],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_DISTINGUISHING,  # base production fails the new case
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        evidence = self.delta_evidence(job_id)
        self.assertEqual(evidence['classification'], DISTINGUISHING_TEST_FAILURE)
        self.assertTrue(evidence['distinguishing'])
        # Failure diagnostics are preserved as behavioral-delta evidence.
        self.assertIn('expected: <hello-world>', evidence['diagnostic'])

        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        checkout = workdir / 'repo'
        self.assertNotEqual(git(checkout, 'rev-parse', 'HEAD'), self.spec['base_commit'])

        final = next(a['result'] for a in result['attempts']
                     if (a.get('result') or {}).get('profile') == 'repo-execute-v1')
        self.assertEqual(final['behavioral_delta']['classification'],
                         DISTINGUISHING_TEST_FAILURE)

    # ------------------------------------------------------------------
    # Fixture 3: new candidate API - compile delta is distinguishing
    # ------------------------------------------------------------------

    def test_new_api_compile_failure_is_distinguishing_not_infrastructure(self):
        self.pin_base_production(WORKFLOW_26_BASE_PROD)
        job_id, result, _ = self.run_case(
            answers=[SELECTION, PLAN, NEW_API_PROD,
                     self.added_case_test(self.UNICODE_CASE)],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_API_COMPILE_FAILURE,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        evidence = self.delta_evidence(job_id)
        self.assertEqual(evidence['classification'],
                         DISTINGUISHING_API_COMPILE_FAILURE)
        self.assertTrue(evidence['distinguishing'])
        self.assertIn('API the base does not provide', evidence['diagnostic'])

    # ------------------------------------------------------------------
    # Fixture 4: infrastructure failure - fail closed
    # ------------------------------------------------------------------

    def test_hybrid_infrastructure_failure_fails_closed(self):
        self.pin_base_production(GENUINE_BASE_PROD)
        cases = {
            'docker startup failure': {
                'infrastructure_error': {'exit_code': 125, 'log': 'docker: no such image'},
            },
            'compile timeout': {
                'compile': {'exit_code': 124, 'timed_out': True, 'log': ''},
            },
            'test timeout': {
                'tests': {'exit_code': 124, 'timed_out': True, 'log': ''},
            },
            'container cleanup failure': {
                'cleanup': {'exit_code': 1, 'log': 'docker rm: permission denied'},
                'junit': {'tests': 13, 'failures': 0, 'skipped': 0},
            },
            'missing junit evidence': {},
            'malformed junit evidence': {
                'junit': {'tests': 'thirteen', 'failures': 0, 'skipped': 0},
            },
            'gradle failed without a junit failure': {
                'tests': {'exit_code': 1},
                'junit': {'tests': 13, 'failures': 0, 'skipped': 0},
            },
            'base production failed to compile': {
                'compile': {'exit_code': 1, 'timed_out': False,
                            'log': '> Task :compileJava FAILED\n'
                                   '/work/project/src/main/java/lab/Slugs.java:4: '
                                   'error: cannot find symbol\n'},
            },
        }

        for name, extra in cases.items():
            with self.subTest(failure=name):
                hybrid = {'passed': False}
                hybrid.update(extra)
                job_id, result, _ = self.run_case(
                    answers=[SELECTION, PLAN, GOOD_PROD,
                             self.added_case_test(self.HYPHEN_CASE)],
                    verify_results=[
                        {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                        {'passed': True, 'junit': {'tests': 12}},
                        hybrid,
                    ],
                )
                # Infrastructure trouble is not novelty evidence, and it is
                # not a no-op verdict either.
                self.assertEqual(result['status'], 'failed')
                self.assertNotEqual(result['status'], REJECTED_NO_BEHAVIORAL_DELTA)
                self.assertIn('Behavioral-delta', result['error'])

                evidence = self.delta_evidence(job_id)
                self.assertEqual(evidence['classification'], INFRASTRUCTURE_FAILURE)
                self.assertFalse(evidence['distinguishing'])

                checkout = self.root / 'jobs' / f'workflow-{job_id}' / 'repo'
                self.assertEqual(git(checkout, 'rev-parse', 'HEAD'),
                                 self.spec['base_commit'])

    # ------------------------------------------------------------------
    # Hybrid state construction
    # ------------------------------------------------------------------

    def test_hybrid_runs_base_production_against_candidate_tests(self):
        self.pin_base_production(GENUINE_BASE_PROD)
        candidate_test = self.added_case_test(self.HYPHEN_CASE)
        job_id, result, _ = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD, candidate_test],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        # Candidate, baseline regression, hybrid - in that order.
        self.assertEqual(len(self.observed_production_sources), 3)
        self.assertEqual(self.observed_production_sources[0], GOOD_PROD)
        self.assertEqual(self.observed_production_sources[1], GOOD_PROD)
        # The hybrid saw PINNED-BASE production, never the candidate's.
        self.assertEqual(self.observed_production_sources[2], GENUINE_BASE_PROD)
        # Stage 5 ran base tests against candidate production; Stage 6 runs
        # candidate tests against base production - the exact inverse.
        self.assertEqual(self.observed_test_sources[0], candidate_test)
        self.assertEqual(self.observed_test_sources[1],
                         git(self.repo, 'show', 'main:' + TEST_TARGET, raw=True))
        self.assertEqual(self.observed_test_sources[2], candidate_test)

        # The candidate production file is restored afterwards and committed.
        checkout = self.root / 'jobs' / f'workflow-{job_id}' / 'repo'
        self.assertEqual(git(checkout, 'show', 'HEAD:' + PROD_TARGET, raw=True),
                         GOOD_PROD)

    def test_hybrid_manifest_records_reproducible_evidence(self):
        self.pin_base_production(GENUINE_BASE_PROD)
        candidate_test = self.added_case_test(self.HYPHEN_CASE)
        job_id, result, _ = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD, candidate_test],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                HYBRID_DISTINGUISHING,
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        evidence = self.delta_evidence(job_id)
        manifest = evidence['manifest']
        self.assertEqual(manifest['base_commit'], self.spec['base_commit'])
        self.assertEqual(list(manifest['overlaid_candidate_test_files']), [TEST_TARGET])
        self.assertEqual(
            manifest['overlaid_candidate_test_files'][TEST_TARGET],
            hashlib.sha256(candidate_test.encode()).hexdigest(),
        )
        self.assertEqual(manifest['base_production_files'], [PROD_TARGET])
        # The hybrid the verifier actually built is exactly base content plus
        # the overlaid candidate test.
        self.assertEqual(manifest['hybrid_snapshot_sha256'],
                         manifest['expected_snapshot_sha256'])
        self.assertEqual(
            manifest['expected_snapshot_sha256'][PROD_TARGET],
            hashlib.sha256(GENUINE_BASE_PROD.encode()).hexdigest(),
        )
        self.assertNotEqual(
            manifest['expected_snapshot_sha256'][PROD_TARGET],
            hashlib.sha256(GOOD_PROD.encode()).hexdigest(),
        )

        workdir = self.root / 'jobs' / f'workflow-{job_id}'
        hybrid_dir = workdir / 'attempt-3' / 'behavioral-delta-verification'
        self.assertTrue(hybrid_dir.is_dir())
        for name in ('compile.log', 'tests.log'):
            self.assertTrue((hybrid_dir / name).is_file())

    def test_candidate_production_leaking_into_the_hybrid_is_rejected(self):
        """The manifest check, not the classifier, is what pins the hybrid to
        base production - so break the snapshot and confirm it fires."""
        self.pin_base_production(GENUINE_BASE_PROD)
        leaked = dict(HYBRID_DISTINGUISHING)
        leaked['leak_candidate_production'] = True
        _, result, _ = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD,
                     self.added_case_test(self.HYPHEN_CASE)],
            verify_results=[
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
                leaked,
            ],
        )
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Hybrid counterfactual state', result['error'])

    # ------------------------------------------------------------------
    # Classifier units
    # ------------------------------------------------------------------

    def test_hybrid_overlay_is_approved_test_files_only(self):
        selected = [PROD_TARGET, TEST_TARGET]
        self.assertEqual(hybrid_overlay_files(selected, [TEST_TARGET]), [TEST_TARGET])
        # Production is exactly what the counterfactual must exclude.
        self.assertNotIn(PROD_TARGET, hybrid_overlay_files(selected, [TEST_TARGET]))
        self.assertEqual(hybrid_overlay_files(selected, []), [])

    def test_javac_errors_extracts_locations_and_messages(self):
        errors = javac_errors(HYBRID_API_COMPILE_LOG)
        self.assertEqual(len(errors), 1)
        path, message = errors[0]
        self.assertTrue(path.endswith('/' + TEST_TARGET))
        self.assertEqual(message, 'cannot find symbol')
        self.assertEqual(javac_errors(''), [])
        self.assertEqual(javac_errors(None), [])

    def test_compile_failure_attribution_requires_candidate_test_evidence(self):
        overlaid = [TEST_TARGET]
        good = {'compile': {'exit_code': 1, 'timed_out': False,
                            'log': HYBRID_API_COMPILE_LOG}}
        self.assertTrue(distinguishing_compile_failure(good, overlaid))

        rejected = {
            'production compile failed': HYBRID_API_COMPILE_LOG.replace(
                '> Task :compileJava\n', '> Task :compileJava FAILED\n'),
            'no test-compile task failure': HYBRID_API_COMPILE_LOG.replace(
                ':compileTestJava FAILED', ':compileTestJava'),
            'error outside the overlaid candidate tests': HYBRID_API_COMPILE_LOG.replace(
                'src/test/java/lab/SlugsTest.java',
                'src/test/java/lab/TextStatsTest.java'),
            'no parsable javac error': '> Task :compileTestJava FAILED\nBUILD FAILED\n',
            'unrelated javac error': HYBRID_API_COMPILE_LOG.replace(
                'error: cannot find symbol', 'error: unclosed string literal'),
        }
        for name, log in rejected.items():
            with self.subTest(case=name):
                self.assertFalse(distinguishing_compile_failure(
                    {'compile': {'exit_code': 1, 'timed_out': False, 'log': log}},
                    overlaid,
                ))

        # A timed-out or non-javac exit code is never source incompatibility.
        self.assertFalse(distinguishing_compile_failure(
            {'compile': {'exit_code': 1, 'timed_out': True,
                         'log': HYBRID_API_COMPILE_LOG}}, overlaid))
        self.assertFalse(distinguishing_compile_failure(
            {'compile': {'exit_code': 124, 'timed_out': False,
                         'log': HYBRID_API_COMPILE_LOG}}, overlaid))
        # Nothing overlaid means nothing is attributable to candidate tests.
        self.assertFalse(distinguishing_compile_failure(good, []))

    def test_classify_behavioral_delta_covers_every_case(self):
        overlaid = [TEST_TARGET]
        green = {'compile': {'exit_code': 0}, 'tests': {'exit_code': 0},
                 'cleanup': {'exit_code': 0},
                 'junit': {'tests': 13, 'failures': 0, 'skipped': 0}}

        self.assertEqual(classify_behavioral_delta(green, overlaid)[0],
                         NO_BEHAVIORAL_DELTA)
        self.assertEqual(
            classify_behavioral_delta(
                dict(green, tests={'exit_code': 1},
                     junit={'tests': 13, 'failures': 1, 'skipped': 0,
                            'diagnostics': 'boom'}),
                overlaid)[0],
            DISTINGUISHING_TEST_FAILURE,
        )
        self.assertEqual(
            classify_behavioral_delta(
                dict(green, compile={'exit_code': 1, 'timed_out': False,
                                     'log': HYBRID_API_COMPILE_LOG}),
                overlaid)[0],
            DISTINGUISHING_API_COMPILE_FAILURE,
        )

        infrastructure = {
            'not a record': None,
            'docker failure': dict(green, infrastructure_error={'exit_code': 125}),
            'cleanup failure': dict(green, cleanup={'exit_code': 1, 'log': 'denied'}),
            'compile timeout': dict(green, compile={'exit_code': 124, 'timed_out': True}),
            'missing compile evidence': dict(green, compile={}),
            'unattributable compile failure': dict(
                green, compile={'exit_code': 1, 'timed_out': False, 'log': 'BUILD FAILED'}),
            'test timeout': dict(green, tests={'exit_code': 124, 'timed_out': True}),
            'missing test evidence': dict(green, tests={}),
            'missing junit evidence': dict(green, junit={}),
            'malformed junit evidence': dict(
                green, junit={'tests': 'x', 'failures': 0, 'skipped': 0}),
            'failed task without a junit failure': dict(green, tests={'exit_code': 1}),
            'no executed cases': dict(
                green, junit={'tests': 13, 'failures': 0, 'skipped': 13}),
        }
        for name, record in infrastructure.items():
            with self.subTest(case=name):
                classification, diagnostic = classify_behavioral_delta(record, overlaid)
                self.assertEqual(classification, INFRASTRUCTURE_FAILURE)
                self.assertTrue(diagnostic)

        # An already-gone container is tolerated exactly as verify() does.
        self.assertEqual(
            classify_behavioral_delta(
                dict(green, cleanup={'exit_code': 1,
                                     'log': 'Error: No such container: x'}),
                overlaid)[0],
            NO_BEHAVIORAL_DELTA,
        )


if __name__ == '__main__':
    unittest.main()
