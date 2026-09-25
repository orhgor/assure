"""Parsure intake page (``GET /parsing``): evidence-first, calm triage.

Contract (assure_ui_revisions.md §4, 2026-09-25): one-line intro, one compact
summary line, document cards ranked ready / needs review / conflict, amber
chips for calm warnings, a parser badge that is secondary, and no fabricated
numbers — a document without a Parsure report reads "—" with the reason, and
the visible copy never uses the system verbs Ingest / Parse / Compile / Route.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types

import pytest


def _stub_missing_parsure_routes(monkeypatch) -> None:
    """``create_app`` registers the Parsure API routes; while that module is still
    landing (concurrent work, 2026-09-25) the intake page must stay testable, so a
    no-op registrar is injected only when the real module is genuinely absent."""
    name = "prompt_matrix.routers.parsure_routes"
    if name in sys.modules or importlib.util.find_spec(name) is not None:
        return
    stub = types.ModuleType(name)
    stub.register_parsure_routes = lambda app: None  # noqa: ARG005
    monkeypatch.setitem(sys.modules, name, stub)


@pytest.fixture
def client(tmp_path, monkeypatch):
    _stub_missing_parsure_routes(monkeypatch)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "parsing.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.delenv("ASSURE_S3_BUCKET", raising=False)
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def _visible_text(html: str) -> str:
    """Strip style/script blocks and tags so assertions read what a user reads."""
    html = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text)


def _seed_vault(project: str, filename: str, **meta):
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.db.substrate_repository import save_substrate_entry

    ensure_project(project)
    base = dict(page_count=3, extracted_text="Coverage limit $5,000,000.", parser_name="jdf-cli", source_kind="pdf-text")
    base.update(meta)
    return save_substrate_entry(project, filename=filename, **base)


def _fake_report(document_id: str, filename: str) -> dict:
    return {
        "report_id": "rep-photo-1",
        "document_id": document_id,
        "filename": filename,
        "material_type": "photo",
        "modality": "phone_photo",
        "source_kind": "image",
        "parser_name": "textract",
        "parser_version": "2026.09",
        "page_count": 4,
        "pages": [
            {"page": 1, "quality_score": 0.81, "flags": [], "basis": "sharpness 0.9"},
            {"page": 2, "quality_score": 0.31, "flags": ["blurry", "glare"], "basis": "laplacian variance 12"},
        ],
        "document_quality_score": 0.42,
        "quality_flags": ["blurry"],
        "classification": {"document_type": "auto_policy", "confidence": 0.88, "basis": "header match", "override": None},
        "fields": [
            {
                "name": "policy_number",
                "label": "Policy number",
                "value": "PR-1234",
                "extraction_confidence": 0.91,
                "confidence_basis": "OCR 0.94 × page quality 0.81",
                "field_state": "accepted",
                "routing_action": "none",
                "review_required": False,
                "reason": "",
            },
            {
                "name": "insured_signature",
                "label": "Insured signature",
                "value": None,
                "extraction_confidence": 0.38,
                "confidence_basis": "ink density 22% of typical",
                "field_state": "unverified",
                "routing_action": "manual_review",
                "review_required": True,
                "reason": "Signature is faint",
                "signature_quality": "faint",
            },
            {
                "name": "premium_total",
                "label": "Premium total",
                "value": "1,2O4.00",
                "extraction_confidence": None,
                "confidence_basis": "no_signal_available",
                "field_state": "partial",
                "routing_action": "manual_review",
                "review_required": True,
                "reason": "Digit confused with letter",
                "number_quality": "ambiguous",
            },
        ],
        "conflicts": [],
        "review_summary": {"fields_total": 3, "fields_accepted": 1, "fields_review": 2, "fields_rejected": 0, "reasons": []},
        "quality_report": {
            "summary": "Page 2 is blurry with glare.",
            "flags": ["blurry", "glare"],
            "signature": {"quality": "faint", "basis": "ink density 22%"},
            "numbers": {"flagged": ["premium_total"]},
        },
        "replay": {"eligible": True, "reasons": ["policy v3 changed signature threshold"], "replayed": False, "history": []},
        "created_at": "2026-03-15T10:04:00+00:00",
    }


def _install_fake_repository(monkeypatch, reports):
    mod = types.ModuleType("prompt_matrix.db.parsure_repository")
    mod.list_reports = lambda project_id, *, limit=100: list(reports)  # noqa: ARG005
    mod.get_report = lambda project_id, report_id: next((r for r in reports if r["report_id"] == report_id), None)
    monkeypatch.setitem(sys.modules, "prompt_matrix.db.parsure_repository", mod)


FORBIDDEN_WORDS = re.compile(r"\b(ingest\w*|parse[ds]?|parsing|compil\w+|rout(e|ed|ing))\b", re.I)


def test_empty_state_has_one_upload_action_and_calm_copy(client):
    res = client.get("/parsing?project_id=empty-project")
    assert res.status_code == 200
    text = _visible_text(res.get_data(as_text=True))
    assert "No documents yet. Upload evidence to begin." in text
    assert text.count(">Upload<") == 0  # tags stripped; the button copy is plain
    assert re.search(r"\bUpload\b", text)
    assert "Documents:" not in text  # no summary line when nothing came in
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


def test_vault_rows_without_reports_render_dashes_and_the_reason(client, monkeypatch):
    # No repository module at all: the page must still render from vault rows.
    monkeypatch.setitem(sys.modules, "prompt_matrix.db.parsure_repository", types.ModuleType("stub"))
    _seed_vault("p-legacy", "scan-2024.pdf", parser_name="textract", source_kind="pdf-scan", page_count=5)
    _seed_vault("p-legacy", "policy.pdf", parser_name="jdf-cli", source_kind="pdf-text", page_count=3)

    res = client.get("/parsing?project_id=p-legacy")
    assert res.status_code == 200
    text = _visible_text(res.get_data(as_text=True))

    assert "Here's what came in, its quality, and what needs attention." in text
    assert "Documents: 2 · Pages: 8 · Avg quality: — · Need attention: 0 · Ready for Assure: 0" in text
    assert text.count("Quality not assessed (uploaded before intake scoring)") == 2
    assert "Quality —" in text
    assert "Type uncertain" in text
    assert "Scan" in text and "Digital PDF" in text
    # Parser is a secondary badge, never a summary stat.
    assert "JDF: " not in text and "Textract: " not in text
    assert "Textract" in text and "JDF" in text
    # No unearned numbers: nothing like "0.50" or "85%" appears for unscored docs.
    assert not re.search(r"\b0\.\d\d\b", text)
    assert "%" not in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


def test_rows_joined_with_a_report_show_quality_chips_and_review_copy(client, monkeypatch):
    row = _seed_vault("p-photo", "claim-photo.jpg", parser_name="textract", source_kind="image", page_count=4)
    _seed_vault("p-photo", "older.pdf", page_count=2)
    _install_fake_repository(monkeypatch, [_fake_report(row["id"], "claim-photo.jpg")])

    res = client.get("/parsing?project_id=p-photo")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    text = _visible_text(html)

    # Summary line: pages come from the report when there is one; avg over scored docs only.
    assert "Documents: 2 · Pages: 6 · Avg quality: 0.42 · Need attention: 1 · Ready for Assure: 0" in text

    # Card copy: source · modality in words, document type in words, facts line.
    assert "Photo · Phone photo" in text
    assert "Auto policy" in text
    assert "Mar 15, 2026" in text
    assert "Pages 4 · Quality 0.42 · 2 fields need review" in text
    assert "2 fields need review" in text
    assert 'data-status="review"' in html
    assert 'data-report-id="rep-photo-1"' in html

    # Calm amber chips.
    assert "Page quality is low." in text
    assert "Signature is faint." in text
    assert "Replay available after policy update." in text
    assert "Some numbers are unclear." in text

    # Primary action carries the report into the Assure workspace.
    assert 'href="/?project_id=p-photo&amp;report_id=rep-photo-1"' in html or 'href="/?project_id=p-photo&report_id=rep-photo-1"' in html
    assert ">Review<" in html

    # Details: per-page quality in words, "why" for fields, a None confidence reads "—".
    assert "Blurry, Glare" in text
    assert "Insured signature" in text and "ink density 22% of typical" in text
    assert "Premium total" in text and "Digit confused with letter" in text
    assert re.search(r"Premium total Partial — ", text), "None confidence must render as —, not a number"
    assert "Technical details" in text and "2026.09" in text

    # The unscored sibling still says why it has no number.
    assert "Quality not assessed (uploaded before intake scoring)" in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


def test_ready_and_conflict_ranking(client, monkeypatch):
    ready_row = _seed_vault("p-rank", "deed.pdf", page_count=2)
    bad_row = _seed_vault("p-rank", "title.pdf", page_count=1)
    ready = _fake_report(ready_row["id"], "deed.pdf")
    ready.update(
        report_id="rep-ready",
        document_quality_score=0.93,
        quality_flags=[],
        pages=[{"page": 1, "quality_score": 0.95, "flags": []}],
        fields=[ready["fields"][0]],
        review_summary={"fields_total": 1, "fields_accepted": 1, "fields_review": 0, "fields_rejected": 0, "reasons": []},
        quality_report={"summary": "", "flags": [], "signature": {"quality": "clear"}, "numbers": {"flagged": []}},
        replay={"eligible": False, "reasons": [], "replayed": False, "history": []},
        classification={"document_type": "deed", "confidence": 0.9, "basis": "", "override": None},
    )
    bad = _fake_report(bad_row["id"], "title.pdf")
    bad.update(
        report_id="rep-bad",
        conflicts=[{"summary": "Insured name differs from the deed"}],
        review_summary={"fields_total": 3, "fields_accepted": 1, "fields_review": 1, "fields_rejected": 1, "reasons": []},
    )
    _install_fake_repository(monkeypatch, [bad, ready])

    res = client.get("/parsing?project_id=p-rank")
    html = res.get_data(as_text=True)
    text = _visible_text(html)

    assert "Need attention: 1 · Ready for Assure: 1" in text
    assert "Ready for Assure" in text and ">Send to Assure<" in html
    assert 'data-status="ready"' in html and 'data-status="conflict"' in html
    assert "Conflict detected" in text
    assert "Insured name differs from the deed" in text
    assert "Deed" in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


def test_repository_errors_do_not_break_the_page(client, monkeypatch):
    mod = types.ModuleType("prompt_matrix.db.parsure_repository")

    def _boom(project_id, *, limit=100):  # noqa: ARG001
        raise RuntimeError("relation parsure_reports does not exist")

    mod.list_reports = _boom
    monkeypatch.setitem(sys.modules, "prompt_matrix.db.parsure_repository", mod)
    _seed_vault("p-err", "note.png", parser_name="textract", source_kind="image", page_count=1)

    res = client.get("/parsing?project_id=p-err")
    assert res.status_code == 200
    text = _visible_text(res.get_data(as_text=True))
    assert "Documents: 1 · Pages: 1 · Avg quality: —" in text
    assert "Image" in text
    assert "Quality not assessed (uploaded before intake scoring)" in text


# --------------------------------------------------------------------------
# Needs attention (review queue) and Analytics (premium report) sections
# --------------------------------------------------------------------------

def _queue_report(project: str, report_id: str, filename: str, *, quality, review_fields: int, created_at=None, modality="scanned_pdf", flags=()):
    """A report with ``review_fields`` fields asking for a person plus one accepted."""
    from prompt_matrix.db import parsure_repository as repo

    fields = [
        {"name": f"f{i}", "label": f"Field {i}", "value": f"v{i}" if i % 2 else None, "extraction_confidence": 0.41,
         "field_state": "unverified", "routing_action": "manual_review", "review_required": True,
         "reason": "field not found" if i % 2 == 0 else "extraction_confidence 0.41 < 0.75"}
        for i in range(review_fields)
    ] + [{"name": "ok", "label": "Policy number", "value": "PR-1", "extraction_confidence": 0.95, "field_state": "accepted", "routing_action": "none", "review_required": False, "reason": ""}]
    report = {
        "report_id": report_id, "document_id": f"doc-{report_id}", "filename": filename, "modality": modality, "material_type": "pdf",
        "parser_name": "jdf-cli", "page_count": 2, "pages": [], "document_quality_score": quality, "quality_flags": list(flags),
        "classification": {"document_type": "auto_policy", "confidence": 0.9}, "fields": fields, "conflicts": [],
        "quality_report": {"summary": "", "flags": list(flags), "signature": {}, "numbers": {"flagged": []}},
        "replay": {"eligible": False, "reasons": [], "history": []},
    }
    if created_at:
        report["created_at"] = created_at
    return repo.save_report(project, report)


def test_needs_attention_table_renders_collapses_and_marks_overdue(client):
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.history import get_db

    ensure_project("p-queue")
    _queue_report("p-queue", "rep-old", "older-scan.pdf", quality=0.61, review_fields=6, created_at="2026-01-01 00:00:00")
    _queue_report("p-queue", "rep-new", "claim-photo.jpg", quality=0.42, review_fields=6, modality="phone_photo", flags=["blurry"])
    dispute = repo.open_dispute("p-queue", "rep-old", "f1", reason="insured disagrees", actor="bob")
    db = get_db()
    db.execute("UPDATE parsure_disputes SET due_at = ? WHERE dispute_id = ?", ("2020-01-01 00:00:00", dispute["dispute_id"]))
    db.commit()
    report = repo.get_report("p-queue", "rep-old")
    f1 = next(f for f in report["fields"] if f["name"] == "f1")
    f1.update(field_state="disputed", routing_action="adjudicator_queue", dispute_id=dispute["dispute_id"], reason="disputed: insured disagrees")
    repo.update_report("p-queue", "rep-old", report)

    res = client.get("/parsing?project_id=p-queue")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    text = _visible_text(html)

    # Section order: summary line → Needs attention → cards → Analytics; one primary action.
    assert text.index("Documents:") < text.index("Needs attention") < text.index("older-scan.pdf") < text.index("Analytics")
    assert html.count('class="btn-primary"') == 1
    assert "12 items · 1 disputed · 1 overdue" in text
    assert "Document Field Value Why State Action" in text

    # Collapsed to the first 8 rows, the rest hidden behind "Show all 12".
    rows = re.findall(r'<tr class="queue-row"[^>]*>', html)
    assert len(rows) == 12
    assert sum(1 for r in rows if "hidden" in r) == 4 and all("hidden" not in r for r in rows[:8])
    assert "Show all 12" in text

    # Newest report first; within the old report the overdue dispute leads.
    assert rows[0].count('data-report-id="rep-new"') == 1 and rows[6].count('data-report-id="rep-old"') == 1
    assert 'data-field="f1"' in rows[6] and 'data-overdue="1"' in rows[6]
    assert "Overdue" in text and "overdue by" in text
    assert "Disputed" in text and "insured disagrees" in text

    # Actions: Accept only where a value exists; Dispute; Review link with report id.
    assert html.count('data-act="accept"') == 5  # 11 undisputed review fields, odd-numbered ones have a value... minus the disputed f1
    assert html.count('data-act="dispute"') == 11
    assert 'href="/?project_id=p-queue&amp;report_id=rep-new"' in html or 'href="/?project_id=p-queue&report_id=rep-new"' in html
    assert "Review in Assure" in text
    assert "Not found" in text and "Low confidence" in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)

    # Analytics: strong metrics, sparkline, histogram and issue list from real counts.
    assert "Avg quality 0.52" in text  # (0.61 + 0.42) / 2 → 0.515 rounds to 0.52 at two decimals
    assert "Review rate 86%" in text  # 12 of 14 fields
    assert "12 of 14 fields" in text
    assert "Open disputes 1" in text and "1 overdue" in text
    assert "Corrections 0" in text
    assert 'class="spark"' in html and "Average quality per day" in text
    assert "Quality distribution" in text and "0.4–0.6" in text
    assert "What needs attention, by reason" in text and "Blurry" in text
    assert "Table view" in text


def test_analytics_and_queue_empty_states_have_no_fabricated_numbers(client):
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("p-none")
    _seed_vault("p-none", "note.png", parser_name="textract", source_kind="image", page_count=1)

    res = client.get("/parsing?project_id=p-none")
    html = res.get_data(as_text=True)
    text = _visible_text(html)
    assert "No review items. New intake will appear here." in text
    assert "Analytics" in text and "No intake yet." in text
    assert "Avg quality —" in text and "Review rate —" in text and "Open disputes —" in text and "Corrections —" in text
    assert 'class="spark"' not in html and "Quality distribution" not in text
    assert "%" not in text
    assert not re.search(r"\b0\.\d\d\b", text)
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)
