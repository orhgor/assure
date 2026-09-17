"""Substrate Vault UI: list/delete/include routes and provenance stamping."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

from prompt_matrix.models.jdf import (
    _MIN_CLAIM_TOKENS,
    _tokenize,
    attach_substrate_provenance_to_tree,
    build_document_from_draft,
)


def _single_page_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture()
def vault_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()

    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client()


def _upload(client, filename="brief.pdf", text="Net income grew 12%."):
    with patch("prompt_matrix.routers.substrate.TextractClient") as mock_cls:
        instance = mock_cls.return_value
        instance._get_page_count.return_value = 1
        instance.extract_text.return_value = {
            "text": text,
            "tables": [],
            "forms": [],
            "page_count": 1,
            "filename": filename,
        }
        data = {"file": (io.BytesIO(_single_page_pdf()), filename)}
        res = client.post(
            "/api/projects/default/substrate/upload",
            data=data,
            content_type="multipart/form-data",
        )
    return res


def test_upload_returns_size_bytes(vault_client):
    res = _upload(vault_client)
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    assert payload["size_bytes"] > 0


def test_list_is_empty_for_unknown_project(vault_client):
    res = vault_client.get("/api/projects/no-such-project/substrate")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "files": []}


def test_list_returns_uploaded_file(vault_client):
    upload_res = _upload(vault_client)
    file_id = upload_res.get_json()["id"]

    res = vault_client.get("/api/projects/default/substrate")
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    assert len(payload["files"]) == 1
    entry = payload["files"][0]
    assert entry["id"] == file_id
    assert entry["filename"] == "brief.pdf"
    assert entry["included"] is True
    assert entry["file_size_bytes"] > 0
    assert entry["claims_count"] == 0


def test_patch_toggles_included(vault_client):
    upload_res = _upload(vault_client)
    file_id = upload_res.get_json()["id"]

    res = vault_client.patch("/api/projects/default/substrate/" + file_id, json={"included": False})
    assert res.status_code == 200
    assert res.get_json()["included"] is False

    listed = vault_client.get("/api/projects/default/substrate").get_json()
    assert listed["files"][0]["included"] is False


def test_patch_unknown_file_returns_404(vault_client):
    res = vault_client.patch(
        "/api/projects/default/substrate/does-not-exist", json={"included": False}
    )
    assert res.status_code == 404


def test_patch_missing_included_field_returns_400(vault_client):
    upload_res = _upload(vault_client)
    file_id = upload_res.get_json()["id"]
    res = vault_client.patch("/api/projects/default/substrate/" + file_id, json={})
    assert res.status_code == 400


def test_delete_removes_file(vault_client):
    upload_res = _upload(vault_client)
    file_id = upload_res.get_json()["id"]

    res = vault_client.delete("/api/projects/default/substrate/" + file_id)
    assert res.status_code == 200
    assert res.get_json()["ok"] is True

    listed = vault_client.get("/api/projects/default/substrate").get_json()
    assert listed["files"] == []


def test_delete_unknown_file_returns_404(vault_client):
    res = vault_client.delete("/api/projects/default/substrate/does-not-exist")
    assert res.status_code == 404


def test_attach_substrate_provenance_to_tree_matches_value_in_source_text():
    # The paragraph clears the claim floor (_MIN_CLAIM_TOKENS) and shares enough
    # content tokens with a source sentence of the same size.
    # The test's semantics are preserved: a matching paragraph gets
    # provenance attached; page_number and source_id come from the
    # matched source row.
    para_text = (
        "Revenue reached ten million dollars this quarter, "
        "reflecting strong year-over-year growth."
    )
    tree = build_document_from_draft("p1", para_text).model_dump(mode="json")
    locks = [{"canonical_key": "Revenue", "value": 10, "unit": "M"}]
    substrate_rows = [
        {
            "id": "sub-1",
            "filename": "cim.pdf",
            "extracted_text": (
                "The board confirmed revenue reached ten million "
                "dollars this quarter, reflecting strong growth "
                "across all reporting segments."
            ),
            "page_count": 1,
        }
    ]

    updated = attach_substrate_provenance_to_tree(tree, locks, substrate_rows)
    para = updated["body"][0]["children"][0]
    assert para["provenance"], "expected a provenance entry to be attached"
    entry = para["provenance"][0]
    assert entry["source_id"] == "sub-1"
    assert entry["source_name"] == "cim.pdf"


def test_attach_substrate_provenance_to_tree_no_match_when_value_absent():
    tree = build_document_from_draft("p1", "Revenue is $10M this quarter.").model_dump(mode="json")
    locks = [{"canonical_key": "Revenue", "value": 10, "unit": "M"}]
    substrate_rows = [
        {
            "id": "sub-1",
            "filename": "cim.pdf",
            "extracted_text": "No relevant numbers in here at all.",
        }
    ]

    updated = attach_substrate_provenance_to_tree(tree, locks, substrate_rows)
    para = updated["body"][0]["children"][0]
    assert not para.get("provenance")


def test_attach_substrate_provenance_to_tree_handles_empty_inputs():
    tree = build_document_from_draft("p1", "Revenue is $10M this quarter.").model_dump(mode="json")
    assert attach_substrate_provenance_to_tree(tree, [], []) == tree
    assert attach_substrate_provenance_to_tree(tree, [{"value": 10}], []) == tree


# ---------------------------------------------------------------------------
# Anchoring rule against the real demo fixture (tests/fixtures/policy-sample.pdf).
# Its extracted sentences are only 7 and 6 content tokens, so a source-sentence
# floor above that made anchoring impossible and every compile from it ungrounded.
# ---------------------------------------------------------------------------


def _policy_source_rows() -> list[dict]:
    from pathlib import Path

    from pypdf import PdfReader

    pdf = Path(__file__).parent / "fixtures" / "policy-sample.pdf"
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    return [{"id": "sub-policy", "filename": "policy-sample.pdf", "extracted_text": text}]


def _provenance_of(text: str, rows: list[dict]) -> list[dict]:
    tree = build_document_from_draft("p1", text).model_dump(mode="json")
    updated = attach_substrate_provenance_to_tree(tree, [], rows)
    return updated["body"][0]["children"][0].get("provenance") or []


@pytest.mark.parametrize(
    "claim",
    [
        # Restates the $5,000,000 combined single limit sentence.
        "The policy establishes a combined single limit of $5,000,000 for liability coverage.",
        # Restates the other-coverages sentence.
        "Other coverages under this policy are not subject to the liability limit.",
        # Quotes the $5,000,000 sentence verbatim: 7 content tokens, so this only
        # anchors when the claim floor does not exceed the overlap the sentence can
        # supply.
        "The policy liability limit is set at $5,000,000 for combined single limit.",
    ],
)
def test_anchor_matches_short_policy_sentences(claim):
    entries = _provenance_of(claim, _policy_source_rows())
    assert entries, f"expected an anchor for {claim!r}"
    assert entries[0]["source_id"] == "sub-policy"
    assert entries[0]["source_name"] == "policy-sample.pdf"
    assert entries[0]["extracted_quote"]


@pytest.mark.parametrize(
    "claim",
    [
        # Fabricated figure and coverage line: no source sentence carries it.
        "The policy includes $250,000 of cyber coverage for the insured's network operations.",
        # Near miss: shares five content tokens with the $5,000,000 sentence
        # (coefficient 0.71, above both floors) and shares its "000" token, but
        # cites a limit the source does not carry.
        "The policy limit is $250,000 for combined single coverage of the insured.",
        # Short fabricated claim: lowering the claim floor to the anchor overlap
        # must not let stub-sized claims anchor.
        "Cyber coverage of $250,000 applies to network operations.",
    ],
)
def test_anchor_rejects_claims_source_does_not_support(claim):
    # Guard against a vacuous pass: the claim must clear the paragraph floor,
    # otherwise the matcher would skip it for being too short, not for being
    # unsupported.
    assert len(_tokenize(claim)) >= _MIN_CLAIM_TOKENS
    assert _provenance_of(claim, _policy_source_rows()) == []


def test_anchor_rejects_figure_the_matched_sentence_does_not_carry():
    # Same wording as the source, different figure. Token overlap clears every
    # floor, so only the numeric check can reject it — without that check the
    # fabricated $5,000,000 anchors to the source's $2,000,000.
    rows = [
        {
            "id": "sub-x",
            "filename": "policy.pdf",
            "extracted_text": (
                "The policy provides commercial general liability coverage with a "
                "general aggregate limit of $2,000,000 per location."
            ),
        }
    ]
    fabricated = (
        "The policy provides commercial general liability coverage with a general "
        "aggregate limit of $5,000,000 per location."
    )
    assert _provenance_of(fabricated, rows) == []
    # Control: identical wording carrying the source's own figure does anchor, so
    # the rejection above is the figure and not the phrasing.
    assert _provenance_of(fabricated.replace("$5,000,000", "$2,000,000"), rows)
