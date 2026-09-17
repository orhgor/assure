"""PDF → JDF → chunks via @uurtech/jdf-cli."""
import json
import os
import subprocess
import tempfile
import shutil
import logging
from pathlib import Path

log = logging.getLogger(__name__)
JDF_BIN = shutil.which("jdf") or "/opt/node-v24.11.1-linux-arm64/bin/jdf"
JDF_TIMEOUT = 60


class JdfConversionError(RuntimeError):
    pass


def _run(cmd, **kw):
    env = os.environ.copy()
    env["PATH"] = env.get("PATH", "") + ":/opt/node-v24.11.1-linux-arm64/bin"
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=JDF_TIMEOUT, env=env, **kw
    )


def pdf_to_jdf(pdf_bytes: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        pdf_path = f.name
    jdf_path = pdf_path[:-4] + ".jdf"
    try:
        r = _run([JDF_BIN, "convert", pdf_path, "-o", jdf_path, "--json"])
        if r.returncode != 0:
            raise JdfConversionError(f"jdf convert failed: {r.stderr[:500]}")
        return json.loads(Path(jdf_path).read_text())
    finally:
        for p in (pdf_path, jdf_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


def chunks_to_text(chunks: list[dict]) -> str:
    """The document text of a chunk list, in order — what a vault entry stores.

    The ingest converts and chunks a PDF but never keeps the extraction on its
    own, so the chunks are the only copy of the text: joining their `text` in
    the order jdf-cli emitted them reproduces the document.
    """
    parts = [str(c.get("text") or c.get("content") or "").strip() for c in chunks or []]
    return "\n\n".join(p for p in parts if p)


def jdf_to_chunks(jdf_dict: dict, strategy: str = "section") -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".jdf", delete=False, mode="w") as f:
        json.dump(jdf_dict, f)
        jdf_path = f.name
    chunks_path = jdf_path[:-4] + ".chunks.jsonl"
    try:
        r = _run(
            [JDF_BIN, "chunk", jdf_path, "--strategy", strategy, "--format", "jsonl", "-o", chunks_path]
        )
        if r.returncode != 0:
            raise JdfConversionError(f"jdf chunk failed: {r.stderr[:500]}")
        chunks = []
        with open(chunks_path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))
        return chunks
    finally:
        for p in (jdf_path, chunks_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass