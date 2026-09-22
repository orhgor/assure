"""PDF → JDF → chunks via @uurtech/jdf-cli."""
import json
import os
import subprocess
import tempfile
import shutil
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
JDF_BIN = shutil.which("jdf") or "/opt/node-v24.11.1-linux-arm64/bin/jdf"
JDF_TIMEOUT = 60


class JdfConversionError(RuntimeError):
    pass


def _run(cmd, **kw):
    env = os.environ.copy()
    env["PATH"] = env.get("PATH", "") + ":/opt/node-v24.11.1-linux-arm64/bin"
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=JDF_TIMEOUT, env=env, **kw
        )
    except FileNotFoundError as exc:
        # A missing jdf binary is a conversion failure, not a crash: callers
        # fall back best-effort, so it surfaces as the one clean error type.
        raise JdfConversionError(f"jdf binary not found: {exc}") from exc


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


def _jdf_page_count(jdf_dict: dict) -> int:
    pages = jdf_dict.get("pages")
    return len(pages) if isinstance(pages, list) and pages else 1


def _jdf_confidence(jdf_dict: dict, key: str) -> float | None:
    """Pull a confidence figure off jdf-cli's own meta, if it ever emits one.

    jdf-cli's `convert` is a text-layer parse, not an OCR pass, so it carries
    no per-page confidence today. This reads `meta.<key>` defensively so a
    future jdf-cli version that does emit one is picked up without a code
    change here, and returns ``None`` (the honest "unknown", not a faked
    number) when it does not.
    """
    meta = jdf_dict.get("meta") if isinstance(jdf_dict, dict) else None
    if not isinstance(meta, dict):
        return None
    value = meta.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _chunk_asset_entries(chunks: list[dict], kind: str) -> list[dict]:
    """Chunks the jdf-cli chunker tagged as this structured-asset kind.

    A chunk's `types` list is jdf-cli's own label of what the block is, so it is
    the honest signal for tables/images/figures — nothing is inferred from text
    shape. Figures and images are kept apart: a figure is a semantic asset (a
    chart, a diagram — `figure` type), an image is the raw asset (`image` type);
    one may refer to the other but they never collapse into one list.
    """
    entries: list[dict] = []
    for chunk in chunks or []:
        if not isinstance(chunk, dict):
            continue
        types = chunk.get("types") or []
        if not isinstance(types, list):
            continue
        if not any(str(t).lower() == kind for t in types):
            continue
        entries.append(
            {
                "id": str(chunk.get("id") or ""),
                "text": str(chunk.get("text") or chunk.get("content") or "").strip(),
                "page": chunk.get("page"),
            }
        )
    return entries


def _page_asset_entries(jdf_dict: dict, kind: str) -> list[dict]:
    """Image/figure objects the JDF document itself lists on its pages.

    `pages[].images` is real document metadata when jdf-cli emits it. Read
    defensively: an older jdf-cli emits no asset lists and the result is an
    empty list, not a fabricated one.
    """
    entries: list[dict] = []
    pages = jdf_dict.get("pages")
    if not isinstance(pages, list):
        return entries
    for page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        assets = page.get(kind) or page.get(f"{kind}s") or []
        if not isinstance(assets, list):
            continue
        for asset in assets:
            entry = {
                "page": page.get("page") or page.get("number") or page_index + 1,
            }
            if isinstance(asset, dict):
                entry["id"] = str(asset.get("id") or asset.get("name") or "")
                entry["src"] = asset.get("src") or asset.get("data") or ""
                entry["caption"] = str(asset.get("caption") or asset.get("alt") or "")
            else:
                entry["id"] = str(asset or "")
                entry["src"] = ""
                entry["caption"] = ""
            entries.append(entry)
    return entries


def _bundle_assets(jdf_dict: dict, chunks: list[dict]) -> dict:
    """Structured content of a parse: tables, images, figures as distinct lists.

    Tables/images/figures are first-class payloads, not text that happens to
    look tabular. Chunk tags are the primary signal (jdf-cli's own labels);
    page-level image metadata is merged in for images so an asset jdf-cli only
    lists on the page is not lost. Counts and `asset_summary` ride along so a
    caller can render a badge without walking the lists.
    """
    tables = _chunk_asset_entries(chunks, "table")
    images = _chunk_asset_entries(chunks, "image") + _page_asset_entries(jdf_dict, "image")
    figures = _chunk_asset_entries(chunks, "figure")
    return {
        "tables": tables,
        "images": images,
        "figures": figures,
        "table_count": len(tables),
        "image_count": len(images),
        "figure_count": len(figures),
        "asset_summary": {
            "tables": len(tables),
            "images": len(images),
            "figures": len(figures),
        },
    }


def pdf_to_parse_bundle(
    pdf_bytes: bytes,
    *,
    strategy: str = "section",
    filename: str | None = None,
    source_kind: str = "pdf",
) -> dict:
    """PDF → JDF → chunks, plus the parse metadata routes/OMP staging need.

    JDF is the default PDF parser: this is the one call ingestion routes
    should make. ``pdf_to_jdf``/``jdf_to_chunks`` stay as the implementation
    underneath it — kept and exported for callers that already work directly
    with the JDF document or the chunk list — but a raw-JDF-only helper is
    not enough for a route that also has to stage a parse artifact into OMP
    (``services/omp.build_omp_artifact_from_parse``) or a vault row
    (``db/substrate_repository``), both of which expect ``parse_confidence``/
    ``ocr_confidence`` alongside the text. Structured content — tables,
    images, figures — travels in the bundle as distinct lists, never
    collapsed into text.

    ``page_count`` comes from the document's own page metadata (``pages``),
    not from the chunk count. ``parse_confidence``/``ocr_confidence`` are
    ``None`` when jdf-cli did not emit them: the honest unknown, never a
    fabricated score, and a 0.0 a real parser reported is passed through as
    0.0, not dropped. Any failure in either jdf-cli step surfaces as a single
    ``JdfConversionError`` carrying that step's stderr.
    """
    # pdf_to_jdf/jdf_to_chunks already raise JdfConversionError carrying the
    # failing step's stderr (`_run`'s stdout/stderr, truncated) — nothing to
    # translate here, just let it propagate as the one clean error type.
    jdf = pdf_to_jdf(pdf_bytes)
    chunks = jdf_to_chunks(jdf, strategy=strategy)
    text = chunks_to_text(chunks)
    assets = _bundle_assets(jdf, chunks)
    return {
        "jdf": jdf,
        "chunks": chunks,
        "text": text,
        "page_count": _jdf_page_count(jdf),
        "parser_name": "jdf-cli",
        "source_kind": source_kind,
        "parse_confidence": _jdf_confidence(jdf, "parse_confidence"),
        "ocr_confidence": _jdf_confidence(jdf, "ocr_confidence"),
        "tables": assets["tables"],
        "images": assets["images"],
        "figures": assets["figures"],
        "table_count": assets["table_count"],
        "image_count": assets["image_count"],
        "figure_count": assets["figure_count"],
        "asset_summary": assets["asset_summary"],
        "filename": filename,
    }


def jdf_to_document_tree(
    jdf_dict: dict,
    chunks: list[dict],
    *,
    document_id: str,
    title: str,
    parse_meta: dict[str, Any] | None = None,
) -> dict:
    """A JDF document tree built from the jdf-cli parse bundle.

    ``import_project_pdf`` saves a JDF revision, and the revision shape is the
    Assure tree (``document_id``/``meta``/``body``), not jdf-cli's raw output.
    Chunks are grouped into one section per page (``page_count`` page metadata
    drives the section list), text blocks become paragraphs and tagged blocks
    become table/image nodes so the structured content survives into the tree —
    a table is stored as a table node, an image as an image node, a figure as
    an image node flagged ``asset_kind: figure`` (JDF has no separate figure
    node type; the flag keeps figures and images distinguishable). ``parse_meta``
    rides into ``meta`` verbatim so parse/OCR confidence and asset counts are
    on the saved tree.
    """
    try:
        from ..models.jdf import empty_annotations, new_node_id
    except ImportError:  # pragma: no cover - flat-import fallback
        from models.jdf import empty_annotations, new_node_id

    meta: dict[str, Any] = {"title": title, "import_source": "jdf-cli"}
    if parse_meta:
        meta.update(parse_meta)

    def _paragraph(chunk: dict) -> dict:
        return {
            "type": "paragraph",
            "id": new_node_id("p"),
            "content": str(chunk.get("text") or chunk.get("content") or "").strip(),
            "entities_referenced": [],
            "provenance": [],
            "meta": {},
            "annotations": empty_annotations(),
        }

    def _table_node(chunk: dict) -> dict:
        return {
            "type": "table",
            "id": new_node_id("tbl"),
            "caption": str(chunk.get("text") or "").strip()[:200],
            "headers": list(chunk.get("headers") or []),
            "rows": [list(r) for r in (chunk.get("rows") or [])],
            "bound_entities": [],
            "annotations": empty_annotations(),
        }

    def _image_node(chunk: dict, *, figure: bool) -> dict:
        node_meta: dict[str, Any] = {}
        if figure:
            node_meta["asset_kind"] = "figure"
        if chunk.get("page") is not None:
            node_meta["page"] = chunk.get("page")
        return {
            "type": "image",
            "id": new_node_id("img"),
            "src": str(chunk.get("src") or ""),
            "alt": str(chunk.get("caption") or chunk.get("text") or "").strip(),
            "caption": str(chunk.get("caption") or "").strip(),
            "meta": node_meta,
            "annotations": empty_annotations(),
        }

    pages = jdf_dict.get("pages")
    page_count = len(pages) if isinstance(pages, list) and pages else 1
    body: list[dict[str, Any]] = []
    for page_num in range(1, page_count + 1):
        children: list[dict[str, Any]] = []
        for chunk in chunks or []:
            if not isinstance(chunk, dict):
                continue
            chunk_page = chunk.get("page")
            if chunk_page is not None and int(chunk_page) != page_num:
                continue
            types = [str(t).lower() for t in (chunk.get("types") or [])]
            if "table" in types:
                children.append(_table_node(chunk))
            elif "figure" in types:
                children.append(_image_node(chunk, figure=True))
            elif "image" in types:
                children.append(_image_node(chunk, figure=False))
            else:
                children.append(_paragraph(chunk))
        if not children:
            children.append(_paragraph(f"(Empty page {page_num})"))
        body.append(
            {
                "type": "section",
                "id": new_node_id("sec"),
                "title": f"Page {page_num}",
                "children": children,
                "meta": {"source_page": page_num},
                "annotations": empty_annotations(),
            }
        )
    return {
        "document_id": document_id,
        "meta": meta,
        "truth_ledger": {},
        "body": body,
    }