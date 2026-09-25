"""Patient Zero compatibility: the bounded Java-lab shape current Nullbrain accepts.

These tests document existing behavior; they change none of it. The fixture is
synthetic and built by composition on ``create_gradle_fixture.create`` -- nothing
here clones or reaches the external ``nullcode-java-lab`` repository.

The shape mirrors the real lab after its compatibility patch:

* a bounded ``editable_files`` list (under the 8-file ceiling),
* a bounded ``editable_test_files`` list (under the 8-file ceiling),
* build files byte-matching the approved ``gradle_profile`` templates,
* healthy committed Java that is deliberately NOT edit authority,
* a realistic source-size distribution straddling the 900-byte pre-filter.
"""

import itertools
import json
import pathlib
import unittest
import uuid

from nullcode.fixtures.create_gradle_fixture import create
from nullcode.gradle.gradle_workflow import inspect
from nullcode.gradle.gradle_workflow import prepare_spec as prepare_gradle_spec
from nullcode.repo.repo_execute_workflow import (
    MAX_SOURCE_BYTES,
    context_for_plan,
    edit_prompt,
    prepare_spec,
    related_context,
    repair_edit_prompt,
    validate_selection,
)
from nullcode.repo.repo_plan_workflow import planning_prompt
from nullcode.repo.repo_workflow import git

TASK = (
    "Add a titleCase(String) helper that capitalizes the first letter of each "
    "whitespace-separated word and lowercases the rest, and cover it with tests."
)

DIAGNOSTIC = (
    "JUnit: titleCase: expected: <Hello World> but was: <hello world>\n"
    "titleCaseEmpty: expected: <> but was: <null>"
)

# --- Approved production: small, realistic, all at or under 900 bytes --------

CASE_CONVERTER = '''package lab.format;

import java.util.Locale;

/** Locale-independent case conversion for ASCII labels. */
public final class CaseConverter {
    private CaseConverter() {}

    public static String upper(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        return text.toUpperCase(Locale.ROOT);
    }

    public static String lower(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        return text.toLowerCase(Locale.ROOT);
    }
}
'''

REPORT_FORMATTER = '''package lab.format;

/** Renders a fixed-order plain-text summary. Always ends with a newline. */
public final class ReportFormatter {
    private ReportFormatter() {}

    public static String format(String slug, int words, int characters) {
        if (slug == null) throw new IllegalArgumentException("null slug");
        if (words < 0 || characters < 0) {
            throw new IllegalArgumentException("counts cannot be negative");
        }
        StringBuilder out = new StringBuilder();
        out.append("Slug: ").append(slug).append('\\n');
        out.append("Words: ").append(words).append('\\n');
        out.append("Characters: ").append(characters).append('\\n');
        return out.toString();
    }
}
'''

TEXT_NORMALIZER = '''package lab.text;

/** Trims and collapses ASCII whitespace. Preserves case, punctuation, accents. */
public final class TextNormalizer {
    private TextNormalizer() {}

    public static String normalize(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        return text.trim().replaceAll("\\\\s+", " ");
    }

    public static String normalizeLineEndings(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        return text.replace("\\r\\n", "\\n").replace('\\r', '\\n');
    }
}
'''

# --- Approved tests: all at or under 900 bytes ------------------------------

CASE_CONVERTER_TEST = '''package lab.format;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class CaseConverterTest {
    @Test void upper() { assertEquals("ABC", CaseConverter.upper("abc")); }
    @Test void lower() { assertEquals("abc", CaseConverter.lower("ABC")); }
    @Test void nullInput() {
        assertThrows(IllegalArgumentException.class, () -> CaseConverter.upper(null));
    }
}
'''

REPORT_FORMATTER_TEST = '''package lab.format;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class ReportFormatterTest {
    @Test void formats() {
        assertEquals("Slug: a\\nWords: 1\\nCharacters: 1\\n",
                ReportFormatter.format("a", 1, 1));
    }
    @Test void rejectsNegative() {
        assertThrows(IllegalArgumentException.class,
                () -> ReportFormatter.format("a", -1, 1));
    }
}
'''

# --- Committed but deliberately UNAPPROVED ----------------------------------
# Healthy code that is repository context, not edit authority. TEXT_ANALYSIS is
# over the 900-byte pre-filter; MAIN is comfortably under it and is unapproved
# purely because the allowlist is bounded.

TEXT_ANALYSIS = '''package lab.service;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** An immutable snapshot. Counts describe the original text, not normalized text. */
public record TextAnalysis(
        String normalizedText,
        String slug,
        int wordCount,
        int characterCount,
        int lineCount,
        Map<String, Integer> wordFrequencies) {

    public TextAnalysis {
        if (normalizedText == null || slug == null || wordFrequencies == null) {
            throw new IllegalArgumentException("analysis fields cannot be null");
        }
        if (wordCount < 0 || characterCount < 0 || lineCount < 0) {
            throw new IllegalArgumentException("counts cannot be negative");
        }
        Map<String, Integer> copy = new LinkedHashMap<>();
        wordFrequencies.forEach((word, count) -> {
            if (word == null || count == null || count < 1) {
                throw new IllegalArgumentException("frequencies require a positive count");
            }
            copy.put(word, count);
        });
        wordFrequencies = Collections.unmodifiableMap(copy);
    }
}
'''

MAIN = '''package lab.cli;

import java.io.PrintStream;

/** Minimal entry point. No file, stdin, or network access. */
public final class Main {
    private Main() {}

    public static int run(String[] args, PrintStream out, PrintStream err) {
        if (args == null || out == null || err == null) {
            throw new IllegalArgumentException("streams are required");
        }
        if (args.length != 1 || args[0] == null) {
            err.println("Usage: lab <text>");
            return 2;
        }
        out.print(args[0]);
        return 0;
    }
}
'''

TEXT_ANALYSIS_TEST = '''package lab.service;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import java.util.LinkedHashMap;
import java.util.Map;
class TextAnalysisTest {
    private Map<String, Integer> frequencies() {
        Map<String, Integer> counts = new LinkedHashMap<>();
        counts.put("hello", 1);
        return counts;
    }
    @Test void keepsFields() {
        TextAnalysis analysis = new TextAnalysis("hello", "hello", 1, 5, 1, frequencies());
        assertEquals("hello", analysis.slug());
        assertEquals(1, analysis.wordCount());
    }
    @Test void copiesFrequencies() {
        Map<String, Integer> source = frequencies();
        TextAnalysis analysis = new TextAnalysis("hello", "hello", 1, 5, 1, source);
        source.put("extra", 2);
        assertEquals(1, analysis.wordFrequencies().size());
    }
    @Test void rejectsNullFields() {
        assertThrows(IllegalArgumentException.class,
                () -> new TextAnalysis(null, "s", 0, 0, 0, frequencies()));
    }
    @Test void rejectsNegativeCounts() {
        assertThrows(IllegalArgumentException.class,
                () -> new TextAnalysis("t", "s", -1, 0, 0, frequencies()));
    }
}
'''

EXTRA_FILES = {
    'src/main/java/lab/format/CaseConverter.java': CASE_CONVERTER,
    'src/main/java/lab/format/ReportFormatter.java': REPORT_FORMATTER,
    'src/main/java/lab/text/TextNormalizer.java': TEXT_NORMALIZER,
    'src/main/java/lab/service/TextAnalysis.java': TEXT_ANALYSIS,
    'src/main/java/lab/cli/Main.java': MAIN,
    'src/test/java/lab/format/CaseConverterTest.java': CASE_CONVERTER_TEST,
    'src/test/java/lab/format/ReportFormatterTest.java': REPORT_FORMATTER_TEST,
    'src/test/java/lab/service/TextAnalysisTest.java': TEXT_ANALYSIS_TEST,
}

APPROVED_PRODUCTION = [
    'src/main/java/lab/Slugs.java',
    'src/main/java/lab/TextStats.java',
    'src/main/java/lab/format/CaseConverter.java',
    'src/main/java/lab/format/ReportFormatter.java',
    'src/main/java/lab/text/TextNormalizer.java',
]

APPROVED_TESTS = [
    'src/test/java/lab/TextStatsTest.java',
    'src/test/java/lab/format/CaseConverterTest.java',
    'src/test/java/lab/format/ReportFormatterTest.java',
]

# Committed, healthy, and deliberately outside both allowlists.
UNAPPROVED_PRODUCTION = [
    'src/main/java/lab/service/TextAnalysis.java',
    'src/main/java/lab/cli/Main.java',
]

UNAPPROVED_TESTS = [
    'src/test/java/lab/SlugsTest.java',
    'src/test/java/lab/service/TextAnalysisTest.java',
]

CONFIG_FILES = ['build.gradle', 'settings.gradle', 'gradle.properties', '.nullcode.json']


def plan_for(selected):
    """A realistic plan; ``target_plan`` clips its contribution at 350 bytes."""
    return {
        'summary': 'Add a title-case helper and cover it with unit tests.',
        'files': [
            {'path': path,
             'reason': 'Implement or cover the new titleCase behavior in '
                       + pathlib.PurePosixPath(path).name}
            for path in selected
        ],
        'steps': [
            'Add the titleCase helper to ' + pathlib.PurePosixPath(path).name
            + ' preserving the existing public API.'
            for path in selected
        ],
        'risks': ['Must not change existing behavior.'],
    }


def filler_production(index):
    """A tiny extra production class, used only to exceed allowlist ceilings."""
    return (f'src/main/java/lab/filler/Filler{index}.java',
            'package lab.filler;\n'
            f'public final class Filler{index} {{\n'
            f'    private Filler{index}() {{}}\n'
            '    public static int value() { return %d; }\n' % index
            + '}\n')


def filler_test(index):
    """A tiny extra test class, used only to exceed allowlist ceilings."""
    return (f'src/test/java/lab/filler/Filler{index}Test.java',
            'package lab.filler;\n'
            'import org.junit.jupiter.api.Test;\n'
            'import static org.junit.jupiter.api.Assertions.*;\n'
            f'class Filler{index}Test {{\n'
            f'    @Test void value() {{ assertEquals({index}, Filler{index}.value()); }}\n'
            + '}\n')


def build_patient_zero(root, production=None, tests=None, minimum_tests=21, fillers=0):
    """Create the bounded Patient Zero fixture and return its repository path."""
    repo = create(root / 'source')
    files = dict(EXTRA_FILES)
    for index in range(fillers):
        for name, content in (filler_production(index), filler_test(index)):
            files[name] = content
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    (repo / '.nullcode.json').write_text(
        json.dumps(
            {
                'profile': 'gradle-junit-v1',
                'editable_files': list(APPROVED_PRODUCTION if production is None else production),
                'editable_test_files': list(APPROVED_TESTS if tests is None else tests),
                'minimum_tests': minimum_tests,
            },
            indent=2,
        ),
        encoding='utf-8',
    )
    git(repo, 'add', '--all')
    git(repo, '-c', 'user.name=NullCode Fixture', '-c', 'user.email=nullcode@localhost',
        'commit', '-m', 'Bound the lab to the Patient Zero policy')
    return repo


class PatientZeroFixtureTests(unittest.TestCase):
    """The bounded fixture is accepted by both profiles that gate on it."""

    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        cls.root.mkdir(parents=True)
        cls.repo = build_patient_zero(cls.root)
        cls.paths, cls.config = inspect(cls.repo, 'main')

    # --- 1. inspect() accepts the bounded fixture ---------------------------

    def test_inspect_accepts_bounded_fixture(self):
        self.assertEqual(self.config['profile'], 'gradle-junit-v1')
        self.assertEqual(self.config['editable_files'], APPROVED_PRODUCTION)
        # The ceiling is shared by gradle-junit-v1 and repo-execute-v1 alike.
        self.assertLessEqual(len(self.config['editable_files']), 8)
        self.assertLessEqual(len(self.config['editable_test_files']), 8)

    # --- 2. prepare_spec() accepts it ---------------------------------------

    def test_repo_execute_prepare_spec_accepts_bounded_fixture(self):
        spec = prepare_spec(self.repo, 'main', TASK)
        self.assertEqual(spec['profile'], 'repo-execute-v1')
        self.assertEqual(spec['base_commit'], git(self.repo, 'rev-parse', 'main'))

    # --- realistic source-size distribution ---------------------------------

    def test_every_approved_source_is_within_the_pre_filter(self):
        for name in APPROVED_PRODUCTION + APPROVED_TESTS:
            with self.subTest(name=name):
                size = len((self.repo / name).read_bytes())
                self.assertLessEqual(size, MAX_SOURCE_BYTES)

    def test_fixture_straddles_the_pre_filter(self):
        """Unapproved surface includes both oversized and merely-bounded files."""
        oversized = len(
            (self.repo / 'src/main/java/lab/service/TextAnalysis.java').read_bytes())
        bounded = len((self.repo / 'src/main/java/lab/cli/Main.java').read_bytes())
        self.assertGreater(oversized, MAX_SOURCE_BYTES)
        self.assertLessEqual(bounded, MAX_SOURCE_BYTES)

    # --- 6. oversized unapproved files do not block inspection --------------

    def test_oversized_unapproved_files_do_not_prevent_inspection(self):
        oversized = 'src/main/java/lab/service/TextAnalysis.java'
        self.assertIn(oversized, self.paths)
        self.assertNotIn(oversized, self.config['editable_files'])
        self.assertGreater(len((self.repo / oversized).read_bytes()), MAX_SOURCE_BYTES)
        # Inspection still succeeds, and so does the repo-execute pre-filter,
        # because the pre-filter only reads approved files.
        self.assertTrue(prepare_spec(self.repo, 'main', TASK))

    # --- 8. unapproved committed files are not edit authority ---------------

    def test_unapproved_production_is_committed_but_not_editable(self):
        for name in UNAPPROVED_PRODUCTION:
            with self.subTest(name=name):
                self.assertIn(name, self.paths)
                self.assertNotIn(name, self.config['editable_files'])
                with self.assertRaisesRegex(ValueError, 'editable_files'):
                    prepare_gradle_spec(self.repo, 'main', TASK, name)

    def test_unapproved_tests_are_committed_but_not_selectable(self):
        approved = self.config['editable_files']
        tests = self.config['editable_test_files']
        for name in UNAPPROVED_TESTS:
            with self.subTest(name=name):
                self.assertIn(name, self.paths)
                self.assertNotIn(name, tests)
                with self.assertRaisesRegex(ValueError, 'unapproved file'):
                    validate_selection(
                        {'files': [APPROVED_PRODUCTION[0], name]}, approved, tests)

    # --- 9. build and config files remain protected -------------------------

    def test_build_and_config_files_are_never_edit_authority(self):
        approved = self.config['editable_files'] + self.config['editable_test_files']
        for name in CONFIG_FILES:
            with self.subTest(name=name):
                self.assertIn(name, self.paths)
                self.assertNotIn(name, approved)
                with self.assertRaisesRegex(ValueError, 'editable_files'):
                    prepare_gradle_spec(self.repo, 'main', TASK, name)

    def test_build_template_drift_is_rejected(self):
        (self.repo / 'build.gradle').write_text(
            "plugins { id 'application' }\n", encoding='utf-8')
        git(self.repo, 'add', 'build.gradle')
        git(self.repo, '-c', 'user.name=T', '-c', 'user.email=t@localhost',
            'commit', '-m', 'drift')
        try:
            with self.assertRaisesRegex(ValueError, 'gradle_profile template'):
                inspect(self.repo, 'main')
        finally:
            git(self.repo, 'reset', '--hard', 'HEAD~1')


class ApprovedSelectionPromptTests(unittest.TestCase):
    """Intended selections construct real prompts inside the 2000-byte budget."""

    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        cls.root.mkdir(parents=True)
        cls.repo = build_patient_zero(cls.root)
        cls.tests = APPROVED_TESTS

    def prompts_for(self, selected):
        """Every real prompt the executor builds for one selection."""
        selected = list(selected)
        validate_selection({'files': selected}, APPROVED_PRODUCTION, self.tests)
        built = [planning_prompt(TASK, selected, context_for_plan(self.repo, selected))]
        plan = plan_for(selected)
        # The executor edits production first, then tests.
        for name in sorted(selected, key=lambda n: 1 if n in self.tests else 0):
            source = (self.repo / name).read_text(encoding='utf-8')
            related = related_context(self.repo, name, selected, self.tests)
            built.append(edit_prompt(TASK, plan, name, source, related))
            built.append(
                repair_edit_prompt(TASK, plan, name, source, DIAGNOSTIC, 'reason', related))
        return built

    # --- 3. every 1-production + 1-test selection fits ----------------------

    def test_one_production_and_one_test_always_fits(self):
        combinations = [(p, t) for p in APPROVED_PRODUCTION for t in APPROVED_TESTS]
        self.assertEqual(len(combinations), 15)
        for selected in combinations:
            with self.subTest(selected=selected):
                for prompt in self.prompts_for(selected):
                    self.assertLessEqual(len(prompt.encode()), 2000)

    # --- 4. 1-production + 2-test selections, where supported ---------------

    def test_one_production_and_two_tests_fits_for_the_supported_shape(self):
        combinations = [
            (production,) + pair
            for production in APPROVED_PRODUCTION
            for pair in itertools.combinations(APPROVED_TESTS, 2)
        ]
        self.assertEqual(len(combinations), 15)
        # Every 1+2 shape in this fixture fits. The real lab is close enough to
        # the ceiling that one of its combinations does not, which is why the
        # shape is validated empirically rather than assumed.
        for selected in combinations:
            with self.subTest(selected=selected):
                for prompt in self.prompts_for(selected):
                    self.assertLessEqual(len(prompt.encode()), 2000)

    # --- 5. an over-budget 2-production + 1-test selection fails closed -----

    def test_two_production_and_one_test_fails_closed_when_over_budget(self):
        # The executor injects every selected production file as reference
        # context for a test target, so two production files plus a test
        # usually exceeds the budget. It must raise, never truncate.
        selected = [
            'src/main/java/lab/format/ReportFormatter.java',
            'src/main/java/lab/text/TextNormalizer.java',
            'src/test/java/lab/format/ReportFormatterTest.java',
        ]
        # The selection itself is policy-legal; only the budget rejects it.
        self.assertEqual(
            validate_selection({'files': selected}, APPROVED_PRODUCTION, self.tests),
            selected)
        target = selected[-1]
        source = (self.repo / target).read_text(encoding='utf-8')
        related = related_context(self.repo, target, selected, self.tests)
        # Both production files are present in full: nothing was dropped to fit.
        for name in selected[:2]:
            self.assertIn(pathlib.PurePosixPath(name).name.replace('.java', ''), related)
        with self.assertRaisesRegex(ValueError, 'nothing truncated'):
            edit_prompt(TASK, plan_for(selected), target, source, related)
        with self.assertRaisesRegex(ValueError, 'nothing truncated'):
            repair_edit_prompt(
                TASK, plan_for(selected), target, source, DIAGNOSTIC, 'reason', related)


class OversizedApprovedSourceTests(unittest.TestCase):
    """Policy still rejects an approved file over the pre-filter."""

    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        cls.root.mkdir(parents=True)
        # Same fixture, but the oversized record is wrongly approved.
        cls.repo = build_patient_zero(
            cls.root,
            production=APPROVED_PRODUCTION + ['src/main/java/lab/service/TextAnalysis.java'],
        )

    # --- 7. oversized approved files are still rejected ---------------------

    def test_oversized_approved_source_is_rejected_by_repo_execute(self):
        with self.assertRaisesRegex(ValueError, f'{MAX_SOURCE_BYTES}-byte'):
            prepare_spec(self.repo, 'main', TASK)

    def test_oversized_approved_source_is_rejected_by_gradle_profile(self):
        with self.assertRaisesRegex(ValueError, '900-byte'):
            prepare_gradle_spec(
                self.repo, 'main', TASK, 'src/main/java/lab/service/TextAnalysis.java')

    def test_gradle_inspection_still_accepts_the_repository(self):
        # inspect() does not enforce the source pre-filter; the profiles do.
        paths, config = inspect(self.repo, 'main')
        self.assertIn('src/main/java/lab/service/TextAnalysis.java', config['editable_files'])
        self.assertIn('src/main/java/lab/service/TextAnalysis.java', paths)


class AllowlistCeilingTests(unittest.TestCase):
    """The 8-file ceilings are shared policy, not per-profile policy."""

    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(__file__).resolve().parent / 'test-results' / uuid.uuid4().hex
        cls.root.mkdir(parents=True)

    def test_nine_unique_production_files_are_rejected(self):
        extra = [filler_production(i)[0] for i in range(4)]
        production = APPROVED_PRODUCTION + [
            'src/main/java/lab/service/TextAnalysis.java',
            'src/main/java/lab/cli/Main.java',
        ] + extra
        self.assertEqual(len(set(production)), 11)
        repo = build_patient_zero(self.root / 'production', production=production, fillers=4)
        with self.assertRaisesRegex(ValueError, '1 to 8 unique editable Java files'):
            inspect(repo, 'main')

    def test_eight_unique_production_files_are_accepted(self):
        """The ceiling is exactly 8, so 8 must still pass."""
        production = APPROVED_PRODUCTION + [
            'src/main/java/lab/service/TextAnalysis.java',
            'src/main/java/lab/cli/Main.java',
            filler_production(0)[0],
        ]
        self.assertEqual(len(set(production)), 8)
        repo = build_patient_zero(self.root / 'production-8', production=production, fillers=1)
        _paths, config = inspect(repo, 'main')
        self.assertEqual(len(config['editable_files']), 8)

    def test_nine_unique_test_files_are_rejected(self):
        extra = [filler_test(i)[0] for i in range(4)]
        tests = APPROVED_TESTS + [
            'src/test/java/lab/SlugsTest.java',
            'src/test/java/lab/service/TextAnalysisTest.java',
        ] + extra
        self.assertEqual(len(set(tests)), 9)
        repo = build_patient_zero(self.root / 'tests', tests=tests, fillers=4)
        with self.assertRaisesRegex(ValueError, '1 to 8 unique editable_test_files'):
            prepare_spec(repo, 'main', TASK)

    def test_eight_unique_test_files_are_accepted(self):
        """The ceiling is exactly 8, so 8 bounded test files must still pass."""
        tests = APPROVED_TESTS + [filler_test(i)[0] for i in range(5)]
        self.assertEqual(len(set(tests)), 8)
        repo = build_patient_zero(self.root / 'tests-8', tests=tests, fillers=5)
        spec = prepare_spec(repo, 'main', TASK)
        self.assertEqual(spec['profile'], 'repo-execute-v1')


if __name__ == '__main__':
    unittest.main()
