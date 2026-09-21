"""Create an isolated review checkout with proposed tests; never commit or submit."""
import argparse
import json
import pathlib
from nullcode.gradle.gradle_workflow import inspect
from nullcode.repo.repo_workflow import git

# Source-controlled acceptance assets shipped as package data.
ASSETS = pathlib.Path(__file__).resolve().parent.parent / "acceptance"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--base", default="main")
    p.add_argument("--destination", required=True)
    args = p.parse_args()
    repo = pathlib.Path(args.repo).resolve(strict=True)
    destination = pathlib.Path(args.destination).resolve()
    if destination.exists():
        raise SystemExit("Destination already exists; choose a new directory")
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", args.base + "^{commit}")
    paths, config = inspect(repo, commit)
    if "src/main/java/lab/TextStats.java" not in config["editable_files"]:
        raise SystemExit("Expected the Java lab TextStats target")
    expected_base = "6749c9b46552147e33c14a73b80b699924667b46"
    if commit != expected_base:
        raise SystemExit("Lab base differs from reviewed snapshot; inspect before preparing fixture")
    destination.parent.mkdir(parents=True, exist_ok=True)
    git(destination.parent, "clone", "--no-hardlinks", "--no-checkout", "--",
        repo.as_posix(), destination.as_posix())
    git(destination, "checkout", "-b", "acceptance/textstats-v1", commit)
    git(destination, "remote", "remove", "origin")
    assets = ASSETS
    additions = {".nullcode-acceptance.json": assets / "contract.json",
                 "src/test/java/lab/TextStatsAcceptanceTest.java": assets / "TextStatsAcceptanceTest.java"}
    for name, source in additions.items():
        with (destination / name).open("x", encoding="utf-8", newline="\n") as f:
            f.write(source.read_text(encoding="utf-8"))
    print(f"Prepared review checkout: {destination}")
    print("No source changes, commit, remote push, or workflow submission performed.")
    print("Review both added files. Negative thresholds rejecting input is a proposed explicit decision.")
    print("The production method is absent until the agent implements it; baseline compilation failure is expected.")


if __name__ == "__main__":
    main()
