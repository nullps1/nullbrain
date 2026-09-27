"""Milestone 7B.3: bounded structural edit-context budgeting (Workflow 46).

Workflow 46 selected exactly one production file (Initials.java) and one test
file (InitialsTest.java). Production editing succeeded and grew the file past
its committed size. Building the InitialsTest.java edit prompt then included
that grown file whole as reference context, and the complete prompt exceeded
the controller's 2000-byte input limit before that second prompt artifact
could even be written:

    Complete edit context exceeds 2000 bytes; nothing truncated

These tests reproduce that failure class with a synthetic fixture (not the
real Initials/InitialsTest sources, and never touching Workflow 46's own
artifacts) and prove the fix: a bounded, structurally safe production
reference that fits the unchanged 2000-byte limit without ever byte-slicing
Java source, falling back to a deterministic, diagnostic-rich failure only
when even the minimum safe context cannot fit.
"""

import json
import pathlib
import shutil
import unittest

import test_repo_execute_workflow as workflow
from nullcode.repo.repo_execute_workflow import (
    EDIT_CONTEXT_LIMIT,
    MAX_SOURCE_BYTES,
    bounded_production_reference,
    compact_prompt_java,
    edit_reference_budget,
    java_type_skeleton,
    prepare_spec,
    related_context,
    run_job,
    select_edit_reference_context,
    select_java_members,
)
from nullcode.repo.repo_workflow import git

PROD_TARGET = workflow.PROD_TARGET  # src/main/java/lab/Slugs.java
TEST_TARGET = workflow.TEST_TARGET  # src/test/java/lab/SlugsTest.java


# ---------------------------------------------------------------------------
# 1 + 5: Workflow-46-style growth, exercised through the real run_job edit
# loop (not just the standalone prompt helpers), proving a production edit
# can be followed by a real test-edit prompt instead of the workflow dying
# between the two files.
# ---------------------------------------------------------------------------

GROWTH_TASK = (
    "Add a dotted(String) method to Slugs that returns uppercase initials "
    "separated and terminated by periods, for example 'hello Java 21' "
    "becomes 'H.J.2.'. Preserve the existing slugify behavior and add "
    "focused tests in SlugsTest covering blank input and punctuation."
)

GROWTH_PLAN = (
    '{"summary": "Add dotted(String) to Slugs.", '
    '"files": [{"path": "' + PROD_TARGET + '", "reason": "Implement dotted"}, '
    '{"path": "' + TEST_TARGET + '", "reason": "Add focused tests to '
    'validate the new dotted(String) method against several inputs."}], '
    '"steps": ["Open SlugsTest.java and add tests for the dotted(String) '
    'method."], "risks": []}'
)

# The committed base Slugs.java is a one-method stub
# (`throw new UnsupportedOperationException(...)`). This candidate keeps
# that stub byte-for-byte (it is not the file this scenario grew) and adds a
# genuinely new `dotted` method - the same shape as Workflow 46, where the
# production candidate grew because a method was added, not because the
# pre-existing method changed.
GROWN_PROD = '''package lab;
public class Slugs {
    public static String slugify(String text) {
        throw new UnsupportedOperationException("Implement slugify");
    }

    public static String dotted(String text) {
        if (text == null) throw new IllegalArgumentException("null text for dotted conversion");
        String trimmed = text.trim();
        if (trimmed.isEmpty()) return "";
        StringBuilder result = new StringBuilder();
        for (String word : trimmed.split("\\\\s+")) {
            if (word.isEmpty()) continue;
            char first = word.charAt(0);
            result.append(Character.toUpperCase(first));
            result.append('.');
        }
        return result.toString();
    }
}
'''

GROWN_TEST = '''package lab;
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
    @Test void dottedBasic() { assertEquals("H.J.2.", Slugs.dotted("hello Java 21")); }
    @Test void dottedBlank() { assertEquals("", Slugs.dotted("   ")); }
}
'''


class WorkflowFortySixGrowthTests(workflow.ExecuteWorkflowHarness):
    """Reproduces Workflow 46's shape: it must no longer die between files."""

    def setUp(self):
        super().setUp()
        # A longer, Workflow-46-shaped task than the harness default so the
        # committed (unedited) whole-file reference context genuinely does
        # not fit once Slugs.java has grown - the growth alone must matter,
        # not an artificially starved budget.
        self.spec = prepare_spec(self.repo, "main", GROWTH_TASK)

    def test_grown_production_file_still_yields_a_valid_test_edit_prompt(self):
        # Confirm the growth actually forces reduction before asserting
        # anything about the run: otherwise this test would not be
        # reproducing Workflow 46 at all.
        committed_test = git(self.repo, "show", "main:" + TEST_TARGET, raw=True)
        budget = edit_reference_budget(
            self.spec["task"],
            {"files": [{"path": TEST_TARGET, "reason": "r"}], "steps": ["s"]},
            TEST_TARGET,
            committed_test,
        )["reference_budget_bytes"]
        whole = PROD_TARGET + ":\n" + GROWN_PROD
        self.assertGreater(
            len(whole.encode("utf-8")), budget,
            "fixture does not actually force structural reduction; adjust it",
        )

        job_id, result, prompts = self.run_case(
            answers=[workflow.SELECTION, GROWTH_PLAN, GROWN_PROD, GROWN_TEST],
            verify_results=[
                {"passed": True, "junit": {"tests": 10, "failures": 0}},  # candidate
                {"passed": True, "junit": {"tests": 8}},  # baseline: original 8
                dict(workflow.HYBRID_DISTINGUISHING),
            ],
        )

        # The whole point: Workflow 46 never reached this. Every prompt the
        # workflow actually built - including the test-edit prompt this bug
        # prevented - fits the unchanged controller limit.
        self.assertEqual(len(prompts), 4)
        for prompt in prompts:
            self.assertLessEqual(len(prompt.encode("utf-8")), EDIT_CONTEXT_LIMIT)

        test_edit_prompt = prompts[3]
        self.assertIn(f"TARGET {TEST_TARGET}:", test_edit_prompt)
        self.assertIn("REFERENCE ONLY:", test_edit_prompt)
        self.assertIn(GROWTH_TASK, test_edit_prompt)

        self.assertEqual(result["status"], "succeeded", result.get("error"))

        # Evidence that reduction, not luck, is what made this fit.
        workdir = self.root / "jobs" / f"workflow-{job_id}" / "attempt-3"
        strategy = json.loads(
            (workdir / "2-SlugsTest.java.context-strategy.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(strategy["strategy"], "structural-member-selection")


# ---------------------------------------------------------------------------
# 3: minimum safe context still cannot fit -> deterministic, diagnostic
# failure, never a truncated prompt sent to inference.
# ---------------------------------------------------------------------------

# A task and test file large enough (within prepare_spec's own 500-byte task
# and 900-byte committed-source limits) that the reference budget left for
# production context is smaller than even one class's header and closing
# brace.
STARVED_TASK = (
    "Add a dotted(String) method to Slugs that returns uppercase initials separated and "
    "terminated by periods, for example 'hello Java 21' becomes 'H.J.2.'. Preserve the "
    "existing slugify behavior exactly as implemented today, and add extensive focused unit "
    "tests in SlugsTest covering blank input, punctuation, digits, accented characters, "
    "repeated separators, tabs, newlines, and very long sentences so the added suite is "
    "genuinely thorough and defensible during code review by a careful human reviewer."
)

STARVED_TEST = '''package lab;
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
    @Test void almostAtLimit() { assertEquals("z", Slugs.slugify("Z")); }
}
'''

STARVED_PLAN = (
    '{"summary": "Add dotted(String) to Slugs.", '
    '"files": [{"path": "' + PROD_TARGET + '", "reason": "' + ("x" * 200) + '"}, '
    '{"path": "' + TEST_TARGET + '", "reason": "' + ("x" * 200) + '"}], '
    '"steps": ["' + ("y" * 200) + '"], "risks": []}'
)

STARVED_PROD = '''package lab;
public class Slugs {
    public static String slugify(String text) {
        throw new UnsupportedOperationException("Implement slugify");
    }

    public static String dotted(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        return text;
    }
}
'''


class MinimumContextCannotFitTests(workflow.ExecuteWorkflowHarness):
    def setUp(self):
        super().setUp()
        self.assertLessEqual(len(STARVED_TASK.encode("utf-8")), 500)
        (self.repo / TEST_TARGET).write_text(STARVED_TEST, encoding="utf-8")
        self.assertLessEqual(
            len(STARVED_TEST.encode("utf-8")), MAX_SOURCE_BYTES
        )
        git(self.repo, "add", TEST_TARGET)
        git(
            self.repo, "-c", "user.name=Test", "-c", "user.email=test@localhost",
            "commit", "-m", "Grow SlugsTest fixture near the 900-byte cap",
        )
        self.spec = prepare_spec(self.repo, "main", STARVED_TASK)

    def test_fails_closed_with_target_limit_and_byte_diagnostics(self):
        job_id, result, prompts = self.run_case(
            answers=[workflow.SELECTION, STARVED_PLAN, STARVED_PROD],
            verify_results=[],
        )

        self.assertEqual(result["status"], "failed")
        error = result["error"]

        # Materially better than "Complete edit context exceeds 2000 bytes;
        # nothing truncated": names the target, the limit and the bytes
        # actually needed, without leaking source content.
        self.assertIn(TEST_TARGET, error)
        self.assertIn(str(EDIT_CONTEXT_LIMIT), error)
        self.assertNotIn("public class Slugs", error)
        self.assertNotIn("class SlugsTest", error)

        # Never a truncated prompt sent to inference: only selection,
        # planning and the production edit ran. Exactly like Workflow 46,
        # no second prompt artifact exists for the test file - but unlike
        # Workflow 46, a diagnostic evidence artifact does.
        self.assertEqual(len(prompts), 3)

        attempt = self.root / "jobs" / f"workflow-{job_id}" / "attempt-3"
        names = {p.name for p in attempt.iterdir()}
        self.assertIn("2-SlugsTest.java.context-budget.json", names)
        self.assertNotIn("2-SlugsTest.java.prompt.txt", names)

        diagnostic = json.loads(
            (attempt / "2-SlugsTest.java.context-budget.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(diagnostic["target"], TEST_TARGET)
        self.assertEqual(diagnostic["controller_limit"], EDIT_CONTEXT_LIMIT)
        self.assertIn("reference_budget_bytes", diagnostic)
        self.assertIn(TEST_TARGET, diagnostic["error"])


# ---------------------------------------------------------------------------
# 2: structural safety - reduction never byte-slices a Java member.
# ---------------------------------------------------------------------------

MULTI_MEMBER_SOURCE = '''package demo;

/** A brace in a comment: { and a stray } must not confuse the splitter. */
public final class Demo {
    private static final String NOTE = "contains { and } and a ; too";

    private Demo() {}

    public static int one() {
        if (true) {
            return 1;
        }
        return -1;
    }

    public static int two() {
        return 2;
    }

    public static int three() {
        return 3;
    }
}
'''


class StructuralSafetyTests(unittest.TestCase):
    def test_skeleton_round_trip_reconstructs_the_original_source_exactly(self):
        header, members, footer = java_type_skeleton(MULTI_MEMBER_SOURCE)
        self.assertEqual(header + "".join(members) + footer, MULTI_MEMBER_SOURCE)

    def test_braces_and_semicolons_inside_strings_and_comments_are_not_boundaries(self):
        header, members, footer = java_type_skeleton(MULTI_MEMBER_SOURCE)
        # NOTE (a field with a string containing {, } and ;), the private
        # constructor, and three methods: exactly five top-level members,
        # not more from the masked comment/string content being miscounted.
        self.assertEqual(len(members), 5)
        self.assertIn("NOTE", members[0])
        self.assertTrue(members[0].strip().endswith(";"))

    def test_a_budget_too_small_for_every_member_keeps_only_complete_members(self):
        # Room for the skeleton and exactly one small method, never enough
        # for two, and nowhere near enough for a byte-sliced fragment of a
        # bigger one.
        header, members, footer = java_type_skeleton(MULTI_MEMBER_SOURCE)
        base_bytes = len(header.encode("utf-8")) + len(footer.encode("utf-8"))
        one_method_bytes = len(
            next(m for m in members if "two()" in m).encode("utf-8")
        )
        budget = base_bytes + one_method_bytes + 5

        text, complete = select_java_members(MULTI_MEMBER_SOURCE, None, budget)

        self.assertIsNotNone(text)
        self.assertFalse(complete)
        self.assertLessEqual(len(text.encode("utf-8")), budget)

        # Every chosen member appears complete (its compacted text is a
        # verbatim substring, open AND close brace/body intact); a member
        # left out leaves no partial trace at all - not its signature, not a
        # dangling brace from its body. (select_java_members compacts
        # whitespace the same way related_context() does, so the comparison
        # is against the compacted form, not the raw source slice.)
        for member in members:
            compacted = compact_prompt_java(member)
            name = member.strip().splitlines()[0].strip()
            if name in text:
                self.assertIn(compacted, text)
            else:
                self.assertNotIn(name, text)

        # A structurally sliced fragment would unbalance braces; the
        # reduced text never does, because only complete members are kept.
        self.assertEqual(text.count("{"), text.count("}"))

    def test_a_text_block_is_never_reduced(self):
        # code_only() cannot safely mask a `"""..."""` text block, so
        # brace-counting inside one would be unsafe. The splitter refuses
        # to split such a file at all rather than guess.
        source = (
            "package demo;\n"
            "public final class Demo {\n"
            '    static final String X = """\n'
            "        { not a real brace }\n"
            '        """;\n'
            "}\n"
        )
        self.assertIsNone(java_type_skeleton(source))
        text, complete = select_java_members(source, None, 10)
        self.assertIsNone(text)
        self.assertFalse(complete)

    def test_bounded_production_reference_never_returns_a_dangling_fragment(self):
        root = (
            pathlib.Path(__file__).resolve().parent
            / "test-results" / "structural-safety-scratch"
        )
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        try:
            repo = workflow.create(root / "source")
            workflow.add_editable_test_files(repo, [TEST_TARGET])
            base_commit = git(repo, "rev-parse", "HEAD")

            checkout = root / "checkout"
            git(root, "clone", "--no-hardlinks", str(repo), str(checkout))
            git(checkout, "checkout", "-b", "structural-safety", base_commit)
            (checkout / PROD_TARGET).write_text(GROWN_PROD, encoding="utf-8")

            for budget in (0, 1, 20, 40, 60, 90, 150, 400, 4096):
                reduced, detail = bounded_production_reference(
                    checkout, base_commit, [PROD_TARGET], budget,
                )
                if reduced is None:
                    continue
                self.assertEqual(reduced.count("{"), reduced.count("}"))
                # A member is either wholly present or wholly absent.
                self.assertTrue(
                    "public static String dotted" not in reduced
                    or "return result.toString();" in reduced
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4: existing small-context behavior is untouched.
# ---------------------------------------------------------------------------


class ExistingSmallContextUnaffectedTests(workflow.ExecuteWorkflowHarness):
    def test_whole_file_strategy_matches_related_context_byte_for_byte(self):
        checkout = self.repo
        selected = [PROD_TARGET, TEST_TARGET]
        tests = [TEST_TARGET]
        source = (checkout / TEST_TARGET).read_text(encoding="utf-8")

        budget = edit_reference_budget(
            self.spec["task"],
            {"files": [{"path": TEST_TARGET, "reason": "r"}], "steps": ["s"]},
            TEST_TARGET,
            source,
        )["reference_budget_bytes"]

        expected = related_context(checkout, TEST_TARGET, selected, tests)
        actual, detail = select_edit_reference_context(
            checkout, TEST_TARGET, selected, tests, self.spec["base_commit"], budget,
        )

        self.assertEqual(actual, expected)
        self.assertEqual(detail["strategy"], "whole-file")

    def test_edit_reference_budget_reports_every_required_component(self):
        source = (self.repo / TEST_TARGET).read_text(encoding="utf-8")
        budget = edit_reference_budget(
            self.spec["task"],
            {"files": [{"path": TEST_TARGET, "reason": "r"}], "steps": ["s"]},
            TEST_TARGET,
            source,
        )
        for field in (
            "limit", "task_bytes", "plan_bytes", "target_bytes",
            "fixed_overhead_bytes", "reference_budget_bytes",
        ):
            self.assertIn(field, budget)
        self.assertEqual(
            budget["fixed_overhead_bytes"] + budget["reference_budget_bytes"],
            budget["limit"],
        )


if __name__ == "__main__":
    unittest.main()
