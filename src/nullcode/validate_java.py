"""Validate the saved Numbers.max answer; does not change controller job status."""
import argparse
import json
import pathlib
import re
import subprocess
import tempfile
import urllib.request
import uuid


TESTS = r'''public class NumbersTest {
    private static int passed;
    private static void check(String name, int expected, int[] input) {
        int actual = Numbers.max(input);
        if (actual != expected) throw new AssertionError(name + ": expected " + expected + ", got " + actual);
        passed++;
        System.out.println("PASS " + name);
    }
    private static void rejects(String name, int[] input) {
        try { Numbers.max(input); }
        catch (IllegalArgumentException expected) {
            passed++;
            System.out.println("PASS " + name);
            return;
        }
        throw new AssertionError(name + ": expected IllegalArgumentException");
    }
    public static void main(String[] args) {
        check("mixed values", 8, new int[]{3,5,7,2,8});
        check("all negative", -1, new int[]{-9,-3,-1,-8});
        check("single value", -42, new int[]{-42});
        check("duplicates and zero", 0, new int[]{0,-1,0});
        check("integer boundaries", Integer.MAX_VALUE, new int[]{Integer.MIN_VALUE,0,Integer.MAX_VALUE});
        check("minimum integer alone", Integer.MIN_VALUE, new int[]{Integer.MIN_VALUE});
        rejects("empty array", new int[]{});
        rejects("null array", null);
        System.out.println("RESULT: " + passed + "/8 checks passed");
    }
}
'''


def extract_source(answer):
    blocks = re.findall(r"^```(?:java)?[ \t]*\r?\n(.*?)^```[ \t]*$", answer, re.M | re.S)
    if "```" in answer:
        if len(blocks) != 1:
            raise ValueError("Expected exactly one Java code block; inspect the saved answer manually.")
        source = blocks[0].strip()
    else:
        source = answer.strip()
    if not re.search(r"\bpublic\s+class\s+Numbers\b", source):
        raise ValueError("Expected public class Numbers.")
    if len(source.encode()) > 32768:
        raise ValueError("Source is too large for this smoke test.")
    return source + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", type=int)
    args = parser.parse_args()
    if args.job_id < 1:
        parser.error("job_id must be positive")
    with urllib.request.urlopen(f"http://127.0.0.1:8080/jobs/{args.job_id}", timeout=15) as response:
        job = json.load(response)
    if job["status"] != "succeeded":
        raise SystemExit("Job has not completed inference successfully.")
    source = extract_source(job["response"])
    # Use the exact locally pulled image, even if its tag subsequently changes.
    image = subprocess.check_output(
        ["sudo", "docker", "image", "inspect", "eclipse-temurin:21-jdk", "--format", "{{.Id}}"],
        text=True, timeout=30,
    ).strip()
    root = pathlib.Path("/srv/nullbrain/jobs")
    folder = pathlib.Path(tempfile.mkdtemp(prefix=f"java-check-{args.job_id}-", dir=root))
    folder.chmod(0o755)
    for name, content in [("Numbers.java", source), ("NumbersTest.java", TESTS)]:
        path = folder / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o644)
    (folder / "job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    name = "nullcode-check-" + uuid.uuid4().hex
    command = [
        "sudo", "docker", "run", "--rm", "--pull=never", "--name", name,
        "--network=none", "--read-only", "--user=1001:1001", "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true", "--memory=512m", "--memory-swap=512m",
        "--cpus=1", "--pids-limit=128", "--log-driver=none",
        "--tmpfs=/work:rw,nosuid,nodev,size=64m,mode=1777",
        "--tmpfs=/tmp:rw,nosuid,nodev,size=16m,mode=1777",
        "--mount", f"type=bind,source={folder},target=/src,readonly",
        "--workdir=/work", "--entrypoint=/bin/sh", image, "-ec",
        "javac -J-Xmx128m -J-XX:ActiveProcessorCount=1 -proc:none -d /work /src/Numbers.java /src/NumbersTest.java "
        "&& java -Xmx128m -XX:ActiveProcessorCount=1 -XX:+UseSerialGC -cp /work NumbersTest",
    ]
    print("Testing saved job", args.job_id, "in a disposable Java 21 container.", flush=True)
    print("Artifacts:", folder, flush=True)
    timed_out = False
    log_path = folder / "build-test.log"
    try:
        with log_path.open("wb") as log:
            try:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120)
                exit_code = result.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = 124
    finally:
        subprocess.run(["sudo", "docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)
    report = {"job_id": args.job_id, "image_id": image, "exit_code": exit_code, "timed_out": timed_out,
              "scope": "Numbers.max smoke test; not a general code-security assessment"}
    (folder / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(log_path.read_text(errors="replace"))
    if exit_code:
        raise SystemExit(f"Validation failed (exit {exit_code}); see {log_path}")
    print("Compile/test process exited successfully. Results saved; controller inference status is unchanged.")


if __name__ == "__main__":
    main()
