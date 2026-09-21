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
        )
        total += len(block)
        yield entry


def numbered_source_blocks(
    substrate_rows: list[dict[str, Any]],
) -> list[tuple[str, list[tuple[str, str, str, Any]]]]:
    """The one place the source is numbered.

    Returns ``[(prompt block, [(id, sentence, filename, page), …]), …]``, the
    block already carrying its ``[S<N>]`` prefixes and already bounded by the
    per-file and total budgets. ``_build_substrate_context`` writes the blocks;
    ``build_sentence_map`` reads the entries. Both walk this function, so an id
    the model cites resolves to the sentence it was shown — numbering the whole
    document while the prompt truncates is how a citation lands on the wrong
    sentence, and numbering the whole document also blew the 30k input cap
    (35,382 tokens measured on two policies) because every sentence now carries
    a prefix.

    Ids are assigned in document order and advance across files, so ``S<n>`` is
    stable for a given set of rows.
    """
    out: list[tuple[str, list[tuple[str, str, str, Any]]]] = []
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
        entries: list[tuple[str, str, str, Any]] = []
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
            entries.append((f"S{n}", clean, filename, page if page else page_no))
            used += len(line) + 1
            n += 1
        if not lines:
            continue
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
