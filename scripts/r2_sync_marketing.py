#!/usr/bin/env python3
"""Upload marketing dist/ to R2 — changed files only (minimize Class A ops)."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

SKIP_NAMES = {"_headers", "_redirects"}


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
    args = parser.parse_args()

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
        return 0

    for key, path, _ in to_upload:
        print(f"  upload: {key}")
        put_object(args.wrangler, args.bucket, key, path, dry_run=False)

    if to_upload:
        save_manifest(manifest_path, current)
    elif not previous:
        save_manifest(manifest_path, current)

    print(f"✅ R2 sync done ({len(to_upload)} uploaded, {skipped} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
