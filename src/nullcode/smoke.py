"""Submit one Java generation job and wait for its recorded outcome."""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8080"


def request(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


print("Controller:", request("/health"), flush=True)
job = request("/jobs", {
    "prompt": "Write a Java 21 class Numbers with static int max(int[] values). "
              "Reject null and empty arrays with IllegalArgumentException. "
              "Handle all-negative arrays correctly. Return only Java source."
})
print("Submitted job", job["id"], flush=True)
deadline = time.monotonic() + 960
last_status = None
next_report = 0
while time.monotonic() < deadline:
    result = request(f'/jobs/{job["id"]}')
    if result["status"] != last_status or time.monotonic() >= next_report:
        print("Status:", result["status"], flush=True)
        last_status = result["status"]
        next_report = time.monotonic() + 30
    if result["status"] == "succeeded":
        print(result["response"])
        print("Inference completed. Generated Java has NOT been compiled or tested.")
        break
    if result["status"] == "failed":
        raise SystemExit("Job failed: " + str(result["error"]))
    time.sleep(5)
else:
    raise SystemExit(f'Timed out waiting; inspect /jobs/{job["id"]} and controller logs.')
