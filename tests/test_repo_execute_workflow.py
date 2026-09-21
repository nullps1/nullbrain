import hashlib
import json
import pathlib
import unittest
import uuid

from nullcode.core.java_workflow import Store
from nullcode.fixtures.create_gradle_fixture import create
from nullcode.repo.repo_execute_workflow import (
    candidate_repairable,
    edit_prompt,
    executed_test_count,
    insufficient_test_count,
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

REPAIR_TEST_FILE = '{"file": "' + TEST_TARGET + '", "reason": "Baseline tests were dropped"}'
REPAIR_PROD_FILE = '{"file": "' + PROD_TARGET + '", "reason": "Blame the implementation"}'


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


class RepoExecuteWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.repo = create(self.root / 'source')
        add_editable_test_files(self.repo, [TEST_TARGET])
        self.store = Store(self.root / 'db')
        self.spec = prepare_spec(self.repo, 'main', TASK)
        self.observed_test_sources = []

    def run_case(self, answers, verify_results):
        job_id = self.store.submit(repo_spec=self.spec)
        self.observed_test_sources = []
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
            return result

        run_job(self.store, self.store.claim(), generate, verify, self.root / 'jobs')
        return job_id, self.store.show(job_id), prompts

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
                '{"file": "' + PROD_TARGET + '", "reason": "Production logic is wrong"}',
                GOOD_PROD_REPAIRED,
            ],
            verify_results=[
                {'passed': False, 'repairable': True,
                 'junit': {'diagnostics': 'expected hello-world but was --hello-world--'}},
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},
                {'passed': True, 'junit': {'tests': 12}},
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertEqual(len(prompts), 6)

    def test_repair_selecting_a_file_outside_the_selection_is_rejected(self):
        _, result, prompts = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, GOOD_TEST,
                '{"file": "src/main/java/lab/TextStats.java", "reason": "unrelated file"}',
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
                '{"file": "' + PROD_TARGET + '", "reason": "try again"}',
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
                '{"file": "' + PROD_TARGET + '", "reason": "Production logic is wrong"}',
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
        self.assertNotIn(
            '"' + PROD_TARGET + '"',
            repair_prompt.split('Selected files: ')[1].splitlines()[0],
        )

    def test_insufficient_count_repair_still_rejects_unselected_files(self):
        # Narrowing removes an already-approved choice. It must not create a
        # path to anything outside the original selection.
        _, result, _ = self.run_case(
            answers=[
                SELECTION, PLAN, GOOD_PROD, REDUCED_TEST,
                '{"file": "src/main/java/lab/TextStats.java", "reason": "no"}',
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
            ],
        )
        self.assertEqual(result['status'], 'succeeded', result.get('error'))

        original_test = git(self.repo, 'show', 'main:' + TEST_TARGET, raw=True)
        self.assertEqual(len(self.observed_test_sources), 3)
        self.assertEqual(self.observed_test_sources[0], REDUCED_TEST)
        self.assertEqual(self.observed_test_sources[1], GOOD_TEST)
        # Stage 5 discarded the repaired suite and injected real base bytes.
        self.assertEqual(self.observed_test_sources[2], original_test)

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


if __name__ == '__main__':
    unittest.main()
