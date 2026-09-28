"""What a compile actually carries of the sources it was handed.

The prompt cannot hold every attached source: the numbering walk stops at the first
source whose block would push the pasted context past
``SUBSTRATE_CONTEXT_CHARS_TOTAL``, and it cuts a single source at
``SUBSTRATE_CONTEXT_CHARS_PER_FILE``. Neither cut was recorded anywhere, so nothing
downstream could tell an attached source from a carried one: measured on
``fv-v3-twenty-1789808891-21a860``, 24 sources (480,000 characters) were attached,
18 reached the model, and the export's source manifest listed all 24 as included
with a hash of the full text — the dossier claiming coverage the compile never had.

This module owns the numbering walk (``numbered_source_blocks``) so there is one
walk and not two: the prompt, the sentence map, the citation resolver and this
report all read the same function, and a cited id cannot drift from the sentence
the model was shown. ``carry_plan`` is the record: per source, whether it was
carried, how many of its characters reached the model, which ``[S<n>]`` ids it was
given, and why it was dropped when it was.

Every block is emitted inside the untrusted delimiter (``_source_block``), whether
or not the ingest scan flagged it: the framing is a property of where the text came
from, and gating it on the phrase list handed a reworded instruction to the model as
ordinary material. The source is defused before it is numbered, so the fence cannot
be closed from inside it and the sentences the map holds are the sentences the model
was shown. A compile persists it beside its gate;
the export reads it, and falls back to recomputing it (flagged ``derived``) for
documents compiled before the record existed.

Forms (2026-09-28). A filled form's text is field captions, not sentences a draft
can cite, so the compile guard refused every CMS-1500 even when Parsure had read
its fields with verbatim grounding quotes (demo measurement 2026-09-27: two forms
refused, both with a field report). ``attach_form_fields`` looks the source's
report up and, when the report or ``compile_guard.looks_like_form`` says form,
the walk appends one synthetic sentence per found field — ``"<Label>: <value as
written>"``, built only from the field's ``raw`` / ``grounding_quote`` — after the
source's own sentences, under the header ``FORM_FIELDS_HEADER`` and inside the same
fence. Each carries its own ``[S<n>]`` id and a ``parsure_field`` provenance
(field, element id, page, bbox, the verbatim quote), so a citation to it anchors
to the field and the claim block's quote and page are the field's. The parsed or
normalised value is never used: ``"2025-01-15"`` is Parsure's reading, and only
``"01/15/2025"`` is on the page.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Iterator

try:
    from ..models.jdf import _merge_short_sentences, _split_sentences
    from .compile_guard import _defuse_delimiter, wrap_untrusted_source
except ImportError:
    from models.jdf import _merge_short_sentences, _split_sentences
    from compile_guard import _defuse_delimiter, wrap_untrusted_source

#: Characters of one source that may reach the prompt. A source longer than this
#: is numbered up to the cap and reported ``truncated``.
SUBSTRATE_CONTEXT_CHARS_PER_FILE = 200_000

#: Characters of pasted source the whole prompt may carry. The walk stops at the
#: first source that would exceed it, which is why a source can be dropped while
#: budget remains for a smaller one later in the order — the walk is a prefix, and
#: changing it to skip-and-continue would change every compile's prompt, so it is
#: reported rather than altered here.
SUBSTRATE_CONTEXT_CHARS_TOTAL = 400_000

_NO_TEXT_REASON = "the source has no extracted text"
_PER_FILE_REASON = f"the per-file cap ({SUBSTRATE_CONTEXT_CHARS_PER_FILE:,} characters) was reached"
_TOTAL_CAP_REASON = (
    f"the context cap ({SUBSTRATE_CONTEXT_CHARS_TOTAL:,} characters) was reached before this "
    "source, and the compile stops there"
)
NOT_ATTACHED_REASON = "not attached to the compile this document came from"

#: The line that opens the field sentences inside a form-like source's block. It
#: does not begin with ``[S`` — that prefix is the citation namespace.
FORM_FIELDS_HEADER = "Fields read from the form (Parsure):"

#: Parsure evidence states whose value was located on the page. ``found_suspect``
#: is excluded on purpose: its ``raw`` is debris the extractor flagged
#: ("~~" under "Policy Number" on the 2026-09-28 live report), not a value.
FORM_FIELD_FOUND_STATES = ("found_verified", "found_unverified")

#: Parsure report flag that names an (un)filled form (``v1_orchestrator.form_template_flag``).
FORM_TEMPLATE_FLAG = "form_template"


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _source_block(
    filename: str,
    lines: list[str],
    *,
    truncated: bool = False,
    original_length: int = 0,
    carried_chars: int = 0,
) -> str:
    """The prompt block for one source: its numbered sentences, fenced as untrusted.

    Every source is fenced, not only a scanned one. The fence is a property of
    where the text came from — a source that matches none of the eight phrases is
    still text the user did not type — and gating it on the phrase list meant a
    reworded instruction reached the model as ordinary material with no framing at
    all, which is the case the scan misses by construction.

    The fence wraps the *block*, never a numbered sentence: a marker inside the
    numbered run would be numbered with it, and from there reach the sentence map,
    the Evidence pane's quote, the entailment claim and the export. The source is
    defused before it is split (``_defused_text``), so the body here carries no
    close marker of its own and the fence cannot be closed from inside.

    Both walks build the block through this one function, so the block the prompt
    carries is the block the carry plan measured.

    A truncated source says so, inside the fence and after the last numbered
    sentence. Measured before this: a 20,000-sentence policy cut at the 200,000
    character cap reached the model as 4,595 numbered sentences ending in a clean
    fence marker, with nothing anywhere in the block indicating that the document
    continued — which is the one condition under which a model asked about the
    missing part has no choice but to invent it. The notice names the cut, so a
    claim the source cannot support reads as "beyond what was carried" rather
    than as silence.
    """
    body = "\n".join(lines)
    if truncated:
        # Deliberately does not begin with "[S" — that prefix is the citation
        # namespace, and a notice opening with it is indistinguishable from a
        # numbered sentence to anything scanning for ids.
        notice = (
            f"--- SOURCE TRUNCATED: the last sentence carried is the {len(lines)}th "
            f"and the file continues. {carried_chars:,} of {original_length:,} "
            f"characters were carried. Do not answer from the part that was not "
            f"carried: if the ask concerns it, say the source continues beyond "
            f"what was provided. ---"
        )
        body = f"{body}\n\n{notice}"
    return f"### Source file: {filename}\n" + wrap_untrusted_source(body)


def _defused_text(text: str) -> str:
    """Source text that cannot close the fence it is about to be wrapped in.

    ``_defuse_delimiter`` is applied to the source itself rather than to the
    assembled block, so the text the walk numbers is the text the model is shown:
    defusing afterwards would edit the sentences after their ids were assigned and
    put the map and the prompt out of step.
    """
    return _defuse_delimiter(text)


def _field_label(field: dict[str, Any]) -> str:
    label = str(field.get("label") or "").strip()
    if label:
        return label
    return str(field.get("name") or "field").replace("_", " ").strip().capitalize()


def form_field_sentence(field: dict[str, Any], report_id: str | None = None) -> tuple[str, dict[str, Any]] | None:
    """``(sentence, provenance)`` for one found Parsure field, or None.

    The sentence is ``"<Label>: <value as written>"``; the value is the field's
    ``raw`` (the text read under the label) or, failing that, its
    ``grounding_quote`` (the page line the value was found on). Both are
    verbatim page text by Parsure's contract (``field_extractor.attach_grounding``,
    ``llm_extraction`` quote gate). ``value`` — the parsed, normalised reading —
    is never used, so a date reads ``01/15/2025`` as the page has it and not
    ``2025-01-15``. A field whose evidence state is not a found state, or that
    carries neither text, yields nothing.
    """
    if not isinstance(field, dict):
        return None
    if str(field.get("evidence_state") or "") not in FORM_FIELD_FOUND_STATES:
        return None
    raw = str(field.get("raw") or "").strip()
    quote = str(field.get("grounding_quote") or "").strip()
    as_written = raw or quote
    if not as_written:
        return None
    span = field.get("source_span") if isinstance(field.get("source_span"), dict) else {}
    gspan = field.get("grounding_span") if isinstance(field.get("grounding_span"), dict) else {}
    page = span.get("page") or gspan.get("page")
    provenance = {
        "kind": "parsure_field",
        "field": str(field.get("name") or ""),
        "element_id": field.get("element_id") or span.get("element_id") or gspan.get("element_id"),
        "page": int(page) if isinstance(page, (int, float)) and page > 0 else None,
        "bbox": span.get("bbox") if isinstance(span.get("bbox"), list) else None,
        "quote": quote or raw,
        "report_id": report_id,
    }
    return f"{_field_label(field)}: {as_written}", provenance


def form_field_sentences(row: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The field sentences of a row ``attach_form_fields`` marked (``parsure_fields``)."""
    fields = row.get("parsure_fields")
    if not isinstance(fields, list) or not fields:
        return []
    report_id = str(row.get("parsure_report_id") or "") or None
    out: list[tuple[str, dict[str, Any]]] = []
    for field in fields:
        item = form_field_sentence(field, report_id)
        if item:
            out.append(item)
    return out


def _report_for_row(row: dict[str, Any], reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest report of this Sources row: by ``document_id`` (the vault row id
    on the Sources path, ``routers/substrate.ingest_substrate_file``), else by
    filename. ``reports`` is newest first."""
    row_id = str(row.get("id") or row.get("source_id") or "")
    if row_id:
        for report in reports:
            if str(report.get("document_id") or "") == row_id:
                return report
    filename = str(row.get("filename") or "")
    if filename:
        for report in reports:
            if str(report.get("filename") or "") == filename:
                return report
    return None


def attach_form_fields(project_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the form-like rows with their Parsure fields, in place; return ``rows``.

    A row is a form when its report's ``quality_flags`` carries
    ``form_template`` or ``compile_guard.looks_like_form`` says so of its text.
    Such a row gets ``parsure_fields`` (the report's fields, filtered by
    ``form_field_sentence`` at numbering time), ``parsure_report_id`` and
    ``parsure_form: True``; a prose row is left untouched, and a form without a
    report keeps only ``parsure_form`` — nothing is invented for it, and the
    guard refuses it as before. One report query per compile, not per row.
    Never raises: a lookup failure leaves the rows as they were, logged.
    """
    if not rows:
        return rows
    try:
        from .compile_guard import looks_like_form
    except ImportError:
        from compile_guard import looks_like_form  # type: ignore
    reports: list[dict[str, Any]] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("extracted_text") or "")
        if reports is None:
            try:
                try:
                    from ..db import parsure_repository as _parsure
                except ImportError:
                    from db import parsure_repository as _parsure  # type: ignore
                reports = _parsure.list_reports(project_id, limit=500, current_only=False) or []
            except Exception:  # noqa: BLE001 — no intake table: the text alone decides
                import logging

                logging.getLogger(__name__).exception("form fields: report lookup failed for %s", project_id)
                reports = []
        report = _report_for_row(row, reports)
        flags = report.get("quality_flags") if isinstance(report, dict) else None
        is_form = (isinstance(flags, list) and FORM_TEMPLATE_FLAG in flags) or looks_like_form([text])
        if not is_form:
            continue
        row["parsure_form"] = True
        if isinstance(report, dict):
            row["parsure_report_id"] = str(report.get("report_id") or "") or None
            row["parsure_fields"] = [f for f in (report.get("fields") or []) if isinstance(f, dict)]
    return rows


def _number_field_lines(
    row: dict[str, Any], n: int, used: int
) -> tuple[list[str], list[tuple[str, str, str, Any, dict[str, Any] | None]], int, int]:
    """The header and ``[S<n>]`` lines for a row's field sentences, within the
    per-file cap. Returns ``(lines, entries, next n, used)``; empty when the row
    carries no field sentence. The sentence is defused like any source text."""
    sentences = form_field_sentences(row)
    if not sentences:
        return [], [], n, used
    filename = str(row.get("filename") or "substrate")
    lines: list[str] = []
    entries: list[tuple[str, str, str, Any, dict[str, Any] | None]] = []
    header_used = used + len(FORM_FIELDS_HEADER) + 1
    for sentence, provenance in sentences:
        clean = _defused_text(sentence).strip()
        if not clean:
            continue
        line = f"[S{n}] {clean}"
        if header_used + len(line) > SUBSTRATE_CONTEXT_CHARS_PER_FILE:
            break
        lines.append(line)
        entries.append((f"S{n}", clean, filename, provenance.get("page"), provenance))
        header_used += len(line) + 1
        n += 1
    if not lines:
        return [], [], n, used
    return ["", FORM_FIELDS_HEADER, *lines], entries, n, header_used


def _walk(substrate_rows: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """One pass over the rows, in order, yielding what the prompt carries of each.

    The shape of the walk is the compile's: a source is numbered until the
    per-file cap, and the walk ends at the first source that would exceed the
    total cap. Rows after that point are yielded as dropped without being
    numbered, so the report can name them, and no id is assigned to a sentence the
    model was not shown.
    """
    total = 0
    n = 1
    stopped = False
    for row in substrate_rows:
        source_id = str(row.get("id") or row.get("source_id") or "")
        filename = str(row.get("filename") or "substrate")
        full_text = str(row.get("extracted_text") or "").strip()
        entry: dict[str, Any] = {
            "source_id": source_id,
            "filename": filename,
            "full_chars": len(full_text),
            "full_text_sha256": _sha256(full_text) if full_text else "",
            "included": False,
            "chars": 0,
            "block_chars": 0,
            "sentences": 0,
            "first_id": "",
            "last_id": "",
            "truncated": False,
            "dropped_reason": "",
            "text_sha256": "",
            "parsure_fields": 0,
        }
        if stopped:
            entry["dropped_reason"] = _TOTAL_CAP_REASON
            yield entry
            continue
        if not full_text:
            entry["dropped_reason"] = _NO_TEXT_REASON
            yield entry
            continue

        text = _defused_text(full_text)
        lines: list[str] = []
        numbered: list[str] = []
        used = 0
        truncated = False
        for sent, _page in _merge_short_sentences(_split_sentences(text)):
            clean = str(sent or "").strip()
            if not clean:
                continue
            line = f"[S{n}] {clean}"
            if used + len(line) > SUBSTRATE_CONTEXT_CHARS_PER_FILE:
                truncated = True
                break
            lines.append(line)
            numbered.append(clean)
            used += len(line) + 1
            n += 1
        if not lines:
            entry["dropped_reason"] = _PER_FILE_REASON if full_text else _NO_TEXT_REASON
            yield entry
            continue
        field_lines, field_entries, n, used = _number_field_lines(row, n, used)
        lines.extend(field_lines)
        numbered.extend(text for _sid, text, _fn, _pg, _prov in field_entries)

        block = _source_block(filename, lines)
        if total + len(block) > SUBSTRATE_CONTEXT_CHARS_TOTAL:
            entry["dropped_reason"] = _TOTAL_CAP_REASON
            stopped = True
            yield entry
            continue

        carried_text = "\n".join(numbered)
        entry.update(
            included=True,
            chars=sum(len(part) for part in numbered),
            block_chars=len(block),
            sentences=len(numbered),
            first_id=f"S{n - len(numbered)}",
            last_id=f"S{n - 1}",
            truncated=truncated,
            dropped_reason=_PER_FILE_REASON if truncated else "",
            text_sha256=_sha256(carried_text),
            parsure_fields=len(field_entries),
        )
        total += len(block)
        yield entry


def numbered_source_blocks(
    substrate_rows: list[dict[str, Any]],
) -> list[tuple[str, list[tuple[str, str, str, Any, dict[str, Any] | None]]]]:
    """The one place the source is numbered.

    Returns ``[(prompt block, [(id, sentence, filename, page, provenance), …]), …]``,
    the block already carrying its ``[S<N>]`` prefixes and already bounded by the
    per-file and total budgets. ``provenance`` is None for a sentence of the
    source's own text and a ``parsure_field`` record (``form_field_sentence``)
    for a field sentence appended to a form-like source. ``_build_substrate_context`` writes the blocks;
    ``build_sentence_map`` reads the entries. Both walk this function, so an id
    the model cites resolves to the sentence it was shown — numbering the whole
    document while the prompt truncates is how a citation lands on the wrong
    sentence, and numbering the whole document also blew the 30k input cap
    (35,382 tokens measured on two policies) because every sentence now carries
    a prefix.

    Ids are assigned in document order and advance across files, so ``S<n>`` is
    stable for a given set of rows.
    """
    out: list[tuple[str, list[tuple[str, str, str, Any, dict[str, Any] | None]]]] = []
    total = 0
    n = 1
    for row in substrate_rows:
        text = str(row.get("extracted_text") or "").strip()
        if not text:
            continue
        filename = str(row.get("filename") or "substrate")
        page_no = row.get("page_number") or row.get("page") or 1
        # Measured before defusing, so the count is of the document the reader
        # attached rather than of the text this function rewrote.
        original_length = len(text)
        text = _defused_text(text)
        lines: list[str] = []
        entries: list[tuple[str, str, str, Any, dict[str, Any] | None]] = []
        used = 0
        cut_at_per_file_cap = False
        # ``_merge_short_sentences`` joins fragments into their neighbours, which is
        # what the matcher has always done and what this function was missing:
        # numbering raw ``_split_sentences`` output on a policy PDF produced
        # sentences like "this Policy", and a citation to a fragment cannot be
        # entailed by anything — the model answers ``no`` and the paragraph lands
        # unsupported however the verdicts are aggregated.
        for sent, page in _merge_short_sentences(_split_sentences(text)):
            clean = str(sent or "").strip()
            if not clean:
                continue
            line = f"[S{n}] {clean}"
            if used + len(line) > SUBSTRATE_CONTEXT_CHARS_PER_FILE:
                cut_at_per_file_cap = True
                break
            lines.append(line)
            entries.append((f"S{n}", clean, filename, page if page else page_no, None))
            used += len(line) + 1
            n += 1
        if not lines:
            continue
        # A form-like source's found fields, as sentences of their own, after
        # the text and inside the same fence (module docstring, "Forms").
        field_lines, field_entries, n, used = _number_field_lines(row, n, used)
        lines.extend(field_lines)
        entries.extend(field_entries)
        block = _source_block(
            filename,
            lines,
            truncated=cut_at_per_file_cap,
            original_length=original_length,
            carried_chars=used,
        )
        if total + len(block) > SUBSTRATE_CONTEXT_CHARS_TOTAL:
            # This source did not fit the total budget at all. The block is not
            # sent, so the notice above is moot; the carry plan records it.
            break
        out.append((block, entries))
        total += len(block)
    return out


def carry_plan(substrate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per source: carried or not, characters carried, ids given, and why not.

    ``included`` is the compile's answer, not the vault's toggle — a source the
    reader attached but the prompt did not reach is ``included: false`` with a
    reason, which is the fact the export needs and the vault cannot supply.
    """
    sources = list(_walk(substrate_rows))
    carried = [entry for entry in sources if entry["included"]]
    return {
        "limit_chars": SUBSTRATE_CONTEXT_CHARS_TOTAL,
        "per_file_limit_chars": SUBSTRATE_CONTEXT_CHARS_PER_FILE,
        "attached": len(sources),
        "carried": len(carried),
        "dropped": len(sources) - len(carried),
        "truncated": sum(1 for entry in sources if entry["truncated"]),
        "carried_chars": sum(entry["block_chars"] for entry in carried),
        "sources": sources,
    }


def empty_plan() -> dict[str, Any]:
    """The plan for a compile that was handed no sources."""
    return carry_plan([])


def summarize(plan: dict[str, Any]) -> dict[str, Any]:
    """The counts alone — what the gate, the compile frame and the report print."""
    return {
        key: plan.get(key)
        for key in ("attached", "carried", "dropped", "truncated", "carried_chars", "limit_chars")
    }


def _gate_sources(project_id: str) -> dict[str, Any] | None:
    """The carry plan the compile persisted beside its gate (``gate.sources``)."""
    try:
        from ..db.connection import init_db
        from ..history import get_db
    except ImportError:
        from db.connection import init_db
        from history import get_db
    try:
        init_db()
        row = get_db().execute(
            "SELECT last_compiled_json FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        data = json.loads(row[0]) if (row and row[0]) else {}
        plan = ((data or {}).get("gate") or {}).get("sources")
        if isinstance(plan, dict) and isinstance(plan.get("sources"), list):
            plan = dict(plan)
            plan["derived"] = False
            return plan
    except Exception:
        return None
    return None


def source_carry_for(
    project_id: str, rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """What the compile carried: the record when it left one, else the walk re-run.

    A document compiled before the record existed has none, and the closest true
    answer is the selection the *same walk* makes over the vault now — which is
    what produced the prompt unless a source was re-uploaded since. That case is
    flagged ``derived``, so a re-computation is never presented as a record.
    """
    recorded = _gate_sources(project_id)
    if recorded is not None:
        return recorded
    if rows is None:
        try:
            from ..db.substrate_repository import list_substrate_for_project
        except ImportError:
            from db.substrate_repository import list_substrate_for_project
        try:
            rows = list_substrate_for_project(project_id, with_text=True)
        except TypeError:  # pragma: no cover - older repository signature
            rows = list_substrate_for_project(project_id)
    plan = carry_plan(rows)
    plan["derived"] = True
    plan["note"] = (
        "The compile did not record which sources it carried; this is what the same "
        "walk carries over the sources in the vault now."
    )
    return plan


def plan_sources_by_id(plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in (plan or {}).get("sources") or []:
        if isinstance(entry, dict) and entry.get("source_id"):
            out[str(entry["source_id"])] = entry
    return out
