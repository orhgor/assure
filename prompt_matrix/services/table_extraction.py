"""Table-aware extraction for Parsure (plan Part 3, 2026-09-27).

What jdf-cli 0.2.3 actually emits for a ruled table (measured 2026-09-27 on
``bench/cases`` ``repair_estimate.pdf`` and ``coverage_schedule.pdf`` through
``jdf_converter.pdf_to_parse_bundle``): one ``pages[].elements[]`` entry of
``type: "table"`` with ``headers[]``, ``rows[][]``, ``columns[]`` (widths),
``position {x, y}``, ``width`` and ``style.fontSize`` — **no** ``content``
and no ``height``. Because the element has no text, ``field_extractor.
page_layout`` skips it, the page text never contains the cells, and the
label pass cannot find a value that only exists in a table: the benchmark
read 0/4 in-table values on the coverage schedule. The chunker does emit a
``types: ["table"]`` chunk (``p1e2``) whose text is a ``Header: cell | …``
flattening; that chunk id is the table's node address.

Two quirks of the emitted grid are repaired here, and the repair is recorded
on the table (``layout_repair``) rather than hidden: ruled column lines
produce phantom columns with a blank header whose cells hold the value of the
header to their left (``["Hours", "", "Parts $", ""]`` with the hours figure
under the blank header), and blank columns appear where a rule had no text.
Blank-header columns whose left neighbour is empty in every row are merged
into that neighbour; all-blank columns are dropped.

Field matching (``extract_table_fields``) is header/row-label to anchor —
the same regexes the label pass uses, so a schema author adds nothing for
tables — and it is restricted to ``money`` / ``number`` / ``date`` fields
(premium, deductible, limits, amounts, dates). The cell chosen is stated in
``evidence.pick``:

* ``column`` match (a header matches the field's anchor): the ``total`` row
  when there is one, the only row, or the value every row agrees on
  (``unanimous``); when the rows disagree and there is no total row the
  **first** parseable row is taken with ``TABLE_AMBIGUOUS_FACTOR`` applied to
  the confidence (0.85 × 0.85 = 0.72 at default parser confidence — under the
  0.75 auto-accept line, so a reviewer confirms which row was meant);
* ``row_label`` match (the field's anchor is found in the row's first cell,
  e.g. ``Coverage A · Dwelling`` for ``dwelling_coverage``): the cell under a
  header that matches the anchor, else under a ``limit/amount/…`` header for
  money, else the first cell in the row that parses as the type.

Every table field is the same contract record the label pass produces
(``field_extractor._empty_field`` + the found-field keys), never a second
shape: ``extraction_method = "table"``, ``source_span.span_type =
"table_cell"`` with ``table_id``/``row``/``col``, ``field_source_node_id`` =
the table's chunk id, ``element_id`` = the table's ``eid-v1`` id, provenance
1.0 (the cell is a real place on the page), ``grounding_quote`` =
``"Header: cell"`` (or ``"Row label — Header: cell"``), ``grounding_source =
grounding_model = "table"``. Only fields the label pass left ``None`` are
offered (``fill_from_tables``), and the pass runs before the grounded LLM
pass so the model is asked about fewer fields.

Table identity: ``table_id = "tbl-" + derive_element_id(chunk_id, page, bbox,
grid text)`` — the ``eid-v1`` derivation over what the document fixes, so
two ingests of the same bytes name the same table.

Tables reach the extraction step through ``ExtractionScope`` (a
``contextvars`` scope ``v1_orchestrator.build_report`` opens around the
report build) because ``extract_segment_fields`` receives texts and layout,
not the bundle; a caller may also pass ``tables=`` explicitly. Outside a
scope the pass reports ``not_run`` with that reason.
"""

from __future__ import annotations

import contextvars
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

try:
    from . import field_extractor as fx
except ImportError:  # pragma: no cover - flat-import fallback
    import field_extractor as fx  # type: ignore

#: Field types a table cell may fill; text/name values in tables are row
#: labels and operation words, not field values.
TABLE_FIELD_TYPES = ("money", "number", "date")
#: Confidence factor for a column pick among disagreeing rows (see module doc).
TABLE_AMBIGUOUS_FACTOR = 0.85
#: Row-height factor for the estimated table height: the bench generator draws
#: 16 pt rows for an 8.5 pt font (1.88); jdf-cli emits no ``height``.
TABLE_ROW_HEIGHT_FACTOR = 1.9
TABLE_QUALITY_STATUSES = ("ok", "irregular_rows", "no_headers", "unreadable")
#: Share of empty cells above which ``many_empty_cells`` is flagged.
EMPTY_CELL_SHARE = 0.3
#: Headers under which a row-label match takes its money / number / date value.
VALUE_HEADERS: dict[str, tuple[str, ...]] = {
    "money": ("limit", "limits", "limit of liability", "amount", "amounts", "coverage limit", "coverage amount",
              "value", "total", "sum insured", "each occurrence", "per occurrence"),
    "number": ("hours", "quantity", "qty", "count", "number", "units", "rate", "percent", "%"),
    "date": ("date", "effective", "effective date", "expiration", "expiration date", "from", "to"),
}
_TOTAL_ROW_RE = re.compile(r"^(?:grand\s+|sub\s*|net\s+)?totals?\b", re.I)
_PICKS = ("total_row", "single_row", "unanimous", "first_row", "row_label")


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------

@dataclass
class ExtractionScope:
    """What one report build shares with its extraction segments: the tables
    collected from the bundle and the label/value pairs discovery found
    (``services/field_discovery``), so ``build_report`` can put both on the
    report after ``_build_report_timed`` returns."""

    tables: list[dict[str, Any]]
    discovered: list[dict[str, Any]] = field(default_factory=list)
    discovery: dict[str, Any] | None = None
    tables_stats: dict[str, Any] | None = None


_SCOPE: contextvars.ContextVar[ExtractionScope | None] = contextvars.ContextVar("parsure_extraction_scope", default=None)


@contextmanager
def extraction_scope(tables: list[dict[str, Any]] | None):
    scope = ExtractionScope(tables=list(tables or []))
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def current_scope() -> ExtractionScope | None:
    return _SCOPE.get()


def current_tables() -> list[dict[str, Any]] | None:
    scope = _SCOPE.get()
    return scope.tables if scope is not None else None


# --------------------------------------------------------------------------
# Collecting tables from a bundle
# --------------------------------------------------------------------------

def _cell(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split())


def _normalize_grid(headers: list, rows: list) -> tuple[list[str], list[list[str]], dict[str, Any]]:
    """Pad to one width, merge phantom blank-header columns into their left
    neighbour, drop all-blank columns. Returns the grid and what was done."""
    headers = [_cell(h) for h in (headers or [])]
    rows = [[_cell(c) for c in (row if isinstance(row, (list, tuple)) else [row])] for row in (rows or [])]
    width = max([len(headers)] + [len(r) for r in rows]) if (headers or rows) else 0
    headers = headers + [""] * (width - len(headers))
    rows = [r + [""] * (width - len(r)) for r in rows]
    merged = 0
    dropped = 0
    keep = [True] * width
    for j in range(width):
        if headers[j] or not any(r[j] for r in rows):
            continue
        # blank header with content: fold into the nearest kept non-blank header on the left whose column is empty wherever this one is filled
        k = j - 1
        while k >= 0 and (not keep[k] or not headers[k]):
            k -= 1
        if k >= 0 and all(not r[k] for r in rows if r[j]):
            for r in rows:
                if r[j]:
                    r[k] = r[j]
                    r[j] = ""
            merged += 1
    for j in range(width):
        if not headers[j] and not any(r[j] for r in rows):
            keep[j] = False
            dropped += 1
    headers = [h for h, k in zip(headers, keep) if k]
    rows = [[c for c, k in zip(r, keep) if k] for r in rows]
    repair = {
        "columns_in": width, "columns_out": len(headers), "columns_merged": merged, "columns_dropped": dropped,
        "basis": ("jdf-cli grid taken as emitted" if not (merged or dropped)
                  else f"{merged} blank-header column{'s' if merged != 1 else ''} folded into the header on the left, "
                       f"{dropped} all-blank column{'s' if dropped != 1 else ''} dropped (ruled-table phantom columns, jdf-cli 0.2.3)"),
    }
    return headers, rows, repair


def _grid_text(headers: list[str], rows: list[list[str]]) -> str:
    return "\n".join([" | ".join(headers)] + [" | ".join(r) for r in rows])


def assess_table_quality(table: dict[str, Any]) -> dict[str, Any]:
    """Plan Part 3.3: ``{"status", "basis", "score", "issues", "header_count",
    "row_count", "cell_count", "empty_cells"}``. ``status`` is one word a
    reviewer can act on — ``unreadable`` (no rows, or every cell blank),
    ``no_headers`` (rows but no header text), ``irregular_rows`` (rows of
    different length: merged or split cells), else ``ok``; ``issues`` keeps
    every finding including ``many_empty_cells``, and ``score`` is the plan's
    1.0 − 0.3/0.2/0.2 arithmetic, stated so it is not mistaken for a
    probability."""
    headers = [str(h) for h in (table.get("headers") or [])]
    rows = [list(r) for r in (table.get("rows") or [])]
    issues: list[str] = []
    score = 1.0
    total_cells = sum(len(r) for r in rows)
    empty_cells = sum(1 for r in rows for c in r if not str(c or "").strip())
    if not any(h.strip() for h in headers):
        issues.append("no_headers")
        score -= 0.3
    lengths = {len(r) for r in rows}
    if len(lengths) > 1:
        issues.append("irregular_rows")
        score -= 0.2
    if total_cells and empty_cells / total_cells > EMPTY_CELL_SHARE:
        issues.append("many_empty_cells")
        score -= 0.2
    if not rows or (total_cells and empty_cells == total_cells):
        status, basis = "unreadable", ("no rows" if not rows else "every cell is blank")
        score = 0.0
    elif "irregular_rows" in issues:
        status = "irregular_rows"
        basis = f"row lengths differ ({', '.join(str(n) for n in sorted(lengths))} cells): merged or split cells"
    elif "no_headers" in issues:
        status, basis = "no_headers", "no header text: columns cannot be matched to field labels"
    else:
        status = "ok"
        basis = f"{len(headers)} headers, {len(rows)} rows, {total_cells} cells" + (f", {empty_cells} blank" if empty_cells else "")
        if "many_empty_cells" in issues:
            basis += f" ({empty_cells / total_cells:.0%} blank)"
    return {"status": status, "basis": basis, "score": round(max(0.0, score), 2), "issues": issues, "header_count": len(headers),
            "row_count": len(rows), "cell_count": total_cells, "empty_cells": empty_cells}


def _table_bbox(el: dict, page: dict, n_rows: int) -> tuple[list[float] | None, str | None]:
    """Relative bbox of a jdf-cli table element. x/y/width are the element's own;
    the height is estimated from the row count and font size because jdf-cli
    emits none — said so in the returned basis."""
    box = fx._element_bbox(el, page)
    if box is None:
        return None, None
    size = page.get("pageSize") or page.get("size") or {}
    ph = size.get("height")
    font = (el.get("style") or {}).get("fontSize") if isinstance(el.get("style"), dict) else None
    font_pt = float(font) if isinstance(font, (int, float)) else 11.0
    if isinstance(el.get("height"), (int, float)) and isinstance(ph, (int, float)) and ph > 0:
        return box, "position/width/height from jdf-cli"
    if not (isinstance(ph, (int, float)) and ph > 0):
        return box, "position/width from jdf-cli; height unknown"
    est_mm = (n_rows + 1) * font_pt * 0.3528 * TABLE_ROW_HEIGHT_FACTOR
    y1 = round(max(0.0, min(1.0, box[1] + est_mm / float(ph))), 4)
    return [box[0], box[1], box[2], y1], f"position/width from jdf-cli; height estimated as ({n_rows} rows + header) × {font_pt:g} pt × {TABLE_ROW_HEIGHT_FACTOR}"


def _table_chunks_by_page(bundle: dict) -> dict[int, list[dict]]:
    """``types: ["table"]`` chunks per page, in chunk order — the table's
    node address (``p1e2``) and the flattened text jdf-cli made of it."""
    by_page: dict[int, list[dict]] = {}
    for chunk in bundle.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        types = [str(t).lower() for t in (chunk.get("types") or []) if isinstance(t, (str, int))]
        if "table" not in types:
            continue
        try:
            page_no = int(chunk.get("page") or 0)
        except (TypeError, ValueError):
            page_no = 0
        by_page.setdefault(page_no, []).append(chunk)
    return by_page


def _make_table(*, headers: list, rows: list, page: int, chunk_id: str | None, node_id: str | None, bbox: list[float] | None,
                bbox_basis: str | None, caption: str | None, source: str) -> dict[str, Any]:
    headers_n, rows_n, repair = _normalize_grid(headers, rows)
    text = _grid_text(headers_n, rows_n)
    element_id = fx.derive_element_id(chunk_id, page, bbox, text)
    table = {
        "table_id": f"tbl-{element_id}",
        "node_id": node_id,
        "chunk_id": chunk_id,
        "element_id": element_id,
        "page": page,
        "bbox": bbox,
        "bbox_basis": bbox_basis,
        "headers": headers_n,
        "rows": rows_n,
        "caption": caption or None,
        "source": source,
        "layout_repair": repair,
        "id_policy": fx.NODE_ID_POLICY,
    }
    table["quality"] = assess_table_quality(table)
    return table


def collect_tables(bundle: dict, layout: list[list[dict]] | None = None) -> list[dict[str, Any]]:
    """Every table of a parse bundle, in page order, in the shape the report
    publishes (``report["tables"]``). Reads the jdf-cli page elements
    (``type: "table"``) first, else the Assure tree's table nodes (``type:
    "table"`` under ``body[].children``). ``layout`` is accepted for symmetry
    with the other passes; nothing here depends on it (the table element is
    not a layout segment — that is the reason this module exists)."""
    del layout
    if not isinstance(bundle, dict):
        return []
    # The ingest path replaces ``bundle["jdf"]`` with the saved tree and keeps
    # the jdf-cli document under ``jdf_source`` (services/pdf_ingest); the
    # element grid is the measured source, the tree node a copy of it.
    jdf = bundle.get("jdf_source") if isinstance(bundle.get("jdf_source"), dict) else bundle.get("jdf")
    tables: list[dict[str, Any]] = []
    if not isinstance(jdf, dict):
        return tables
    pages = jdf.get("pages")
    if isinstance(pages, list) and not isinstance(jdf.get("body"), list):
        chunks_by_page = _table_chunks_by_page(bundle)
        for idx, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            page_no = idx + 1
            page_chunks = list(chunks_by_page.get(page_no) or [])
            k = 0
            for el in fx._walk_elements(page.get("elements")):
                if str(el.get("type") or "").lower() != "table":
                    continue
                rows = el.get("rows") if isinstance(el.get("rows"), list) else []
                headers = el.get("headers") if isinstance(el.get("headers"), list) else []
                chunk = page_chunks[k] if k < len(page_chunks) else None
                k += 1
                chunk_id = str(chunk.get("id")) if chunk and chunk.get("id") else None
                node_id = str(el.get("id")) if el.get("id") else chunk_id
                bbox, basis = _table_bbox(el, page, len(rows))
                tables.append(_make_table(headers=headers, rows=rows, page=page_no, chunk_id=chunk_id, node_id=node_id, bbox=bbox,
                                          bbox_basis=basis, caption=el.get("caption"), source="jdf_element"))
        return tables
    if isinstance(jdf.get("body"), list):
        for section in jdf["body"]:
            if not isinstance(section, dict):
                continue
            smeta = section.get("meta") if isinstance(section.get("meta"), dict) else {}
            try:
                page_no = int(smeta.get("source_page") or 0)
            except (TypeError, ValueError):
                page_no = 0
            stack = list(section.get("children") or [])
            while stack:
                node = stack.pop(0)
                if not isinstance(node, dict):
                    continue
                if node.get("type") == "table":
                    nmeta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
                    chunk_id = str(nmeta.get("chunk_id")) if nmeta.get("chunk_id") else None
                    tables.append(_make_table(headers=node.get("headers") or [], rows=node.get("rows") or [], page=page_no or 1,
                                              chunk_id=chunk_id, node_id=str(node.get("id")) if node.get("id") else chunk_id,
                                              bbox=None, bbox_basis=None, caption=node.get("caption"), source="tree_node"))
                stack = list(node.get("children") or []) + stack
    return tables


# --------------------------------------------------------------------------
# Matching headers and row labels to field anchors
# --------------------------------------------------------------------------

_HEADER_NOISE_RE = re.compile(r"\([^)]*\)|[$:#*]|\busd\b")
_SEPARATORS_RE = re.compile(r"[·•—–/|]")


def _norm_label(text: str) -> str:
    s = _SEPARATORS_RE.sub(" ", str(text or "").lower())
    s = _HEADER_NOISE_RE.sub(" ", s)
    return " ".join(s.split())


def _header_matches(spec: fx.FieldSpec, header: str) -> bool:
    h = _norm_label(header)
    if not h:
        return False
    for anchor in spec.anchors:
        try:
            if re.fullmatch(anchor + r"(?:\s+(?:amount|limit|limits|\$|usd))?", h, re.I):
                return True
        except re.error:
            continue
    return False


def _row_label_matches(spec: fx.FieldSpec, label: str) -> bool:
    lab = _norm_label(label)
    if not lab:
        return False
    for anchor in spec.anchors:
        try:
            if re.search(r"(?<![a-z])" + anchor + r"(?![a-z])", lab, re.I):
                return True
        except re.error:
            continue
    return False


def _parse(spec: fx.FieldSpec, raw: str) -> Any:
    if not raw or not raw.strip():
        return None
    if spec.field_type == "money":
        return fx._parse_money(raw)
    if spec.field_type == "number":
        return fx._parse_number(raw)
    if spec.field_type == "date":
        return fx._parse_date(raw)
    return None


def _valid(spec: fx.FieldSpec, raw: str) -> Any:
    parsed = _parse(spec, raw)
    if parsed is None:
        return None
    return parsed if fx.value_shape(spec, raw, parsed)["quality"] == "valid" else None


def _row_label(row: list[str]) -> str:
    return row[0] if row else ""


def _find_cell(spec: fx.FieldSpec, table: dict[str, Any]) -> dict[str, Any] | None:
    """``{"row", "col", "raw", "value", "pick", "basis"}`` for the cell of
    ``table`` that answers ``spec``, or None. See the module doc for the pick
    rules; the basis is the sentence the field's evidence carries."""
    headers: list[str] = table.get("headers") or []
    rows: list[list[str]] = table.get("rows") or []
    if not rows:
        return None
    # 1. column match
    for col, header in enumerate(headers):
        if not _header_matches(spec, header):
            continue
        parsed = [(i, r[col], _valid(spec, r[col])) for i, r in enumerate(rows) if col < len(r)]
        parsed = [(i, raw, v) for i, raw, v in parsed if v is not None]
        if not parsed:
            continue
        total = next(((i, raw, v) for i, raw, v in parsed if _TOTAL_ROW_RE.match(_row_label(rows[i]))), None)
        if total is not None:
            i, raw, v = total
            return {"row": i, "col": col, "raw": raw, "value": v, "pick": "total_row",
                    "basis": f"column '{header}' matched the {spec.label} label; the '{_row_label(rows[i])}' row is the total"}
        if len(parsed) == 1:
            i, raw, v = parsed[0]
            return {"row": i, "col": col, "raw": raw, "value": v, "pick": "single_row",
                    "basis": f"column '{header}' matched the {spec.label} label; one row carries a value"}
        if len({repr(v) for _, _, v in parsed}) == 1:
            i, raw, v = parsed[0]
            return {"row": i, "col": col, "raw": raw, "value": v, "pick": "unanimous",
                    "basis": f"column '{header}' matched the {spec.label} label; the same value in all {len(parsed)} rows"}
        i, raw, v = parsed[0]
        return {"row": i, "col": col, "raw": raw, "value": v, "pick": "first_row",
                "basis": f"column '{header}' matched the {spec.label} label; {len(parsed)} rows carry different values and no total row — "
                         f"first row '{_row_label(rows[i])}' taken, confidence × {TABLE_AMBIGUOUS_FACTOR:.2f}"}
    # 2. row-label match
    for i, row in enumerate(rows):
        label = _row_label(row)
        if not _row_label_matches(spec, label):
            continue
        col_order: list[tuple[int, str]] = []
        for col, header in enumerate(headers):
            if col == 0 or col >= len(row):
                continue
            if _header_matches(spec, header):
                col_order.append((col, f"header '{header}' matches the {spec.label} label"))
        for col, header in enumerate(headers):
            if col == 0 or col >= len(row) or any(c == col for c, _ in col_order):
                continue
            if _norm_label(header) in VALUE_HEADERS.get(spec.field_type, ()):
                col_order.append((col, f"header '{header}' names a {spec.field_type} column"))
        for col in range(1, len(row)):
            if not any(c == col for c, _ in col_order):
                col_order.append((col, f"first cell in the row that reads as a {spec.field_type} value (no matching header)"))
        for col, why in col_order:
            v = _valid(spec, row[col])
            if v is not None:
                return {"row": i, "col": col, "raw": row[col], "value": v, "pick": "row_label",
                        "basis": f"row '{label}' matched the {spec.label} label; {why}"}
    return None


# --------------------------------------------------------------------------
# Building the field record
# --------------------------------------------------------------------------

def build_table_field(
    spec: fx.FieldSpec,
    table: dict[str, Any],
    cell: dict[str, Any],
    *,
    parser_name: str | None,
    parse_confidence: float | None,
    ocr_confidence: float | None,
    page_quality: list[float | None] | None,
    visual_pages: list[dict | None] | None,
) -> dict[str, Any]:
    """The contract record for a table cell — ``field_extractor.
    build_found_field``'s shape built from ``_empty_field`` because the cell
    has no character offsets on the page (see module doc)."""
    if parse_confidence is None and ocr_confidence is not None:
        parse_confidence = float(ocr_confidence)
    page = int(table.get("page") or 1)
    page_index = page - 1
    pq_list = list(page_quality or [])
    pq = pq_list[page_index] if 0 <= page_index < len(pq_list) else None
    headers = table.get("headers") or []
    header = headers[cell["col"]] if cell["col"] < len(headers) else ""
    row_label = _row_label((table.get("rows") or [])[cell["row"]]) if cell["row"] < len(table.get("rows") or []) else ""
    raw = str(cell["raw"]).strip()
    field_rec = fx._empty_field(spec)
    field_rec["raw"] = raw
    field_rec["value"] = cell["value"]
    field_rec["extraction_method"] = "table"
    field_rec["field_state"], field_rec["routing_action"], field_rec["review_required"] = "unverified", "manual_review", True
    field_rec["reason"] = None
    field_rec["value_quality"] = fx.value_shape(spec, raw, cell["value"])
    field_rec["provenance_confidence"] = 1.0
    field_rec["quality_source"] = "page_quality"
    field_rec["local_quality"] = None
    span: dict[str, Any] = {
        "page": page, "span_type": "table_cell", "table_id": table["table_id"], "row": cell["row"], "col": cell["col"],
        "element_id": table.get("element_id"), "node_id": table.get("node_id"), "header": header, "row_label": row_label or None,
    }
    if table.get("bbox"):
        span["bbox"] = table["bbox"]
    field_rec["source_span"] = span
    field_rec["field_source_node_id"] = table.get("node_id")
    field_rec["element_id"] = table.get("element_id")
    field_rec["evidence"] = {
        "kind": "found", "page": page, "node_id": table.get("node_id"), "element_id": table.get("element_id"), "method": "table",
        "table_id": table["table_id"], "row": cell["row"], "col": cell["col"], "header": header, "row_label": row_label or None,
        "pick": cell["pick"], "basis": cell["basis"], "table_quality": (table.get("quality") or {}).get("status"),
    }
    quote = f"{header}: {raw}" if header else raw
    if cell["pick"] == "row_label" and row_label:
        quote = f"{row_label} — {quote}"
    field_rec["grounding_quote"] = quote
    field_rec["grounding_span"] = {"page": page, "table_id": table["table_id"], "row": cell["row"], "col": cell["col"],
                                   "element_id": table.get("element_id"), "node_id": table.get("node_id")}
    field_rec["grounding_model"] = "table"
    field_rec["grounding_source"] = "table"
    visual = (visual_pages or [None] * (page_index + 1))[page_index] if 0 <= page_index < len(visual_pages or []) else None
    handwritten = bool(visual and "handwritten" in (visual.get("flags") or []))
    number_quality = None
    if spec.field_type in ("money", "number"):
        nq = fx.assess_number(raw, ocr_confidence=ocr_confidence, page_quality=pq, handwritten=handwritten)
        field_rec["number_quality"] = nq
        number_quality = nq.get("quality")
    conf, basis = fx.quality_weighted_confidence(parser_confidence=parse_confidence, parser_name=parser_name, page_quality=pq,
                                                 number_quality=number_quality, quality_label="page_quality")
    if cell["pick"] == "first_row":
        conf = round(conf * TABLE_AMBIGUOUS_FACTOR, 4)
        basis = f"{basis.rsplit(' = ', 1)[0]} × table_ambiguous_row ({TABLE_AMBIGUOUS_FACTOR:.2f}) = {conf:.2f}"
    field_rec["extraction_confidence"] = conf
    field_rec["confidence_basis"] = basis
    if field_rec.get("number_quality") and field_rec["number_quality"].get("review_required"):
        field_rec["reason"] = f"number_quality {field_rec['number_quality']['quality']}: {field_rec['number_quality'].get('basis', '')}"
    fx.mark_low_quality_page(field_rec, pq_list)
    return field_rec


def extract_table_fields(
    document_type: str,
    tables: list[dict[str, Any]],
    specs: list[fx.FieldSpec],
    *,
    parser_name: str | None = None,
    parse_confidence: float | None = None,
    ocr_confidence: float | None = None,
    page_quality: list[float | None] | None = None,
    visual_pages: list[dict | None] | None = None,
) -> list[dict[str, Any]]:
    """One field record per spec in ``specs`` that a table answers (specs with
    no matching cell are omitted — the caller keeps its own record for them).
    Tables are tried in page order; the first cell found wins. ``document_type``
    is carried for the note text only: the anchors are the specs'."""
    del document_type
    out: list[dict[str, Any]] = []
    for spec in specs:
        if spec.field_type not in TABLE_FIELD_TYPES:
            continue
        for table in tables:
            if (table.get("quality") or {}).get("status") == "unreadable":
                continue
            cell = _find_cell(spec, table)
            if cell is None:
                continue
            out.append(build_table_field(spec, table, cell, parser_name=parser_name, parse_confidence=parse_confidence,
                                         ocr_confidence=ocr_confidence, page_quality=page_quality, visual_pages=visual_pages))
            break
    return out


def _not_run(reason: str, tables: int = 0) -> dict[str, Any]:
    return {"status": "not_run", "tables": tables, "fields_offered": 0, "fields_from_tables": 0, "fields_filled": [], "reason": reason}


def fill_from_tables(
    document_type: str,
    fields: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    *,
    parser_name: str | None = None,
    parse_confidence: float | None = None,
    ocr_confidence: float | None = None,
    page_quality: list[float | None] | None = None,
    visual_pages: list[dict | None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replace, in place and in order, the ``value is None`` money/number/date
    fields of ``fields`` with what the tables answer. Returns the list and the
    ``execution.tables`` block: ``{"status": "completed"|"not_run", "tables",
    "fields_offered", "fields_from_tables", "fields_filled", "reason"}``."""
    if not tables:
        return fields, _not_run("no table elements in the document")
    specs = {s.name: s for s in fx.FIELD_TAXONOMY.get(document_type) or []}
    offered = [specs[f["name"]] for f in fields
               if f.get("value") is None and f.get("name") in specs and specs[f["name"]].field_type in TABLE_FIELD_TYPES]
    stats: dict[str, Any] = {"status": "completed", "tables": len(tables), "fields_offered": len(offered), "fields_from_tables": 0,
                             "fields_filled": [], "reason": None}
    if not offered:
        stats["reason"] = ("every table-eligible field already had a value" if specs
                           else f"no schema fields for {document_type}: nothing to match against table headers")
        return fields, stats
    found = extract_table_fields(document_type, tables, offered, parser_name=parser_name, parse_confidence=parse_confidence,
                                 ocr_confidence=ocr_confidence, page_quality=page_quality, visual_pages=visual_pages)
    by_name = {f["name"]: f for f in found}
    for idx, f in enumerate(fields):
        rep = by_name.get(f.get("name"))
        if rep is not None and f.get("value") is None:
            fields[idx] = rep
            stats["fields_filled"].append(f["name"])
    stats["fields_from_tables"] = len(stats["fields_filled"])
    if not stats["fields_from_tables"]:
        stats["reason"] = f"{len(offered)} field(s) offered; no table header or row label matched their anchors"
    return fields, stats


def _pages_with_text(texts: list[str] | None) -> set[int] | None:
    if not texts:
        return None
    pages = {i + 1 for i, t in enumerate(texts) if (t or "").strip()}
    return pages or None


def run_table_pass(
    document_type: str,
    fields: list[dict[str, Any]],
    *,
    texts: list[str] | None,
    tables: list[dict[str, Any]] | None = None,
    execution: dict[str, Any] | None = None,
    notes: list[str],
    parser_name: str | None,
    parse_confidence: float | None,
    ocr_confidence: float | None,
    page_quality: list[float | None] | None,
    visual_pages: list[dict | None] | None,
) -> list[dict[str, Any]]:
    """The hook ``v1_orchestrator.extract_segment_fields`` calls between the
    label pass and the model pass. ``tables`` defaults to the current
    ``extraction_scope``; the tables tried are those on pages that carry text
    in ``texts`` (a mixed bundle blanks the other segments' pages). Writes
    ``execution["tables"]`` (summed over segments) and one note when a field
    was filled. Never raises."""
    try:
        source = tables if tables is not None else current_tables()
        if source is None:
            stats = _not_run("no extraction scope on this path (direct call without tables)")
            fields_out = fields
        else:
            pages = _pages_with_text(texts)
            use = [t for t in source if pages is None or int(t.get("page") or 0) in pages]
            fields_out, stats = fill_from_tables(document_type, fields, use, parser_name=parser_name, parse_confidence=parse_confidence,
                                                 ocr_confidence=ocr_confidence, page_quality=page_quality, visual_pages=visual_pages)
            if source and not use:
                stats = _not_run("the bundle's tables are on pages outside this segment", tables=0)
    except Exception as exc:  # noqa: BLE001 — advisory pass; the label pass result stands
        stats = {"status": "failed", "tables": len(tables or current_tables() or []), "fields_offered": 0, "fields_from_tables": 0,
                 "fields_filled": [], "reason": f"{type(exc).__name__}: {exc}"[:200]}
        fields_out = fields
        notes.append(f"table pass failed: {stats['reason']}")
    if stats.get("fields_from_tables"):
        notes.append(f"table pass: {stats['fields_from_tables']} field(s) read from {stats['tables']} table(s): {', '.join(stats['fields_filled'])}")
    scope = current_scope()
    for sink, key in ((execution, "tables"), (scope.__dict__ if scope is not None else None, "tables_stats")):
        if sink is None:
            continue
        sink[key] = _merge_stats(sink.get(key), stats)
    return fields_out


def _merge_stats(prev: Any, stats: dict[str, Any]) -> dict[str, Any]:
    """Sum a segment's pass over the previous segments' (a mixed bundle runs
    one pass per document); a failure never hides an earlier completed pass."""
    if not (isinstance(prev, dict) and prev.get("status") in ("completed", "failed") and stats.get("status") != "failed"):
        return stats
    merged = dict(prev)
    merged["fields_offered"] = int(prev.get("fields_offered") or 0) + int(stats.get("fields_offered") or 0)
    merged["fields_filled"] = list(prev.get("fields_filled") or []) + list(stats.get("fields_filled") or [])
    merged["fields_from_tables"] = len(merged["fields_filled"])
    merged["tables"] = max(int(prev.get("tables") or 0), int(stats.get("tables") or 0))
    merged["status"] = "completed"
    merged["reason"] = None if merged["fields_from_tables"] else (stats.get("reason") or prev.get("reason"))
    return merged


def annotate_report(report: dict[str, Any], scope: ExtractionScope | None, *, replace: bool = False) -> None:
    """``report["tables"]`` and ``report["discovered_fields"]`` from the scope.
    ``execution.tables`` / ``execution.discovery`` are ``setdefault``-ed (the
    build owns ``report["execution"]`` and the segments already wrote their
    stats there); ``replace=True`` — a re-extraction — overwrites them with
    this run's stats so a stale block from the first run cannot survive."""
    tables = list(scope.tables) if scope is not None else []
    report["tables"] = tables
    report["discovered_fields"] = list(scope.discovered) if scope is not None else []
    execution = report.setdefault("execution", {})
    tables_block = (scope.tables_stats if scope is not None and scope.tables_stats else None) or _not_run("no extraction segment ran the table pass", tables=len(tables))
    discovery_block = (scope.discovery if scope is not None and scope.discovery else None) or {"status": "not_run", "pairs": 0, "reason": "typed document — taxonomy fields apply"}
    if replace:
        execution["tables"] = tables_block
        execution["discovery"] = discovery_block
    else:
        execution.setdefault("tables", tables_block)
        execution.setdefault("discovery", discovery_block)
