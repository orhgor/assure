"""Push a local file to the staging box over SSM, in base64 chunks.

usage: scripts/aws/_boxput.py <local-path> <remote-absolute-path>

SSM SendCommand caps the whole parameter payload at 97KB, so a file larger than
that (prototype/shell.js is ~230KB) has to travel in pieces: each chunk lands in
its own part file in parallel, and one final command concatenates and decodes
them. The remote md5 is printed so the caller can compare it with the local one.
"""
import base64
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys

INSTANCE_ID = os.environ.get("ASSURE_INSTANCE_ID", "i-03e39eccc57572191")
REGION = os.environ.get("AWS_REGION", "us-east-1")
CHUNK = 60000  # base64 chars per SSM command; the limit is 97KB of parameters


def ssm(commands: list[str]) -> tuple[str, str]:
    """Run one shell script on the box; return (status, stdout)."""
    params = json.dumps({"commands": ["\n".join(commands)]})
    cmd_id = subprocess.run(
        ["aws", "ssm", "send-command", "--instance-ids", INSTANCE_ID,
         "--document-name", "AWS-RunShellScript", "--parameters", params,
         "--region", REGION, "--output", "text", "--query", "Command.CommandId"],
        capture_output=True, text=True, check=True).stdout.strip()
    for _ in range(400):
        out = subprocess.run(
            ["aws", "ssm", "get-command-invocation", "--command-id", cmd_id,
             "--instance-id", INSTANCE_ID, "--region", REGION, "--output", "json"],
            capture_output=True, text=True, check=True).stdout
        j = json.loads(out)
        if j["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            return j["Status"], j.get("StandardOutputContent", "") + j.get("StandardErrorContent", "")
        import time
        time.sleep(3)
    return "Timeout", cmd_id


def main() -> int:
    local, remote = sys.argv[1], sys.argv[2]
    blob = open(local, "rb").read()
    local_md5 = hashlib.md5(blob).hexdigest()
    name = os.path.basename(remote)
    b64 = base64.b64encode(blob).decode("ascii")
    parts = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
    print(f"{name}: {len(blob)} bytes, {len(parts)} chunks, local md5 {local_md5}")

    # Chunks land in parallel: each is one short command writing its own part.
    def put(idx_chunk):
        idx, chunk = idx_chunk
        return ssm([f"printf '%s' '{chunk}' > /tmp/_put_{name}.{idx:03d}.b64"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(put, enumerate(parts)))
    bad = [i for i, (st, _) in enumerate(results) if st != "Success"]
    if bad:
        print(f"FAILED chunks: {bad}", file=sys.stderr)
        return 1

    status, out = ssm([
        f"cat /tmp/_put_{name}.*.b64 | base64 -d > '{remote}'",
        f"rm -f /tmp/_put_{name}.*.b64",
        f"md5sum '{remote}'; wc -c < '{remote}'",
    ])
    print(f"assemble status={status}")
    print(out.strip())
    if status != "Success":
        return 1
    remote_md5 = out.split()[0] if out.split() else ""
    if remote_md5 != local_md5:
        print(f"MD5 MISMATCH: local {local_md5} remote {remote_md5}", file=sys.stderr)
        return 1
    print("MD5 MATCH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
