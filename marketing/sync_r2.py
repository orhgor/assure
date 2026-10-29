#!/usr/bin/env python3
"""Upload marketing dist/ to R2 — changed files only (minimize Class A ops).

With --prune, after every upload succeeds, list the bucket's real objects
(Cloudflare R2 REST API, paginated) and delete site-managed keys that are no
longer in the build. Pruning is guarded: protected prefixes are never touched,
the build must look complete, and the delete count is capped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SKIP_NAMES = {"_headers", "_redirects"}

DEFAULT_ACCOUNT_ID = "381b292d419f2efdc1c85a3636268e91"
# Keys the deploy manages (and may prune): static assets, locale trees,
# root-level files, and page folders that only hold index.html / 404.html.
MANAGED_PREFIXES = ("static/", "tr/", "de/", "es/", "fr/", "ja/", "zh/", "ar/")
MANAGED_ROOT_FILE = re.compile(r"^[A-Za-z0-9._-]+\.(html|txt|xml|css|js|svg|ico|png|webp|json)$")
MANAGED_PAGE = re.compile(r"^[a-z0-9-]+(/[a-z0-9-]+)*/(index|404)\.html$")
# Never deleted, even if they match the rules above.
PROTECTED_PREFIXES = ("uploads/", "private/", "worker/", "backups/", "_", ".", ".well-known/")
PROTECTED_KEYS = frozenset(SKIP_NAMES)
MIN_BUILD_FILES = 50
DEFAULT_MAX_DELETES = 25
MAX_DELETE_FRACTION = 0.10


def is_managed(key: str) -> bool:
    if key in PROTECTED_KEYS or key.startswith(PROTECTED_PREFIXES):
        return False
    if key.startswith(MANAGED_PREFIXES):
        return True
    return bool(MANAGED_ROOT_FILE.match(key) or MANAGED_PAGE.match(key))


def plan_prune(
    build_keys: set[str],
    bucket_keys: set[str],
    *,
    max_deletes: int = DEFAULT_MAX_DELETES,
) -> tuple[list[str], list[str], str | None]:
    """Return (to_delete, unmanaged_kept, abort_reason). Pure; no network."""
    if "index.html" not in build_keys:
        return [], [], "build has no index.html"
    if len(build_keys) < MIN_BUILD_FILES:
        return [], [], f"build has only {len(build_keys)} files (< {MIN_BUILD_FILES})"
    extra = sorted(bucket_keys - build_keys)
    to_delete = [k for k in extra if is_managed(k)]
    kept = [k for k in extra if not is_managed(k)]
    cap = min(max_deletes, max(1, int(len(build_keys) * MAX_DELETE_FRACTION)))
    if len(to_delete) > cap:
        return to_delete, kept, f"{len(to_delete)} deletes exceeds cap {cap}"
    return to_delete, kept, None


def api_token(wrangler: Path) -> str:
    tok = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if tok:
        return tok
    # Local use after `wrangler login`; the token stays in memory, never printed.
    r = subprocess.run([str(wrangler), "auth", "token"], capture_output=True, text=True, check=False)
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if r.returncode != 0 or not lines:
        raise RuntimeError("no CLOUDFLARE_API_TOKEN and `wrangler auth token` failed")
    return lines[-1]


def list_bucket_keys(bucket: str, token: str, account_id: str) -> set[str]:
    keys: set[str] = set()
    cursor = ""
    base = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects"
    for _ in range(1000):
        q = {"per_page": "1000"}
        if cursor:
            q["cursor"] = cursor
        req = urllib.request.Request(
            base + "?" + urllib.parse.urlencode(q),
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("success"):
            raise RuntimeError(f"list objects failed: {data.get('errors')}")
        for obj in data.get("result") or []:
            keys.add(obj["key"])
        info = data.get("result_info") or {}
        cursor = info.get("cursor") or ""
        if not info.get("is_truncated") or not cursor:
            return keys
    raise RuntimeError("list objects: too many pages")


def delete_object(wrangler: Path, bucket: str, key: str) -> None:
    subprocess.run([str(wrangler), "r2", "object", "delete", f"{bucket}/{key}", "--remote"], check=True)


def prune(args, build_keys: set[str]) -> int:
    try:
        token = api_token(args.wrangler)
        account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", DEFAULT_ACCOUNT_ID)
        bucket_keys = list_bucket_keys(args.bucket, token, account)
    except Exception as exc:  # noqa: BLE001
        print(f"Prune aborted: could not list bucket ({exc}). Nothing deleted.", file=sys.stderr)
        return 3
    to_delete, kept, reason = plan_prune(build_keys, bucket_keys, max_deletes=args.max_deletes)
    print(f"R2 prune: bucket {len(bucket_keys)} objects, build {len(build_keys)}, "
          f"{len(to_delete)} stale managed, {len(kept)} unmanaged kept")
    for k in kept:
        print(f"  keep (unmanaged): {k}")
    for k in to_delete:
        print(f"  would delete: {k}" if args.dry_run or reason else f"  delete: {k}")
    if reason:
        print(f"Prune aborted: {reason}. Nothing deleted.", file=sys.stderr)
        return 4
    if args.dry_run:
        return 0
    for k in to_delete:
        delete_object(args.wrangler, args.bucket, k)
    print(f"✅ R2 prune done ({len(to_delete)} deleted)")
    return 0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def content_type(key: str) -> str:
    if key.endswith(".html"):
        return "text/html; charset=utf-8"
    if key.endswith(".css"):
        return "text/css; charset=utf-8"
    if key.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if key.endswith(".svg"):
        return "image/svg+xml"
    if key.endswith(".ico"):
        return "image/x-icon"
    if key.endswith(".txt"):
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def load_manifest(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def save_manifest(path: Path, files: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "files": dict(sorted(files.items())),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def iter_dist_files(dist_dir: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for path in sorted(dist_dir.rglob("*")):
        if not path.is_file():
            continue
        key = path.relative_to(dist_dir).as_posix()
        if key in SKIP_NAMES:
            continue
        out.append((key, path))
    return out


def put_object(
    wrangler: Path,
    bucket: str,
    key: str,
    file_path: Path,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    subprocess.run(
        [
            str(wrangler),
            "r2",
            "object",
            "put",
            f"{bucket}/{key}",
            f"--file={file_path}",
            f"--content-type={content_type(key)}",
            "--remote",
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--bucket", default="assure-marketing-prod")
    parser.add_argument("--wrangler", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Local hash manifest (default: .cache/marketing-r2-manifest.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned uploads; do not call wrangler",
    )
    parser.add_argument(
        "--max-uploads",
        type=int,
        default=0,
        help="Abort if more than N uploads would run (0 = no cap)",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="After uploads, delete managed bucket keys not in the build (SYNC_R2_PRUNE=0 disables)",
    )
    parser.add_argument(
        "--max-deletes",
        type=int,
        default=DEFAULT_MAX_DELETES,
        help="Abort pruning if more keys would be deleted (also capped at 10%% of the build)",
    )
    args = parser.parse_args()
    if os.environ.get("SYNC_R2_PRUNE", "1") == "0":
        args.prune = False

    dist = args.dist.resolve()
    if not dist.is_dir():
        print(f"dist not found: {dist}", file=sys.stderr)
        return 1

    manifest_path = args.manifest or (dist.parent / ".cache" / "marketing-r2-manifest.json")
    previous = load_manifest(manifest_path)
    current: dict[str, str] = {}
    to_upload: list[tuple[str, Path, str]] = []

    for key, path in iter_dist_files(dist):
        digest = sha256_file(path)
        current[key] = digest
        if previous.get(key) != digest:
            to_upload.append((key, path, digest))

    skipped = len(current) - len(to_upload)
    print(f"R2 sync: {len(to_upload)} to upload, {skipped} unchanged (skip Class A ops)")

    if args.max_uploads and len(to_upload) > args.max_uploads:
        print(
            f"Abort: {len(to_upload)} uploads exceeds --max-uploads={args.max_uploads}. "
            "Use MARKETING_DEPLOY_FORCE=1 or raise cap if intentional.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        for key, _, _ in to_upload:
            print(f"  would upload: {key}")
        return prune(args, set(current)) if args.prune else 0

    for key, path, _ in to_upload:
        print(f"  upload: {key}")
        put_object(args.wrangler, args.bucket, key, path, dry_run=False)

    if to_upload:
        save_manifest(manifest_path, current)
    elif not previous:
        save_manifest(manifest_path, current)

    print(f"✅ R2 sync done ({len(to_upload)} uploaded, {skipped} skipped)")
    if args.prune:
        return prune(args, set(current))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
