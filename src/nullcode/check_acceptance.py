"""Run the proposed acceptance suite against a reference and three mutants in Docker.

Works only in disposable clones; no inference, commits, or source-repo changes.
"""
import argparse
import json
import pathlib
import tempfile

from accepted_workflow import load_contract, prepare_spec
from gradle_workflow import verify
from repo_workflow import git


REFERENCE = r'''package lab;
public class TextStats {
    public static int countWords(String text) {
        if (text == null) throw new IllegalArgumentException("null text");
        return text.isBlank() ? 0 : text.trim().split("\\s+").length;
    }
    public static int countWordsLongerThan(String text, int minLength) {
        if (text == null || minLength < 0) throw new IllegalArgumentException();
        if (text.isBlank()) return 0;
        int count = 0;
        for (String word : text.trim().split("\\s+")) {
            if (word.length() > minLength) count++;
        }
        return count;
    }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--acceptance-reviewed", action="store_true", required=True)
    args = parser.parse_args()
    spec = prepare_spec(args.repo, args.base, ".nullcode-acceptance.json", args.acceptance_reviewed)
    root = pathlib.Path(tempfile.mkdtemp(prefix="acceptance-check-", dir="/srv/nullbrain/jobs"))
    root.chmod(0o755)
    print(f"Artifacts: {root}", flush=True)
    cases = {"reference": REFERENCE,
             "inclusive-mutant": REFERENCE.replace("word.length() > minLength", "word.length() >= minLength"),
             "zero-mutant": REFERENCE.replace("return count;", "return 0;"),
             "regression-mutant": REFERENCE.replace('return text.isBlank() ? 0 : text.trim().split("\\\\s+").length;', 'return 0;')}
    if len(set(cases.values())) != 4:
        raise SystemExit("Mutant construction failed")
    results = {}
    for name, source in cases.items():
        folder = root / name
        folder.mkdir()
        checkout = folder / "repo"
        git(folder, "clone", "--no-hardlinks", "--no-checkout", "--", spec["repo"], checkout.as_posix())
        paths, contract = load_contract(checkout, spec["base_commit"], spec["contract"])
        if contract["target"] != "src/main/java/lab/TextStats.java":
            raise SystemExit("This fixture check only supports the TextStats acceptance contract")
        git(checkout, "checkout", "--detach", spec["base_commit"])
        git(checkout, "remote", "remove", "origin")
        (checkout / contract["target"]).write_text(source)
        result = verify(checkout, paths, folder, contract["minimum_tests"],
                        lambda phase: print(name, phase, flush=True))
        results[name] = result
        (root / "results.json").write_text(json.dumps(results, indent=2))
        compiled = result.get("compile") or {}
        tested = result.get("tests") or {}
        cleanup = result.get("cleanup") or {}
        report = result.get("junit") or {}
        if (compiled.get("exit_code") != 0 or compiled.get("timed_out")
                or tested.get("timed_out") or cleanup.get("exit_code") != 0
                or cleanup.get("timed_out") or report.get("skipped") != 0
                or report.get("tests", 0) < contract["minimum_tests"]):
            raise SystemExit(f"{name}: verification infrastructure/coverage failed; inspect artifacts")
        if name == "reference":
            ok = result["passed"] and tested.get("exit_code") == 0
        else:
            ok = not result["passed"] and tested.get("exit_code") == 1 and report.get("failures", 0) > 0
        if not ok:
            raise SystemExit(f"{name}: unexpected result; stop before model evaluation")
        print(name, "PASS" if name == "reference" else "correctly rejected", flush=True)
    print("Acceptance check passed: reference passes; three faulty implementations fail.")


if __name__ == "__main__":
    main()
