"""Upload a file to a project's substrate vault through the gate.
usage: _probe_upload.py <project_id> <local_path_on_box>
"""
import json, os, re, sys, urllib.request, uuid

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")

def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""

pid, path = sys.argv[1], sys.argv[2]
name = os.path.basename(path)
data = open(path, "rb").read()
boundary = "----assureprobe" + uuid.uuid4().hex
body = b"".join([
    f"--{boundary}\r\n".encode(),
    f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
    b"Content-Type: application/octet-stream\r\n\r\n", data, b"\r\n",
    f"--{boundary}--\r\n".encode(),
])
req = urllib.request.Request(
    f"{BASE}/api/projects/{pid}/substrate/upload", data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
             "X-Shell-Key": gate_key()}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        print("http", resp.status)
        print(resp.read().decode()[:1500])
except urllib.error.HTTPError as exc:
    print("http", exc.code)
    print(exc.read().decode()[:1500])
