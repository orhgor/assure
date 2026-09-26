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
    assert "Documents: 2 · Pages: 8 · Avg quality: — · Need attention: 0 documents · 0 fields · Ready for Assure: 0" in text
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
    assert "Documents: 2 · Pages: 6 · Avg quality: 42% · Need attention: 1 document · 2 fields · Ready for Assure: 0" in text

    # Card copy: source · modality in words, document type in words, facts line.
    assert "Photo · Phone photo" in text
    assert "Auto policy" in text
    assert "Mar 15, 2026" in text
    assert "Pages 4 · Quality 42% · 2 fields need review" in text
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

    assert "Need attention: 1 document · 2 fields · Ready for Assure: 1" in text  # 2 = the fake report's two manual_review fields, by the queue's rule
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
    assert "12 fields across 2 documents · 1 disputed · 1 overdue" in text
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
    assert "Avg quality 52%" in text  # (0.61 + 0.42) / 2 → 0.515 rounds to 0.52 at two decimals
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


# --------------------------------------------------------------------------
# Extracted data (one table per document type) and the record page
# --------------------------------------------------------------------------

def _data_field(name, label, value, *, field_type="text", state="accepted", routing="none", conf=0.9, reason="", page=1, **extra):
    f = {"name": name, "label": label, "value": value, "field_type": field_type, "extraction_confidence": conf,
         "confidence_basis": f"parser_default[jdf-cli] (0.85) × page_quality ({conf})", "field_state": state,
         "routing_action": routing, "review_required": routing != "none", "reason": reason,
         "source_span": {"page": page, "span_type": "text_range"}}
    f.update(extra)
    return f


def _seed_data_project(project):
    """Two auto policies, one deed, one document of uncertain type (no fields)."""
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project(project)
    policy_fields = lambda n, premium, state: [  # noqa: E731
        _data_field("policy_number", "Policy number", n),
        _data_field("insured_name", "Insured name", "Mary Sample"),
        _data_field("effective_date", "Effective date", "2026-08-14", field_type="date"),
        _data_field("premium", "Premium", premium, field_type="money", state=state, routing="none" if state == "accepted" else "manual_review",
                    conf=0.52, reason="" if state == "accepted" else "extraction_confidence 0.52 < 0.75"),
        _data_field("vin", "VIN", None, field_type="vin", state="unverified", routing="manual_review", conf=0.0, reason="field not found"),
        _data_field("liability_limit", "Liability limit", 100000, field_type="money", state="disputed", routing="adjudicator_queue", reason="disputed: schedule shows 150,000"),
    ]
    repo.save_report(project, {
        "report_id": "rep-pol-1", "document_id": "doc-1", "filename": "policy-declarations.pdf", "modality": "digital_pdf", "material_type": "pdf",
        "parser_name": "jdf-cli", "parser_version": "0.2.3", "page_count": 2, "document_quality_score": 0.93,
        "pages": [{"page": 1, "quality_score": 0.95, "flags": []}, {"page": 2, "quality_score": 0.91, "flags": ["low_contrast"]}],
        "classification": {"document_type": "auto_policy", "confidence": 0.9, "basis": "keyword match"},
        "fields": policy_fields("AP-2025-0001", 1284.0, "unverified"), "conflicts": [],
        "quality_report": {"summary": "Low contrast on 1 of 2 pages.", "flags": ["low_contrast"], "signature": {}, "numbers": {"flagged": []}},
        "replay": {"eligible": False, "reasons": [], "history": []}, "laya": {"suggested_route": "jdf", "escalate": False, "human_review": False, "reasons": [], "model": "rules-v1"},
        "created_at": "2026-09-20 10:04:00",
        "_page_texts": ["Policy Number: AP-2025-0001\nNamed Insured: Mary Sample\nTotal Premium: $1,284.00", "Page two text about coverage."],
    })
    repo.save_report(project, {
        "report_id": "rep-pol-2", "document_id": "doc-2", "filename": "prior-policy.pdf", "modality": "digital_pdf", "material_type": "pdf",
        "parser_name": "jdf-cli", "page_count": 1, "document_quality_score": 0.88, "pages": [{"page": 1, "quality_score": 0.88, "flags": []}],
        "classification": {"document_type": "auto_policy", "confidence": 0.9},
        "fields": policy_fields("AP-2024-0777", 1190.5, "accepted"), "conflicts": [],
        "replay": {"eligible": False}, "created_at": "2026-09-18 09:00:00",
    })
    repo.save_report(project, {
        "report_id": "rep-deed", "document_id": "doc-3", "filename": "warranty-deed-scan.pdf", "modality": "scanned_pdf", "material_type": "pdf",
        "parser_name": "jdf-cli+tesseract", "page_count": 1, "document_quality_score": 0.66, "pages": [{"page": 1, "quality_score": 0.66, "flags": ["skewed"]}],
        "classification": {"document_type": "deed", "confidence": 0.8},
        "fields": [
            _data_field("grantor", "Grantor", "John Q. Sample"),
            _data_field("consideration", "Consideration", 425000, field_type="money"),
            _data_field("legal_description", "Legal description", "Lot 4, Block 2, of the Riverside Addition to the City of Springfield, according to the plat thereof recorded in Book 12"),
            _data_field("recording_date", "Recording date", "2026-07-02", field_type="date", state="rejected", routing="compliance_review", reason="Z3 violation: recorded before execution", z3_violation=True),
        ], "conflicts": [], "replay": {"eligible": False}, "created_at": "2026-09-19 12:00:00",
    })
    repo.save_report(project, {
        "report_id": "rep-unk", "document_id": "doc-4", "filename": "photo-of-something.jpg", "modality": "phone_photo", "material_type": "photo",
        "parser_name": "textract", "page_count": 1, "document_quality_score": 0.12, "pages": [{"page": 1, "quality_score": 0.12, "flags": ["blurry"]}],
        "classification": {"document_type": "uncertain", "confidence": None, "basis": "2 keyword hits"},
        "fields": [], "conflicts": [], "replay": {"eligible": False}, "created_at": "2026-09-21 08:00:00",
    })


def test_extracted_data_tables_are_grouped_by_type_with_quiet_state_marks(client):
    _seed_data_project("p-data")
    res = client.get("/parsing?project_id=p-data")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    text = _visible_text(html)

    # Placement: after Needs attention, before the cards; still one page primary (Upload).
    assert text.index("Needs attention") < text.index("Extracted data") < text.index(" Documents ", text.index("Extracted data"))
    assert html.count('class="btn-primary"') == 1
    assert 'id="data-export"' in html and ">Export all<" in html
    assert 'href="/api/projects/p-data/parsure/export?format=csv&amp;wide=1"' in html or 'href="/api/projects/p-data/parsure/export?format=csv&wide=1"' in html
    assert "CSV, one row per field" in text and ">JSON<" in html

    # Count line from real counts: 4 documents; values = fields with a value; need review = not accepted.
    assert "4 documents · 14 values · 6 need review" in text

    # One table per type, taxonomy order, "Type uncertain" last; columns in taxonomy order.
    groups = re.findall(r'<div class="data-group" data-type="([^"]+)">', html)
    assert groups == ["auto_policy", "deed", "uncertain"]
    assert text.index("Auto policy 2 documents") < text.index("Deed 1 document") < text.index("Type uncertain 1 document")
    policy_head = re.search(r'data-type="auto_policy">.*?</thead>', html, re.S).group(0)
    heads = re.findall(r'<th class="c-val">([^<]+)</th>', policy_head)
    assert heads == ["Policy number", "Insured name", "Effective date", "VIN", "Premium", "Liability limit"]

    # Cells: formatted values with a quiet mark; None is "—" with the reason in the title.
    row = re.search(r'<tr class="data-row" data-report-id="rep-pol-1".*?</tr>', html, re.S).group(0)
    assert 'data-mark="accepted"' in row and ">AP-2025-0001<" in row
    assert ">Aug 14, 2026<" in row
    assert 'data-mark="review"' in row and ">1,284.00<" in row and "Extraction confidence 0.52 is below 0.75" in row
    assert 'data-mark="missing"' in row and 'title="— — Not found in the document"' in row
    assert 'data-mark="rejected"' in row and ">100,000.00<" in row and "Disputed: schedule shows 150,000" in row
    assert 'data-buckets="accepted needs_review not_found"' in row
    assert 'href="/?project_id=p-data&amp;report_id=rep-pol-1"' in row or 'href="/?project_id=p-data&report_id=rep-pol-1"' in row
    assert 'href="/parsing/rep-pol-1?project_id=p-data"' in row and ">Review<" in row and ">Record<" in row
    deed_row = re.search(r'<tr class="data-row" data-report-id="rep-deed".*?</tr>', html, re.S).group(0)
    assert ">425,000.00<" in deed_row
    assert "Lot 4, Block 2, of the Riverside Additi…" in deed_row and 'title="Lot 4, Block 2, of the Riverside Addition to the City of Springfield' in deed_row
    assert 'data-mark="rejected"' in deed_row and "Verification failed: recorded before execution" in deed_row
    unk_row = re.search(r'<tr class="data-row" data-report-id="rep-unk".*?</tr>', html, re.S).group(0)
    assert "No fields until the type is known" in unk_row

    # Filters over the rendered rows; a legend that says what the marks mean.
    assert 'id="data-type"' in html and '<option value="deed">Deed</option>' in html
    assert 'id="data-state"' in html and '<option value="needs_review">Needs review</option>' in html and '<option value="not_found">Not found</option>' in html
    assert 'id="data-search"' in html
    assert "Needs review Rejected or disputed — not found" in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


def test_extracted_data_empty_state(client):
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("p-nodata")
    _seed_vault("p-nodata", "note.png", parser_name="textract", source_kind="image", page_count=1)
    res = client.get("/parsing?project_id=p-nodata")
    html = res.get_data(as_text=True)
    text = _visible_text(html)
    assert "Extracted data" in text
    assert "No extracted data yet. Upload a document to begin." in text
    assert 'id="data-export"' not in html and 'id="data-type"' not in html
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


def test_record_page_shows_fields_page_text_and_history(client):
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.history import get_db

    _seed_data_project("p-record")
    repo.log_event("p-record", "intake_received", report_id="rep-pol-1", payload={})
    repo.log_event("p-record", "quality_assessed", report_id="rep-pol-1", payload={"document_quality_score": 0.93})
    repo.record_correction("p-record", "rep-pol-1", "insured_name", original_value="Mary Sampel", corrected_value="Mary Sample", actor="ana", reason="typo")
    dispute = repo.open_dispute("p-record", "rep-pol-1", "liability_limit", reason="schedule shows 150,000", actor="bob")
    db = get_db()
    db.execute("UPDATE parsure_disputes SET due_at = ? WHERE dispute_id = ?", ("2020-01-01 00:00:00", dispute["dispute_id"]))
    db.commit()

    res = client.get("/parsing/rep-pol-1")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    text = _visible_text(html)

    # Head: back link, filename, type in words, the quality sentence, one primary action.
    assert "← Parsure" in text and 'href="/parsing?project_id=p-record"' in html
    assert "policy-declarations.pdf" in text
    assert "Auto policy · PDF · Digital PDF · Sep 20, 2026" in text
    assert "Quality 93% · Low contrast on 1 of 2 pages." in text
    assert html.count('class="btn-primary"') == 1 and "Review in Assure" in text
    assert 'href="/?project_id=p-record&amp;report_id=rep-pol-1"' in html or 'href="/?project_id=p-record&report_id=rep-pol-1"' in html
    assert "Export JSON" in text and 'href="/api/projects/p-record/parsure/rep-pol-1/export?format=csv"' in html
    assert "Pages: 2 · Fields: 6 · Need review: 3" in text

    # Fields: needs-review rows first, then accepted; state chip, confidence + why, page, reason in words.
    order = re.findall(r'<tr class="field[^"]*" data-field="([^"]+)"', html)
    assert order == ["premium", "vin", "liability_limit", "policy_number", "insured_name", "effective_date"]
    assert "3 need review, listed first" in text
    prem = re.search(r'data-field="premium".*?</tr>', html, re.S).group(0)
    assert ">1,284.00<" in prem and "Unverified" in prem and ">52%<" in prem and "parser_default[jdf-cli] (0.85) × page_quality (0.52)" in prem and "Why this confidence" in prem
    assert "Extraction confidence 0.52 is below 0.75" in prem and "Needs a reviewer" in prem
    vin = re.search(r'data-field="vin".*?</tr>', html, re.S).group(0)
    assert 'data-mark="missing"' in vin and "Not found in the document" in vin and ">—<" in vin
    assert ">0%<" in vin  # a not-found field's confidence is a measured 0.0, not a blank
    assert "Aug 14, 2026" in text

    # Pages: quality and flags in words, the text of each page behind a disclosure.
    assert "Page 1 Quality 95% No issues" in text
    assert "Page 2 Quality 91% Low contrast" in text
    assert text.count("Text of this page") == 2
    assert "Policy Number: AP-2025-0001" in text and "Page two text about coverage." in text

    # History: correction, overdue dispute and events in one timeline, newest first.
    assert "Provenance &amp; history" in text
    assert "Insured name corrected" in text and "Mary Sampel → Mary Sample — typo" in text and "ana" in text
    assert "Liability limit disputed · overdue" in text and "schedule shows 150,000 — overdue by" in text
    assert 'data-kind="dispute" data-overdue="1"' in html
    assert "Received" in text and "Quality assessed" in text and "Quality 93%" in text
    kinds = re.findall(r'<li data-kind="([^"]+)"', html)
    assert kinds[0] in ("dispute", "correction") and "event" in kinds

    # Technical details folded away; the private page-text key never leaks by name.
    assert "Technical details" in text and "jdf-cli" in text and "0.2.3" in text and "rules-v1" in text
    assert "_page_texts" not in html
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)

    # The same record by project hint; a wrong project is not found.
    assert client.get("/parsing/rep-pol-1?project_id=p-record").status_code == 200
    assert client.get("/parsing/rep-pol-1?project_id=someone-else").status_code == 404


def test_record_page_without_page_text_says_so_and_404_is_calm(client):
    _seed_data_project("p-record-2")
    res = client.get("/parsing/rep-pol-2")
    assert res.status_code == 200
    text = _visible_text(res.get_data(as_text=True))
    assert "prior-policy.pdf" in text
    assert "text not kept" in text and "The text of this page was not kept with the record." in text
    assert "Quality 88% · No page issues were found." in text
    assert "all accepted" not in text  # vin is not found, liability is disputed → still need review
    assert "Nothing has been changed on this record." in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)

    res = client.get("/parsing/rep-nope")
    assert res.status_code == 404
    text = _visible_text(res.get_data(as_text=True))
    assert "This record is not here." in text and "Back to Parsure" in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


# --------------------------------------------------------------------------
# One unit per count, and "nothing extracted" said plainly (customer report,
# 2026-09-26: "Need attention: 3" over "62 items", and twelve "not found" rows
# read as a broken OCR step)
# --------------------------------------------------------------------------

def _empty_report(project, report_id, filename, *, fields, doc_type="auto_claim", page_texts=None, text_chars=None, flags=(), created_at="2026-09-24 09:30:00"):
    """A typed report whose every field is empty — the customer's real_estate_policy_500697.pdf shape."""
    from prompt_matrix.db import parsure_repository as repo

    report = {
        "report_id": report_id, "document_id": f"doc-{report_id}", "filename": filename, "modality": "digital_pdf", "material_type": "pdf",
        "parser_name": "jdf-cli", "parser_version": "0.2.3", "page_count": 1, "document_quality_score": 0.97,
        "pages": [{"page": 1, "quality_score": 0.97, "flags": list(flags)}], "quality_flags": list(flags),
        "classification": {"document_type": doc_type, "confidence": 0.25, "basis": "keyword heuristic: 3/12 auto_claim keywords matched", "override": None},
        "fields": [
            {"name": n, "label": lbl, "field_type": ft, "value": None, "raw": None, "extraction_confidence": 0.0, "confidence_basis": "field not found",
             "field_state": "unverified", "routing_action": "manual_review", "review_required": True, "reason": "field not found", "source_span": None}
            for n, lbl, ft in fields
        ],
        "conflicts": [],
        "quality_report": {"summary": "No quality issues detected on 1 page.", "flags": list(flags), "signature": {}, "numbers": {"flagged": []}, "text_chars": text_chars},
        "replay": {"eligible": False, "reasons": [], "history": []},
        "created_at": created_at,
    }
    if page_texts is not None:
        report["_page_texts"] = page_texts
    return repo.save_report(project, report)


CLAIM_FIELDS = [("claim_number", "Claim number", "text"), ("policy_number", "Policy number", "text"), ("claimant_name", "Claimant", "name"),
                ("date_of_loss", "Date of loss", "date"), ("vin", "VIN", "vin")]


def _seed_counts_project(project):
    from prompt_matrix.db.jdf_repository import ensure_project
    from tests.test_field_extractor import REAL_ESTATE_LINES

    ensure_project(project)
    _queue_report(project, "rep-a", "older-scan.pdf", quality=0.61, review_fields=6, created_at="2026-09-01 00:00:00")
    _queue_report(project, "rep-b", "claim-photo.jpg", quality=0.42, review_fields=6, modality="phone_photo", created_at="2026-09-02 00:00:00")
    text = "\n".join(REAL_ESTATE_LINES)
    _empty_report(project, "rep-re", "real_estate_policy_500697.pdf", fields=CLAIM_FIELDS, page_texts=[text], text_chars=len(text))
    _empty_report(project, "rep-blank", "blank-scan.pdf", fields=CLAIM_FIELDS[:3], page_texts=["Cl aim  n0."], text_chars=11, flags=["no_text"],
                  created_at="2026-09-25 09:30:00")


def test_every_needs_attention_figure_is_the_same_number(client):
    """Summary line, "Needs attention" header, Extracted-data count line, the
    queue API's counts and the repository helper all say the same documents
    and the same fields for the same data."""
    from prompt_matrix.db import parsure_repository as repo

    _seed_counts_project("p-counts")
    reports = repo.list_reports("p-counts")
    attention = repo.attention_counts(reports)
    assert attention == {"documents": 4, "fields": 20, "nothing_extracted": 2, "fields_found": 8, "schema_mismatch": 0}  # 6+6+5+3 flagged fields; (3+1)+(3+1) values

    api = client.get("/api/projects/p-counts/parsure/queue").get_json()
    assert api["total"] == 20 and api["counts"]["needs_review"] == 20
    assert api["counts"]["documents"] == 4 and api["counts"]["nothing_extracted"] == 2 and api["counts"]["fields_found"] == 8
    assert api["total"] == attention["fields"] and api["counts"]["documents"] == attention["documents"]

    res = client.get("/parsing?project_id=p-counts")
    html = res.get_data(as_text=True)
    text = _visible_text(html)
    summary = re.search(r"Need attention: (\d+) documents · (\d+) fields", text)
    header = re.search(r'id="queue-meta" data-fields="(\d+)" data-documents="(\d+)"', html)
    header_text = re.search(r"(\d+) fields across (\d+) documents", text)
    data_line = re.search(r"(\d+) documents · (\d+) values · (\d+) need review", text)
    assert summary and header and header_text and data_line
    assert int(summary.group(1)) == int(header.group(2)) == int(header_text.group(2)) == api["counts"]["documents"] == 4
    assert int(summary.group(2)) == int(header.group(1)) == int(header_text.group(1)) == int(data_line.group(3)) == api["counts"]["needs_review"] == 20
    assert int(data_line.group(2)) == attention["fields_found"] == 8
    assert "items" not in text.lower()  # the unit is always named
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


def test_nothing_extracted_folds_to_one_queue_row_and_one_amber_card_line(client):
    _seed_counts_project("p-empty")
    res = client.get("/parsing?project_id=p-empty")
    html = res.get_data(as_text=True)
    text = _visible_text(html)

    # Queue: the two empty documents are one row each (not 5 + 3 "not found" rows); the count is unchanged.
    folded = re.findall(r'<tr class="queue-row queue-row--empty" data-kind="nothing_extracted" data-report-id="([^"]+)"', html)
    assert folded == ["rep-blank", "rep-re"]  # newest first
    assert len(re.findall(r'<tr class="queue-row', html)) == 14 and "Show all 14" in text
    assert "20 fields across 4 documents" in text
    assert "real_estate_policy_500697.pdf Auto claim nothing extracted as Auto claim 5 fields of this type, none found" in text
    assert "The document type may be wrong — change it and the fields are re-read." in text
    row = re.search(r'data-report-id="rep-re"[^>]*>.*?</tr>', html, re.S).group(0)
    assert 'href="/parsing/rep-re?project_id=p-empty"' in row and ">Check type<" in row and "data-act=" not in row
    # No per-field "not found" rows for the empty documents: one folded row each, and nothing else.
    assert not re.findall(r'<tr class="queue-row" [^>]*data-report-id="rep-(?:re|blank)"', html)
    assert len(re.findall(r'data-kind="nothing_extracted"', html)) == 2

    # Cards: amber "nothing extracted" status, one primary action to the record page, no red.
    card = re.search(r'<article class="card" data-status="notype" data-report-id="rep-re">.*?</article>', html, re.S).group(0)
    card_text = _visible_text(card)
    assert "Nothing extracted — check the document type" in card_text
    assert "0 of 5 fields read" in card_text and "need review" not in card_text
    assert 'href="/parsing/rep-re?project_id=p-empty"' in card and ">Check type<" in card
    assert "Replay available" not in card_text and "Document type is uncertain" not in card_text
    blank = _visible_text(re.search(r'<article class="card" data-status="notype" data-report-id="rep-blank">.*?</article>', html, re.S).group(0))
    assert "The pages could not be read (11 characters)." in blank and "0 of 3 fields read" in blank
    assert not re.search(r"OCR confidence|0\.\d\d confidence", blank)
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


def test_untyped_report_without_fields_is_not_called_ready(client):
    _seed_data_project("p-untyped")
    html = client.get("/parsing?project_id=p-untyped").get_data(as_text=True)
    card = _visible_text(re.search(r'<article class="card" data-status="notype" data-report-id="rep-unk">.*?</article>', html, re.S).group(0))
    assert "Nothing extracted — choose the document type" in card and "Choose type" in card
    assert "Ready for Assure" not in card and "no fields until the type is chosen" in card


def test_record_page_notice_offers_the_type_and_reloads_with_fields(client):
    _seed_counts_project("p-notice")
    res = client.get("/parsing/rep-re?project_id=p-notice")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    text = _visible_text(html)

    # The notice: one calm sentence, the likely cause, the selector, above the table.
    assert 'id="type-notice"' in html and text.index("Nothing extracted.") < text.index("Claim number")
    assert "Nothing extracted. No fields could be read as Auto claim. The page carries 10 of 11 Property policy fields" in text
    assert "Fields: 5 · Need review: 5" in text and "none of 5 found" in text
    options = re.findall(r'<option value="([^"]+)"( selected)?>([^<]+)</option>', html)
    assert [o[0] for o in options] == list(__import__("prompt_matrix.services.field_extractor", fromlist=["x"]).DOCUMENT_TYPES) + ["uncertain"]
    # The evidence pass proposes the type whose fields are on the page: pre-selected, and said in words.
    assert [o[2] for o in options if o[1]] == ["Property policy"] and ("auto_claim", "", "Auto claim") in options
    assert "The page carries 10 of 11 Property policy fields — re-read it as that type." in text
    assert "Quality 97% · No quality issues detected on 1 page." in text  # the hero does not repeat the notice
    assert 'id="type-form" data-url="/api/projects/p-notice/parsure/rep-re/classification"' in html
    assert ">Re-read as this type<" in html and html.count('class="btn-primary"') == 1  # the selector is not a second primary
    # The page text is open so the reader sees what was read.
    assert '<details class="text" open>' in html and "Policy Number: RE-500697" in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)

    # The blank scan says the pages could not be read, with the count, and does not invent a confidence.
    blank = _visible_text(client.get("/parsing/rep-blank?project_id=p-notice").get_data(as_text=True))
    assert "the pages carry 11 characters of text; the file may be a scan the OCR could not read" in blank
    assert "The pages could not be read (11 characters of text)" in blank and "Changing the type will not add text." in blank
    assert "The document type may be wrong — choose the right one" in blank  # no type finds fields in 11 characters: no hint

    # Choosing the type through the same route the selector posts to re-reads the fields; the notice goes away.
    res = client.post("/api/projects/p-notice/parsure/rep-re/classification", json={"document_type": "property_policy", "reason": "set on the record page", "actor": "reviewer"})
    assert res.status_code == 200 and res.get_json()["reextracted"] is True
    body = res.get_json()["report"]
    assert body["review_summary"]["fields_found"] == 10 and body["classification"]["override"]["previous"] == "auto_claim"
    assert not body["quality_report"]["summary"].startswith("No fields could be read")
    html = client.get("/parsing/rep-re?project_id=p-notice").get_data(as_text=True)
    text = _visible_text(html)
    assert 'id="type-notice"' not in html and "Property policy (set by reviewer)" in text
    assert "Fields: 11 · Need review:" in text and "RE-500697" in text and "425,000.00" in text
    assert '<details class="text" open>' not in html
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


def test_schema_mismatch_is_one_line_with_the_type_selector_and_absent_fields_show_their_anchor(client, monkeypatch):
    """A CMS-1500 whose labels the OCR did not read: the family is medical, no
    medical schema fits, and the document — not thirteen fields — asks for a
    person: one card line, one queue row, one notice with the type selector
    proposing Medical claim. A read CMS-1500 shows every field anchored, the
    absent signature on the node it was searched from, marked "searched"."""
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.services.v1_orchestrator import run_after_parse
    from tests.test_field_extractor import CMS_1500_LINES
    from tests.test_v1_orchestrator import MEDICAL_PROSE_LINES, VERIFICATION, jdf_cli_bundle

    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    ensure_project("p-mismatch")
    letter = run_after_parse("p-mismatch", bundle=jdf_cli_bundle(MEDICAL_PROSE_LINES), verification=VERIFICATION, filename="clinic-letter.pdf",
                             file_bytes=None, result={"document_id": "doc-letter", "revision_id": "rev-1", "version": 1}, job_id=None, intake=None)["report_id"]
    claim = run_after_parse("p-mismatch", bundle=jdf_cli_bundle(CMS_1500_LINES), verification=VERIFICATION, filename="cms1500.pdf",
                            file_bytes=None, result={"document_id": "doc-cms", "revision_id": "rev-2", "version": 1}, job_id=None, intake=None)["report_id"]
    reports = repo.list_reports("p-mismatch")
    attention = repo.attention_counts(reports)
    assert attention["schema_mismatch"] == 1 and attention["documents"] == 2 and attention["nothing_extracted"] == 0
    queue = client.get("/api/projects/p-mismatch/parsure/queue").get_json()
    mismatch_items = [i for i in queue["items"] if i.get("kind") == "schema_mismatch"]
    assert len(mismatch_items) == 1 and mismatch_items[0]["report_id"] == letter and mismatch_items[0]["suggested_type"] == "medical_claim"
    assert queue["total"] == attention["fields"] and queue["counts"]["schema_mismatch"] == 1 and queue["counts"]["documents"] == 2

    html = client.get("/parsing?project_id=p-mismatch").get_data(as_text=True)
    text = _visible_text(html)
    card = re.search(r'<article class="card" data-status="notype" data-report-id="%s">.*?</article>' % letter, html, re.S).group(0)
    card_text = _visible_text(card)
    assert "Wrong document type — read as Medical claim?" in card_text and "Medical — type unknown" in card_text
    assert "need review" not in card_text and "Nothing extracted" not in card_text and ">Check type<" in card
    visible_card = _visible_text(re.sub(r"<details.*?</details>", "", card, flags=re.S))  # the folded Details repeat the report's summary sentence
    assert visible_card.count("Wrong document type") == 1
    row = re.search(r'<tr class="queue-row queue-row--empty" data-kind="schema_mismatch" data-report-id="%s"[^>]*>.*?</tr>' % letter, html, re.S).group(0)
    assert "Wrong document type — read as Medical claim?" in _visible_text(row) and f'href="/parsing/{letter}?project_id=p-mismatch"' in row
    assert not re.findall(r'<tr class="queue-row" [^>]*data-report-id="%s"' % letter, html)  # no per-field rows for the mismatch
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)

    res = client.get(f"/parsing/{letter}?project_id=p-mismatch")
    html = res.get_data(as_text=True)
    text = _visible_text(html)
    assert 'id="type-notice" data-tone="partial" data-kind="schema_mismatch"' in html
    assert text.count("Wrong document type — read as Medical claim?") == 1
    options = re.findall(r'<option value="([^"]+)"( selected)?>([^<]+)</option>', html)
    assert [o[2] for o in options if o[1]] == ["Medical claim"] and ("medical_claim", " selected", "Medical claim") in options
    assert "Need review: 0" in text and "Medical — type unknown" in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)

    html = client.get(f"/parsing/{claim}?project_id=p-mismatch").get_data(as_text=True)
    text = _visible_text(html)
    assert 'id="type-notice"' not in html and "Medical claim" in text
    rows = re.findall(r'<tr class="field[^"]*" data-field="([^"]+)"[^>]*>.*?</tr>', html, re.S)
    assert len(rows) == 13
    sig = re.search(r'<tr class="field[^"]*" data-field="signature"[^>]*>.*?</tr>', html, re.S).group(0)
    assert re.search(r'<small class="node-id" title="JDF node the field was searched from">el-0 · searched</small>', sig)
    npi = re.search(r'<tr class="field[^"]*" data-field="provider_npi"[^>]*>.*?</tr>', html, re.S).group(0)
    assert re.search(r'<small class="node-id" title="JDF node">el-17</small>', npi) and "searched" not in npi
    assert "Found, needs a look" in _visible_text(npi) and "Found, verified" in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


# ---------------------------------------------------------------------------
# Intake graph critique (services/redhat_graph): record section and card line
# ---------------------------------------------------------------------------


def _critique(project, report_id, **kw):
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.services import redhat_graph as rg

    report = repo.get_report(project, report_id)
    kw.setdefault("llm", False)
    rg.attach_findings(report, rg.critique_report(report, **kw))
    repo.update_report(project, report_id, report)
    return report


def test_record_page_redhat_section_says_not_run_then_lists_findings_with_anchors(client):
    _seed_data_project("p-rh-record")
    html = client.get("/parsing/rep-pol-1").get_data(as_text=True)
    text = _visible_text(html)
    assert 'id="redhat" data-ran="0"' in html
    assert "Red-Hat findings" in text and "Not run — no critique is recorded for this report." in text
    assert "No findings" not in text
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)

    report = _critique("p-rh-record", "rep-pol-1", export_state={"trust_state": "review_required", "title": "Formal Verification Certificate"})
    high = [f for f in report["redhat"]["findings"] if f["severity"] == "high"]
    assert high and high[0]["rule"] == "export_overclaiming"
    html = client.get("/parsing/rep-pol-1").get_data(as_text=True)
    text = _visible_text(html)
    assert f'id="redhat" data-ran="1" data-count="{len(report["redhat"]["findings"])}" data-high="{len(high)}"' in html
    assert "intake graph critique" in text and "Not run" not in text.split("Red-Hat findings", 1)[1].split("Pages", 1)[0]
    assert 'data-severity="high"' in html and "Export uses certificate language before verification" in text
    assert "Document root · doc-1" in text  # the anchor is never empty; the root is named as such
    assert "only a verified state may carry that word" in text
    assert re.search(r"\b\d+ high · \d+ medium · \d+ low\b", text)
    assert not FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text)


def test_record_page_says_no_findings_only_when_a_critique_ran_clean(client):
    from prompt_matrix.db import parsure_repository as repo
    from prompt_matrix.services import redhat_graph as rg

    _seed_data_project("p-rh-clean")
    report = repo.get_report("p-rh-clean", "rep-pol-1")
    rg.attach_findings(report, [], notes=["unsupported-claim check skipped: PARSURE_REDHAT_LLM=0"])
    repo.update_report("p-rh-clean", "rep-pol-1", report)
    html = client.get("/parsing/rep-pol-1").get_data(as_text=True)
    text = _visible_text(html)
    assert 'id="redhat" data-ran="1" data-count="0" data-high="0"' in html
    assert "No findings." in text and "What did not run" in text and "PARSURE_REDHAT_LLM=0" in text


def test_card_shows_a_redhat_count_line_red_only_for_high(client):
    _seed_data_project("p-rh-card")
    html = client.get("/parsing?project_id=p-rh-card").get_data(as_text=True)
    assert "Red-Hat finding" not in _visible_text(html)  # not run: no line, no zero

    report = _critique("p-rh-card", "rep-pol-1")  # rules only, no export state → no high finding expected on this seed
    n = len(report["redhat"]["findings"])
    highs = report["redhat"]["counts"]["high"]
    html = client.get("/parsing?project_id=p-rh-card").get_data(as_text=True)
    text = _visible_text(html)
    if n:
        m = re.search(r'class="chip chip--redhat" data-tone="(\w+)"[^>]*>(\d+) Red-Hat finding', html)
        assert m and int(m.group(2)) == n
        assert m.group(1) == ("high" if highs else ("medium" if report["redhat"]["counts"]["medium"] else "low"))
        assert 'href="/parsing/rep-pol-1?project_id=p-rh-card#redhat"' in html
    else:
        assert "Red-Hat finding" not in text

    report = _critique("p-rh-card", "rep-pol-1", export_state={"trust_state": "not_verified", "renderer": "text", "title": "Certificate"})
    html = client.get("/parsing?project_id=p-rh-card").get_data(as_text=True)
    m = re.search(r'class="chip chip--redhat" data-tone="high"[^>]*>(\d+) Red-Hat findings \((\d+) high\)', html)
    assert m and int(m.group(1)) == len(report["redhat"]["findings"]) and int(m.group(2)) == report["redhat"]["counts"]["high"] == 2
    assert re.search(r"(\d+) Red-Hat high", _visible_text(html))  # the queue header's count comes from the same block
    assert not FORBIDDEN_WORDS.search(_visible_text(html)), FORBIDDEN_WORDS.search(_visible_text(html))
