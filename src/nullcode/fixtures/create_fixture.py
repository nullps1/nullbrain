"""Create a new local Git repository for the repository-workflow smoke test."""
import argparse
import pathlib
from nullcode.repo.repo_workflow import git, TARGET, TEST
from nullcode.core.validate_java import TESTS


def create(path):
    path = pathlib.Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    for name, text in [
        (TARGET, 'public class Numbers {\n    public static int max(int[] values) {\n        return 0; // TODO implement correctly\n    }\n}\n'),
        (TEST, TESTS),
        ('README.md', '# NullCode repository fixture\n\nJava 21, no dependencies. Fix Numbers.max without changing the tests.\n')]:
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf-8')
    git(path, 'init', '-b', 'main')
    git(path, 'add', '.')
    git(path, '-c', 'user.name=NullCode Fixture', '-c', 'user.email=nullcode@localhost',
        'commit', '-m', 'Add failing Numbers fixture and fixed tests')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    print(create(parser.parse_args().path))
