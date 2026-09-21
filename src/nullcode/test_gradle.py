import hashlib
import pathlib
import unittest
import uuid
from create_gradle_fixture import create, FILES
from gradle_workflow import prepare_spec, run_job, extract, junit_report, repair_prompt
from java_workflow import Store
from repo_workflow import git
from publish_workflow import prepare

TARGET = 'src/main/java/lab/Slugs.java'
GOOD = '''package lab;
public class Slugs {
 public static String slugify(String text) {
  if (text == null) throw new IllegalArgumentException();
  return text.toLowerCase(java.util.Locale.ROOT).replaceAll("[^a-z0-9]+", "-").replaceAll("^-|-$", "");
 }
}
'''


class ParsingTests(unittest.TestCase):
    def test_java_type_is_selected_by_filename(self):
        self.assertEqual(extract('```java\n' + GOOD + '```', TARGET), GOOD)
        with self.assertRaises(ValueError):
            extract(GOOD, 'src/main/java/lab/Wrong.java')

    def test_junit_multiple_reports_and_failures(self):
        report = junit_report('<?xml version="1.0"?><testsuite><testcase name="a"/><testcase name="b"><failure>wrong</failure></testcase></testsuite><?xml version="1.0"?><testsuite><testcase name="c"><skipped/></testcase></testsuite>')
        self.assertEqual((report['tests'], report['failures'], report['skipped']), (3, 1, 1))
        self.assertIn('wrong', report['diagnostics'])

    def test_empty_report_is_not_test_evidence(self):
        self.assertEqual(junit_report('')['tests'], 0)

    def test_xml_entities_rejected(self):
        with self.assertRaises(ValueError):
            junit_report('<!DOCTYPE testsuite><testsuite/>')

    def test_fixture_word_counter_has_java_regex_escape(self):
        self.assertIn('split("\\\\s+")', FILES['src/main/java/lab/TextStats.java'])

    def test_repair_includes_multiple_failures_and_complete_source(self):
        xml = '<testsuite>' + ''.join(f'<testcase name="case{i}"><failure message="expected one hyphen">stack trace\n at framework.Frame</failure></testcase>' for i in range(3)) + '</testsuite>'
        diagnostic = junit_report(xml)['diagnostics']
        prompt = repair_prompt('Implement slugify.\n', GOOD, diagnostic)
        self.assertIn(GOOD, prompt)
        for i in range(3):
            self.assertIn(f'case{i}', prompt)
        self.assertNotIn('framework.Frame', prompt)

    def test_oversized_repair_is_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, 'Complete source'):
            repair_prompt('task', 'x' * 2000, 'failed')


class GradleWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.repo = create(self.root / 'source')
        self.store = Store(self.root / 'db')
        self.spec = prepare_spec(self.repo, 'main', 'Implement lowercase ASCII slugify; reject null.', TARGET)

    def test_tests_are_not_editable(self):
        with self.assertRaisesRegex(ValueError, 'editable_files'):
            prepare_spec(self.repo, 'main', 'Change tests', 'src/test/java/lab/SlugsTest.java')

    def test_custom_build_code_rejected(self):
        (self.repo / 'build.gradle').write_text('plugins { id "java" }')
        git(self.repo, 'add', '.')
        git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-m', 'custom build')
        with self.assertRaisesRegex(ValueError, 'approved'):
            prepare_spec(self.repo, 'main', 'Implement slugify', TARGET)

    def run_case(self, results, unchanged=False):
        job = self.store.submit(repo_spec=self.spec)
        pending = iter(results)
        generations = 0
        def generate(prompt, record):
            nonlocal generations
            generations += 1
            self.assertLessEqual(len(prompt.encode()), 2000)
            record(7)
            return GOOD if unchanged or generations == 1 else GOOD.replace('public class', 'public final class')
        def verify(checkout, paths, folder, minimum, phase):
            self.assertEqual(minimum, 12)
            self.assertIn('src/test/java/lab/TextStatsTest.java', paths)
            phase('compiling')
            phase('testing')
            result = dict(next(pending))
            result.update(image_id='sha256:test', compile={'exit_code': 0}, tests={'exit_code': 0}, cleanup={'exit_code': 0})
            if result['passed']:
                result['junit'] = {'tests': 12, 'failures': 0, 'skipped': 0}
            result['snapshot_sha256'] = {p: hashlib.sha256((checkout / p).read_bytes()).hexdigest() for p in paths}
            return result
        run_job(self.store, self.store.claim(), generate, verify, self.root / 'jobs')
        return self.store.show(job)

    def test_success_commits_only_selected_source(self):
        result = self.run_case([{'passed': True}])
        self.assertEqual(result['status'], 'succeeded', result['error'])
        metadata = result['attempts'][-1]['result']['repository']
        self.assertEqual(git(metadata['checkout'], 'diff', '--name-only', self.spec['base_commit'], 'HEAD'), TARGET)
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_test_failure_repairs_then_commits(self):
        result = self.run_case([{'passed': False, 'repairable': True, 'junit': {'diagnostics': 'expected hello-world'}}, {'passed': True}])
        self.assertEqual(result['status'], 'succeeded', result['error'])
        self.assertEqual(len(result['attempts']), 2)

    def test_repair_budget_stops_failing_job(self):
        bad = {'passed': False, 'repairable': True, 'junit': {'diagnostics': 'AssertionFailedError'}}
        result = self.run_case([bad, bad])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(git(self.root / 'jobs/workflow-1/repo', 'rev-parse', 'HEAD'), self.spec['base_commit'])

    def test_gradle_publisher_uses_junit_evidence(self):
        result = self.run_case([{'passed': True}])
        plan = prepare(result, self.root / 'jobs')
        self.assertIn('12 JUnit cases', plan['body'])
        self.assertIn('Slugs.java', plan['title'])
        result['attempts'][-1]['result']['junit']['failures'] = 1
        with self.assertRaises(ValueError):
            prepare(result, self.root / 'jobs')

    def test_identical_repair_fails_without_second_verification(self):
        bad = {'passed': False, 'repairable': True, 'junit': {'diagnostics': 'spaces: expected hello-world but got hello---world'}}
        result = self.run_case([bad], unchanged=True)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('identical source', result['error'])


if __name__ == '__main__':
    unittest.main()
