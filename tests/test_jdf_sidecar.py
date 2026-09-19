"""The JDF sidecar and its round trip.

Two contracts, both observable from outside the app:

* ``GET /api/projects/<id>/export?format=jdf`` serves the document AND what the
  compile decided about it — per-node provenance, each node's verification state,
  the source manifest, the version chain, the drafting model. A reader who takes
  the file away can re-derive every anchor without Assure.
* ``POST /api/projects/<id>/import-jdf`` into a fresh project preserves that: the
  document hashes to the exported one, every anchor still resolves (against the
  manifest the file carries), and the verification states and entailment verdicts
  are unchanged. A sidecar that reproduces the prose but loses the anchors has
  failed the promise the export exists to keep.

No model call: the tree is seeded through ``PUT /jdf`` with the provenance rows a
compile would have written.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from prompt_matrix.db.jdf_repository import ensure_project
from prompt_matrix.db.substrate_repository import save_substrate_entry
from prompt_matrix.services.jdf_sidecar import SIDECAR_FORMAT, audit_jdf_payload
from prompt_matrix.web import create_app

SRC_ID = "sub-sidecartest0001"
SOURCE = (
    "## 3 Wind and hail deductible\n"
    "For coastal and high-wind exposure zones (Suffolk, Norfolk, Essex counties), the wind/hail "
    "deductible is 2 percent of insured value at each location.\n"
    "## 2 General liability\n"
    "The maximum general liability per occurrence is $2,000,000 unless a senior underwriter "
    "approves a documented exception.\n"
)
CLAIM = (
    "For properties in coastal and high-wind exposure zones — Suffolk, Norfolk, and Essex "
    "counties — the wind and hail deductible is 2 percent of the insured value at each location."
)
ANCHOR_QUOTE = (
    "For coastal and high-wind exposure zones (Suffolk, Norfolk, Essex counties), the wind/hail "
    "deductible is 2 percent of insured value at each location."
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "sidecar.db"))
    monkeypatch.setenv("ASSURE_JDF_DIR", str(tmp_path / "jdf"))
    monkeypatch.setenv("SQLITE_USE_POOL", "0")
    monkeypatch.delenv("ASSURE_FROZEN_PROJECTS", raising=False)
    (tmp_path / "jdf").mkdir()
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _node(node_id: str, content: str, verdict: str) -> dict:
    return {
        "type": "paragraph",
        "id": node_id,
        "content": content,
        "entities_referenced": [],
        "provenance": [
            {
                "source_type": "internal_doc",
                "source_name": "wind-policy.md",
                "source_id": SRC_ID,
                "page_number": "1",
                "extracted_quote": ANCHOR_QUOTE,
                "anchor_window": ANCHOR_QUOTE,
                "anchor_window_span": "1-1",
                # What the compile stamps when it reads the model's ``[S<N>]``
                # markers: which numbered source sentence the claim cites, and the
                # page that sentence sits on.
                "cited_id": "S2",
                "page": 1,
            }
        ],
        "meta": {
            "source": "generate_draft",
            "provenance": {
                "source_id": SRC_ID,
                "source_name": "wind-policy.md",
                "page_number": 1,
                "excerpt": content[:120],
                "confidence": 0.92,
                "entailment": {
                    "verdict": verdict,
                    "reasoning": "seeded for the round-trip contract",
                    "model": "seed/model",
                    "checked_at": "2026-09-18T20:00:00+00:00",
                },
            },
        },
        "annotations": {"redhat": [], "z3": []},
    }


TREE = {
    "document_id": "doc-sidecar",
    "meta": {"title": "Sidecar Round Trip", "answer_shape": "memo"},
    "truth_ledger": {"wind_and_hail_deductible": 2.0},
    "body": [
        {
            "type": "section",
            "id": "sec-sidecar",
            "title": "Underwriting Obligations",
            "children": [
                _node("para-anchored", CLAIM, "yes"),
                {
                    "type": "paragraph",
                    "id": "para-unanchored",
                    "content": "The insured must also notify the carrier within five business days.",
                    "entities_referenced": [],
                    "provenance": [],
                    "meta": {"source": "generate_draft"},
                    "annotations": {"redhat": [], "z3": []},
                },
            ],
            "meta": {},
            "annotations": {"redhat": [], "z3": []},
        }
    ],
}


def _seed_project(client, project_id: str = "sidecar-src") -> None:
    ensure_project(project_id, "Sidecar Source")
    save_substrate_entry(
        project_id,
        filename="wind-policy.md",
        page_count=1,
        extracted_text=SOURCE,
        entry_id=SRC_ID,
    )
    res = client.put(f"/api/projects/{project_id}/jdf", json={"document": TREE, "mutation_type": "compile"})
    assert res.status_code == 200, res.get_data(as_text=True)


def test_sidecar_carries_verification_manifest_chain_and_model(client):
    _seed_project(client)
    res = client.get("/api/projects/sidecar-src/export?format=jdf")
    assert res.status_code == 200
    assert "vnd.assure.jdf+json" in res.headers["Content-Type"]
    assert res.headers["Content-Disposition"].endswith('.jdf.json"')

    sidecar = res.get_json()
    assert sidecar["format"] == SIDECAR_FORMAT
    document = sidecar["document"]
    cited = document["body"][0]["children"][0]
    assert cited["meta"]["provenance"]["source_id"] == SRC_ID
    # The exported node carries the citation list a reader has to be able to act
    # on: which numbered source sentence, from which file, on which page, and what
    # the entailment check made of the claim.
    assert cited["meta"]["provenance"]["cited_ids"] == ["S2"]
    assert cited["meta"]["provenance"]["sentences"] == [
        {"id": "S2", "text": ANCHOR_QUOTE, "filename": "wind-policy.md", "page": 1}
    ]
    assert cited["meta"]["provenance"]["verdict"] == "supported"
    # Adding the citation list must not cost the record already stored on the node.
    assert cited["meta"]["provenance"]["entailment"]["verdict"] == "yes"
    uncited = document["body"][0]["children"][1]["meta"]["provenance"]
    assert uncited["cited_ids"] == []
    assert uncited["sentences"] == []
    assert uncited["verdict"] == "unanchored"

    states = {entry["node_id"]: entry["verification_state"] for entry in sidecar["nodes"]}
    assert states["para-anchored"] == "supported"
    assert states["para-unanchored"] == "unanchored"
    assert sidecar["verification"]["states"]["supported"] == 1

    manifest = sidecar["source_manifest"]
    assert [row["source_id"] for row in manifest] == [SRC_ID]
    assert manifest[0]["filename"] == "wind-policy.md"
    assert manifest[0]["text_sha256"]

    chain = sidecar["version_chain"]
    assert chain["ordered"] == "oldest_first"
    assert chain["revisions"][-1]["document_sha256"] == sidecar["document_sha256"]
    assert chain["revisions"][-1]["mutation_type"] == "compile"

    # No compile ran, so the model is reported as absent with its reason rather
    # than left out or invented.
    assert sidecar["drafting_model"]["model"] is None
    assert sidecar["drafting_model"]["reason"]


def test_sidecar_bundle_holds_the_pdf_and_the_jdf(client):
    _seed_project(client)
    sidecar_hash = client.get("/api/projects/sidecar-src/export?format=jdf").get_json()["document_sha256"]
    res = client.get("/api/projects/sidecar-src/export?format=bundle")
    assert res.status_code == 200
    assert res.headers["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(res.data))
    names = archive.namelist()
    assert len(names) == 2
    assert any(name.endswith("-dossier.pdf") for name in names)
    inner = json.loads(archive.read([n for n in names if n.endswith(".jdf.json")][0]))
    assert inner["format"] == SIDECAR_FORMAT
    assert inner["document_sha256"] == sidecar_hash


def test_round_trip_into_a_fresh_project_keeps_anchors_and_state(client):
    _seed_project(client)
    sidecar = client.get("/api/projects/sidecar-src/export?format=jdf").get_json()

    fresh = (client.post("/api/projects", json={"title": "Round Trip Fresh"}).get_json() or {})["id"]
    res = client.post(f"/api/projects/{fresh}/import-jdf", json=sidecar)
    assert res.status_code == 200, res.get_data(as_text=True)
    report = res.get_json()["round_trip"]

    assert report["document_sha256_matches"] is True
    assert report["anchors_total"] == 1
    assert report["anchors_resolved"] == 1
    assert report["anchors_unresolved"] == []
    assert report["verification_states"]["supported"] == 1
    assert report["verification_states"]["unanchored"] == 1
    assert report["entailment_verdicts"] == {"yes": 1}

    loaded = client.get(f"/api/projects/{fresh}/jdf").get_json()["document"]
    node = loaded["body"][0]["children"][0]
    assert node["provenance"][0]["source_id"] == SRC_ID
    assert node["provenance"][0]["extracted_quote"] == (
        TREE["body"][0]["children"][0]["provenance"][0]["extracted_quote"]
    )
    assert node["meta"]["provenance"]["entailment"]["verdict"] == "yes"
    # The citation the file carried in is what the fresh project renders from: the
    # source sentence's id and page survive the load, not just its text.
    assert node["provenance"][0]["cited_id"] == "S2"
    assert node["provenance"][0]["page"] == 1
    assert node["meta"]["provenance"]["cited_ids"] == ["S2"]
    assert node["meta"]["provenance"]["sentences"] == [
        {"id": "S2", "text": ANCHOR_QUOTE, "filename": "wind-policy.md", "page": 1}
    ]
    assert node["meta"]["provenance"]["verdict"] == "supported"
    assert loaded["meta"]["answer_shape"] == "memo"


def test_round_trip_reports_an_anchor_whose_source_is_not_in_the_manifest(client):
    """A stripped manifest must read as unresolved, not as a clean import."""
    _seed_project(client)
    sidecar = client.get("/api/projects/sidecar-src/export?format=jdf").get_json()
    sidecar["source_manifest"] = []

    report = audit_jdf_payload(sidecar)
    assert report["anchors_total"] == 1
    assert report["anchors_resolved"] == 0
    assert report["anchors_unresolved"] == [{"node_id": "para-anchored", "source_id": SRC_ID}]


def test_frozen_project_refuses_a_cold_compile_and_allows_a_warm_one(client, monkeypatch):
    """The demo's document is a pinned artifact: cold means refused, warm means replayed."""
    monkeypatch.setenv("ASSURE_FROZEN_PROJECTS", "sidecar-frozen")
    from prompt_matrix.routers.draft import frozen_cold_compile_blocked, frozen_projects

    assert frozen_projects() == {"sidecar-frozen"}
    assert frozen_cold_compile_blocked(project_id="sidecar-frozen", cached_hit=False)
    assert frozen_cold_compile_blocked(project_id="sidecar-frozen", cached_hit=True) == ""
    assert frozen_cold_compile_blocked(project_id="sidecar-frozen", cached_hit=False, force=True) == ""
    assert frozen_cold_compile_blocked(project_id="sidecar-other", cached_hit=False) == ""

    ensure_project("sidecar-frozen", "Frozen")
    save_substrate_entry(
        "sidecar-frozen",
        filename="wind-policy.md",
        page_count=1,
        extracted_text=SOURCE,
        entry_id="sub-sidecarfrozen01",
    )
    res = client.post(
        "/api/projects/sidecar-frozen/draft/stream",
        json={"intent": "Summarize the coverage limits", "substrate_file_ids": ["sub-sidecarfrozen01"]},
    )
    body = res.get_data(as_text=True)
    assert "frozen_project_cold_compile" in body
    assert "frozen document" in body
