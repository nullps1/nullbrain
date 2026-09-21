import hashlib
import json
import pathlib
import unittest
import uuid

from nullcode.core.java_workflow import Store
from nullcode.fixtures.create_gradle_fixture import create
from nullcode.repo.repo_execute_workflow import prepare_spec, run_job
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

    def run_case(self, answers, verify_results):
        job_id = self.store.submit(repo_spec=self.spec)
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
            result = dict(next(pending_verify))
            result.setdefault('repairable', False)
            result.update(
                compile={'exit_code': 0},
                tests={'exit_code': 0},
                cleanup={'exit_code': 0},
            )
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
                {'passed': True, 'junit': {'tests': 13, 'failures': 0}},  # candidate
                {'passed': True},  # baseline regression against the original test
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
                {'passed': True},
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

    def test_coverage_check_rejects_an_edited_test_with_no_new_cases(self):
        # The test file was edited (so it differs from the committed version)
        # but the fake verifier reports the same case count as before - this
        # must be treated as insufficient, not as a pass.
        _, result, prompts = self.run_case(
            answers=[SELECTION, PLAN, GOOD_PROD, GOOD_TEST],
            verify_results=[
                {'passed': True, 'junit': {'tests': 12, 'failures': 0}},  # not increased
            ],
        )
        self.assertEqual(result['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
