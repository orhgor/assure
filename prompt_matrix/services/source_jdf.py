"""The source JDF: the raw jdf-cli document as a persisted, addressable artifact.

Until 2026-09-28 the document the models read — jdf-cli's ``pages[].elements[]``
with millimetre positions — lived only in memory (``bundle["jdf_source"]`` in
``services/pdf_ingest``, ``extracted["jdf"]`` in ``routers/substrate``) and was
gone once the revision was saved. The browser rendered the Assure tree, a
paragraph list without positions, so a reviewer could not see the page the
field came from nor point at a span of it. This module makes that document a
first-class artifact:

* ``persist_source_jdf`` writes it to the object store under
  ``documents/<project>/<document_id>/<revision or job id>.jdf`` (JSON,
  ``application/json``) so ``@uurtech/jdf``'s ``<jdf src="…/source.jdf">`` can
  render it, and records the key on the ingest job. Nothing is rendered here:
  page images are not part of the pipeline (``services/vision.render_page_png``
  renders one page for the multimodal read only, on demand, and stores
  nothing).
* ``element_index`` gives every text element the same ``eid-v1`` identity a
  Parsure field carries (``field_extractor.derive_element_id`` through
  ``page_layout``), so an id on a field, an id in the browser and an id in a
  selection anchor are the same string. The ids are stamped on the stored
  elements (``element["assure"]["element_id"]``) because jdf-cli 0.2.3 elements
  carry no id of their own and the chunk list the prefix comes from is not part
  of the stored document.
* ``find_elements_for_text`` maps a text selection made on the rendered page
  back to element ids and a union bbox — verbatim, whitespace-collapsed
  (``llm_extraction.find_verbatim``), the same comparison the claim policy uses
  for a quote. A selection that is not in the page text maps to nothing.
* ``SelectionAnchor`` / ``selection_anchor_meta``: the shape the compile and
  inquire streams accept and the ``meta.selection_anchor`` they write, with
  ``verbatim`` true only when the selection text was re-found in the cited
  sources' ``extracted_text`` — never assumed from the browser's word.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

try:
    from ..services import field_extractor as fx
    from ..services.llm_extraction import find_verbatim
    from ..services.object_store import _safe_segment, get_object_store
except ImportError:
    from services import field_extractor as fx  # type: ignore
    from services.llm_extraction import find_verbatim  # type: ignore
    from services.object_store import _safe_segment, get_object_store  # type: ignore

log = logging.getLogger(__name__)

#: Longest selection text the streams accept. A page of A4 body text is ~3,000
#: characters; a selection longer than that is a page, not an anchor.
SELECTION_TEXT_MAX_CHARS = 4000

#: Up to this many jobs are scanned for a project when resolving a document's
#: latest source JDF (the shell shows 50; 500 is the list route's own cap).
_JOB_SCAN_LIMIT = 500


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_source_jdf(jdf: Any) -> bool:
    """Whether ``jdf`` is a jdf-cli document (``pages`` list, no Assure ``body``)."""
    return isinstance(jdf, dict) and isinstance(jdf.get("pages"), list) and not isinstance(jdf.get("body"), list)


def source_jdf_key(project_id: str, document_id: str, revision: str) -> str:
    """``documents/<project>/<document_id>/<revision>.jdf``.

    The project is the first segment so a key can be checked against the
    requesting project (``key_in_project``) the way ``object_store.
    key_belongs_to_project`` checks an upload key — a document id alone is a
    client-supplied string and cannot be trusted to name the caller's project.
    """
    return (
        f"documents/{_safe_segment(project_id, 'project')}/"
        f"{_safe_segment(document_id, 'document')}/{_safe_segment(revision, 'revision')}.jdf"
    )


def document_prefix(project_id: str, document_id: str) -> str:
    return f"documents/{_safe_segment(project_id, 'project')}/{_safe_segment(document_id, 'document')}/"


def key_in_project(key: str, project_id: str) -> bool:
    """Whether a stored key names this project — the guard before the route
    streams bytes the client asked for by document id."""
    return (
        bool(key)
        and key.startswith(f"documents/{_safe_segment(project_id, 'project')}/")
        and key.endswith(".jdf")
        and ".." not in key
    )


def source_jdf_url(project_id: str, document_id: str) -> str:
    return f"/api/projects/{project_id}/documents/{document_id}/source.jdf"


def content_revision(file_bytes: bytes | None) -> str:
    """The revision segment for a path with no revision and no job (the Sources
    panel's synchronous upload): the content hash, so the same bytes land on
    the same key and a re-upload overwrites rather than accumulates."""
    return "sha-" + hashlib.sha256(file_bytes or b"").hexdigest()[:16]


# --------------------------------------------------------------------------
# Element addressing
# --------------------------------------------------------------------------

def _text_elements(page: dict) -> list[dict]:
    """The elements of one page that ``page_layout`` turns into segments, in
    the order it walks them: nested elements included, empty text skipped."""
    return [el for el in fx._walk_elements(page.get("elements")) if fx._element_text(el).strip()]


def _indexed_elements(jdf: dict, chunks: list[dict] | None) -> list[tuple[int, dict, dict]]:
    """``(page_no, element, layout_segment)`` for every text element, the
    segment being ``page_layout``'s view of that element. One walk shared by
    the index and the stamping so the two can never disagree on order."""
    if not is_source_jdf(jdf):
        return []
    pages = [p for p in jdf["pages"] if isinstance(p, dict)]
    if not any(_text_elements(p) for p in pages):
        return []
    layouts = fx.page_layout({"jdf": jdf, "chunks": list(chunks or [])})
    out: list[tuple[int, dict, dict]] = []
    for page_no, (page, segs) in enumerate(zip(pages, layouts), start=1):
        for el, seg in zip(_text_elements(page), segs):
            out.append((page_no, el, seg))
    return out


def element_index(jdf: dict, chunks: list[dict] | None = None) -> dict[str, dict[str, Any]]:
    """``{element_id: {"page", "bbox_rel", "text", "chunk_id", "start", "end"}}``
    for every text element of a jdf-cli document.

    With ``chunks`` (the ``jdf chunk`` output of the same parse) the ids are
    derived exactly as ``field_extractor.page_layout`` derives them for the
    Parsure fields — same walk, same chunk attachment, same
    ``derive_element_id`` — so the two sets are equal by construction
    (``tests/test_source_jdf.py`` proves it on one bundle). Without chunks the
    stamped ``element["assure"]["element_id"]`` is used when the document was
    stored by ``persist_source_jdf``; a document with neither gets the
    chunk-less derivation (``p<page>:`` prefix), which is a different id space
    and is reported as such by callers that compare.
    """
    out: dict[str, dict[str, Any]] = {}
    for page_no, el, seg in _indexed_elements(jdf, chunks):
        stamped = (el.get("assure") or {}).get("element_id") if isinstance(el.get("assure"), dict) else None
        eid = seg.get("element_id") if chunks else (stamped or seg.get("element_id"))
        if not eid:
            continue
        out[str(eid)] = {
            "page": page_no,
            "bbox_rel": seg.get("bbox"),
            "text": seg.get("text") or "",
            "chunk_id": seg.get("chunk_id"),
            "start": seg.get("start"),
            "end": seg.get("end"),
        }
    return out


def stamp_element_ids(jdf: dict, chunks: list[dict] | None) -> int:
    """Write ``element["assure"] = {"element_id", "policy"}`` on every text
    element, in place, from the chunk-aware derivation. Returns the number
    stamped."""
    count = 0
    for _page_no, el, seg in _indexed_elements(jdf, chunks):
        eid = seg.get("element_id")
        if not eid:
            continue
        assure = el.get("assure") if isinstance(el.get("assure"), dict) else {}
        assure.update({"element_id": str(eid), "policy": fx.NODE_ID_POLICY})
        el["assure"] = assure
        count += 1
    return count


def _union_bbox(boxes: list[list[float] | None]) -> list[float] | None:
    real = [b for b in boxes if isinstance(b, (list, tuple)) and len(b) == 4]
    if not real:
        return None
    return [
        round(min(b[0] for b in real), 4), round(min(b[1] for b in real), 4),
        round(max(b[2] for b in real), 4), round(max(b[3] for b in real), 4),
    ]


def find_elements_for_text(
    jdf: dict, text: str, page: int | None = None, chunks: list[dict] | None = None
) -> dict[str, Any]:
    """The elements a text selection covers: ``{"element_ids", "page", "bbox_rel", "found"}``.

    The page text is the elements joined by newlines (``page_layout``'s own
    page text) and the selection is looked up in it verbatim under the
    whitespace-collapsed, case-insensitive comparison of ``find_verbatim``; the
    elements whose character range overlaps the match are the answer, the
    union of their relative boxes the bbox. ``page`` restricts the search to
    one page (1-based); without it pages are searched in order and the first
    hit wins. A selection not found anywhere is ``found: False`` with no ids —
    never the nearest element.
    """
    empty = {"element_ids": [], "page": page, "bbox_rel": None, "found": False}
    needle = " ".join(str(text or "").split())
    if not needle or not is_source_jdf(jdf):
        return empty
    index = element_index(jdf, chunks)
    if not index:
        return empty
    by_page: dict[int, list[tuple[str, dict]]] = {}
    for eid, info in index.items():
        by_page.setdefault(int(info["page"]), []).append((eid, info))
    pages = [int(page)] if page is not None else sorted(by_page)
    for page_no in pages:
        entries = by_page.get(page_no) or []
        if not entries:
            continue
        entries.sort(key=lambda item: int(item[1].get("start") or 0))
        page_text = "\n".join(str(info["text"]) for _eid, info in entries)
        span = find_verbatim(page_text, needle)
        if span is None:
            continue
        start, end = span
        hits = [
            (eid, info) for eid, info in entries
            if int(info.get("start") or 0) < end and int(info.get("end") or 0) > start
        ]
        if not hits:
            continue
        return {
            "element_ids": [eid for eid, _info in hits],
            "page": page_no,
            "bbox_rel": _union_bbox([info.get("bbox_rel") for _eid, info in hits]),
            "found": True,
        }
    return empty


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def persist_source_jdf(
    project_id: str,
    document_id: str,
    revision: str,
    jdf: dict,
    *,
    chunks: list[dict] | None = None,
    job_id: str | None = None,
    filename: str | None = None,
) -> dict[str, Any] | None:
    """Store the raw jdf-cli document and return its descriptor, or None.

    The descriptor — ``{"key", "url", "pages", "elements", "stored_at",
    "revision", "document_id"}`` — is what the ingest job row, the Parsure
    report (``report["source_jdf"]``) and the Assure tree (``meta.source_jdf``)
    record. Never raises: the revision is the deliverable and a store that is
    not reachable is logged and leaves ``source_jdf`` absent, which the UI
    reads as "no source document is stored". The document is copied before the
    ids are stamped so the in-memory bundle the rest of the pipeline reads is
    left as jdf-cli produced it.
    """
    if not is_source_jdf(jdf):
        return None
    try:
        doc = copy.deepcopy(jdf)
        stamped = stamp_element_ids(doc, chunks)
        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        stored_at = _now()
        meta["assure"] = {
            "project_id": project_id,
            "document_id": document_id,
            "revision": revision,
            "filename": filename,
            "stored_at": stored_at,
            "element_id_policy": fx.NODE_ID_POLICY,
            "element_id_derivation": fx.NODE_ID_DERIVATION,
            "elements_stamped": stamped,
        }
        doc["meta"] = meta
        key = source_jdf_key(project_id, document_id, revision)
        payload = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        get_object_store().put_bytes(key, payload, content_type="application/json")
        pages = [p for p in doc["pages"] if isinstance(p, dict)]
        descriptor = {
            "key": key,
            "url": source_jdf_url(project_id, document_id),
            "pages": len(pages),
            "elements": sum(len(_text_elements(p)) for p in pages),
            "stored_at": stored_at,
            "revision": revision,
            "document_id": document_id,
            "bytes": len(payload),
        }
    except Exception:
        log.exception("source JDF for %s/%s could not be stored", project_id, document_id)
        return None
    if job_id:
        try:
            try:
                from ..db.ingest_jobs_repository import set_source_jdf_key
            except ImportError:
                from db.ingest_jobs_repository import set_source_jdf_key  # type: ignore
            set_source_jdf_key(job_id, key)
        except Exception:
            log.exception("ingest job %s: could not record source_jdf_key", job_id)
    return descriptor


def resolve_source_jdf_key(project_id: str, document_id: str, revision: str | None = None) -> str | None:
    """The stored key for a document of this project, newest first.

    Resolution: the project's ingest jobs (``source_jdf_key``, ordered by
    ``created_at``), then the project's intake reports (``report["source_jdf"]
    ["key"]``) for a Sources-panel upload that ran without a job (the
    synchronous path creates none). With ``revision`` the key must be that
    revision's. Every candidate is checked against the project prefix; a key
    that names another project is never returned.
    """
    prefix = document_prefix(project_id, document_id)
    wanted = source_jdf_key(project_id, document_id, revision) if revision else None

    def _accept(key: Any) -> str | None:
        k = str(key or "")
        if not k or not key_in_project(k, project_id) or not k.startswith(prefix):
            return None
        if wanted and k != wanted:
            return None
        return k

    try:
        try:
            from ..db.ingest_jobs_repository import list_source_jdf_keys
        except ImportError:
            from db.ingest_jobs_repository import list_source_jdf_keys  # type: ignore
        for key in list_source_jdf_keys(project_id, limit=_JOB_SCAN_LIMIT):
            hit = _accept(key)
            if hit:
                return hit
    except Exception:
        log.exception("source JDF: ingest job lookup failed for %s/%s", project_id, document_id)
    try:
        try:
            from ..db.parsure_repository import list_reports
        except ImportError:
            from db.parsure_repository import list_reports  # type: ignore
        for report in list_reports(project_id, limit=_JOB_SCAN_LIMIT, current_only=False) or []:
            if str(report.get("document_id") or "") != str(document_id):
                continue
            src = report.get("source_jdf")
            hit = _accept(src.get("key")) if isinstance(src, dict) else None
            if hit:
                return hit
    except Exception:
        log.exception("source JDF: report lookup failed for %s/%s", project_id, document_id)
    return None


def load_source_jdf(key: str) -> dict | None:
    """The stored document, parsed; None when the key is not in the store or
    does not hold a JDF. Reads what ``persist_source_jdf`` wrote — no parse."""
    try:
        raw = get_object_store().get_bytes(key)
    except Exception:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return doc if is_source_jdf(doc) else None


def describe_source_jdf(key: str, doc: dict) -> dict[str, Any]:
    """The ``source.json`` body for a stored document."""
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    assure = meta.get("assure") if isinstance(meta.get("assure"), dict) else {}
    pages = [p for p in doc["pages"] if isinstance(p, dict)]
    return {
        "key": key,
        "pages": len(pages),
        "elements": sum(len(_text_elements(p)) for p in pages),
        "stored_at": assure.get("stored_at"),
        "revision": assure.get("revision"),
        "element_id_policy": assure.get("element_id_policy"),
    }


# --------------------------------------------------------------------------
# Selection anchors (compile / inquire passthrough)
# --------------------------------------------------------------------------

class SelectionAnchor(BaseModel):
    """A text selection the browser made on the rendered source JDF.

    Strict: a page that arrives as ``"3"`` or element ids that are not strings
    are a client bug and are refused with 400 rather than coerced — the anchor
    is recorded on the document and a coerced value would be a value the
    client never sent.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    text: str = Field(min_length=1, max_length=SELECTION_TEXT_MAX_CHARS)
    page: int | None = None
    element_ids: list[str] = Field(default_factory=list, max_length=500)
    document_id: str | None = None
    source_jdf: str | None = None


def selection_anchor_meta(selection: dict[str, Any] | SelectionAnchor, source_texts: list[str]) -> dict[str, Any]:
    """The ``meta.selection_anchor`` block: the selection as sent plus
    ``verbatim`` — whether the text was re-found (whitespace-collapsed,
    case-insensitive) in one of the cited sources' ``extracted_text``. The
    browser's claim that the text came from the page is not taken on trust;
    without a source that carries it the anchor says ``verbatim: false``.
    """
    data = selection.model_dump() if isinstance(selection, SelectionAnchor) else dict(selection or {})
    text = str(data.get("text") or "")
    verbatim = any(find_verbatim(str(src or ""), text) is not None for src in source_texts) if text.strip() else False
    return {
        "text": text,
        "page": data.get("page"),
        "element_ids": [str(e) for e in (data.get("element_ids") or [])],
        "document_id": data.get("document_id"),
        "source_jdf": data.get("source_jdf"),
        "verbatim": bool(verbatim),
        "checked_against": len(source_texts),
    }
