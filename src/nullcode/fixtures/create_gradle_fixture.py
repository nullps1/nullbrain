"""Create a small, configurable Java 21 / Gradle / JUnit repository."""
import argparse
import json
import pathlib
from nullcode.gradle.gradle_workflow import PROFILE as GRADLE_PROFILE
from nullcode.repo.repo_workflow import git

FILES = {
'src/main/java/lab/Slugs.java': '''package lab;
public class Slugs {
    public static String slugify(String text) {
        throw new UnsupportedOperationException("Implement slugify");
    }
}
''',
'src/main/java/lab/TextStats.java': '''package lab;
public class TextStats {
    public static int countWords(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        return text.isBlank() ? 0 : text.trim().split("\\\\s+").length;
    }
}
''',
'src/test/java/lab/SlugsTest.java': '''package lab;
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
}
''',
'src/test/java/lab/TextStatsTest.java': '''package lab;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class TextStatsTest {
    @Test void words() { assertEquals(3, TextStats.countWords("one two three")); }
    @Test void whitespace() { assertEquals(2, TextStats.countWords("  one\\t two\\n")); }
    @Test void empty() { assertEquals(0, TextStats.countWords("   ")); }
    @Test void nullInput() { assertThrows(IllegalArgumentException.class, () -> TextStats.countWords(null)); }
}
''',
'README.md': '# NullCode Java lab\n\nJava 21, Gradle 8.14.3, JUnit 5.11.4. Slugs needs implementation; TextStats must keep working.\n',
'.gitignore': '.gradle/\nbuild/\n',
}


def create(path):
    path = pathlib.Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    files = dict(FILES)
    template = GRADLE_PROFILE
    for name in ('build.gradle', 'settings.gradle', 'gradle.properties'):
        files[name] = (template / name).read_text(encoding='utf-8')
    files['.nullcode.json'] = json.dumps({'profile': 'gradle-junit-v1',
        'editable_files': ['src/main/java/lab/Slugs.java', 'src/main/java/lab/TextStats.java'],
        'minimum_tests': 12}, indent=2)
    for name, content in files.items():
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding='utf-8')
    git(path, 'init', '-b', 'main')
    git(path, 'add', '.')
    git(path, '-c', 'user.name=NullCode Fixture', '-c', 'user.email=nullcode@localhost', 'commit', '-m', 'Add Gradle Java lab with JUnit acceptance tests')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    print(create(parser.parse_args().path))
