"""The source JDF as a persisted, addressable artifact (services/source_jdf, 2026-09-28).

Persisted after import and after a Sources upload (object store under
``ASSURE_DATA_DIR/objects``), recorded on the ingest job, the Parsure report and
the Assure tree; streamed by ``GET …/documents/<doc>/source.jdf``; element ids
equal to the ones on Parsure fields; a text selection maps back to elements;
and both compile streams carry a selection anchor that says ``verbatim`` only
when the text was re-found in the sources.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from prompt_matrix.services import field_extractor as fx
from prompt_matrix.services import source_jdf as sj
from tests.test_draft import SOURCE_FILLER, _FakeGovernor, _parse_sse
from tests.test_inquire_stream import _mock_result, _sample_tree
from tests.test_quality_probe import crisp_pdf
from tests.test_v1_orchestrator import POLICY_LINES, RESULT, VERIFICATION, jdf_cli_bundle


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "source_jdf.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    monkeypatch.setenv("PARSE_ASYNC", "0")
    monkeypatch.setenv("SUBSTRATE_ASYNC_UPLOAD", "0")
    monkeypatch.setenv("PEM_OMP_CACHE", "0")
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    # An empty string, not delenv: ``cloud_billing`` runs ``load_dotenv(override=False)``
    # on app import, which re-populates a *deleted* variable from the developer's
    # ``.env`` and sent one test write to the real bucket (2026-09-28). An empty
    # value is left alone and selects the local store.
    monkeypatch.setenv("ASSURE_S3_BUCKET", "")
    monkeypatch.delenv("OMP_SERVER", raising=False)
    monkeypatch.setattr("prompt_matrix.omp_client.API_KEY_PATH", str(tmp_path / "no-omp-key"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.web import create_app

    init_db()
    for pid in ("default", "srcjdf", "other", "srcvault"):
        ensure_project(pid)
    return create_app(require_auth=False).test_client()


def _fake_converter(monkeypatch):
    import prompt_matrix.services.jdf_converter as conv

    monkeypatch.setattr(conv, "pdf_to_parse_bundle", lambda data, **kw: jdf_cli_bundle())


def _import(monkeypatch, project="srcjdf"):
    _fake_converter(monkeypatch)
    from prompt_matrix.db.ingest_jobs_repository import create_job
    from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

    job_id = create_job(project, kind="import_pdf", filename="policy.pdf")
    payload = ingest_pdf_for_project(project, "policy.pdf", crisp_pdf(), job_id=job_id)
    return job_id, payload


# --------------------------------------------------------------------------
# Persistence: import path
# --------------------------------------------------------------------------

def test_import_persists_the_source_jdf_and_records_the_key_everywhere(client, tmp_path, monkeypatch):
    from prompt_matrix.db.ingest_jobs_repository import get_job
    from prompt_matrix.db.jdf_repository import fetch_latest_jdf_or_empty
    from prompt_matrix.db.parsure_repository import get_latest_report

    job_id, payload = _import(monkeypatch)
    src = payload["source_jdf"]
    assert src, "the import must report where the source JDF went"
    key = src["key"]
    assert key == f"documents/srcjdf/doc-srcjdf/{payload['revision_id']}.jdf"
    assert src["url"] == "/api/projects/srcjdf/documents/doc-srcjdf/source.jdf"
    assert src["pages"] == 1 and src["elements"] == len(POLICY_LINES)

    stored = tmp_path / "objects" / key
    assert stored.is_file(), "written to the local object store under ASSURE_DATA_DIR/objects"
    doc = json.loads(stored.read_text("utf-8"))
    assert doc["pages"][0]["pageSize"]["width"] == 209.9, "jdf-cli's own document, mm positions intact"
    assert doc["meta"]["assure"]["element_id_policy"] == fx.NODE_ID_POLICY
    assert all(el["assure"]["element_id"] for el in doc["pages"][0]["elements"])

    # The ingest job row.
    assert get_job(job_id)["source_jdf_key"] == key
    # The Parsure report.
    report = get_latest_report("srcjdf")
    assert report["source_jdf"]["key"] == key and report["source_jdf"]["url"] == src["url"]
    assert report["source_jdf"]["pages"] == 1 and report["source_jdf"]["elements"] == len(POLICY_LINES)
    assert "source_jdf" not in (report.get("intake_extra") or {}), "consumed by the report, not an unread extra"
    # The saved revision's tree.
    tree = fetch_latest_jdf_or_empty("srcjdf")
    assert tree["meta"]["source_jdf"]["key"] == key


def test_a_bundle_without_a_jdf_cli_document_stores_nothing(client, monkeypatch):
    """PyMuPDF fallback / Textract: no raw jdf-cli document exists, so no key is
    invented and the route answers 404."""
    import prompt_matrix.services.jdf_converter as conv

    def boom(data, **kw):
        raise conv.JdfConversionError("jdf-cli missing")

    monkeypatch.setattr(conv, "pdf_to_parse_bundle", boom)
    from prompt_matrix.db.ingest_jobs_repository import create_job, get_job
    from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

    job_id = create_job("srcjdf", kind="import_pdf", filename="policy.pdf")
    payload = ingest_pdf_for_project("srcjdf", "policy.pdf", crisp_pdf(), job_id=job_id)
    assert payload["parser_name"] == "pymupdf" and payload["source_jdf"] is None
    assert get_job(job_id)["source_jdf_key"] is None
    res = client.get("/api/projects/srcjdf/documents/doc-srcjdf/source.jdf")
    assert res.status_code == 404
    assert res.get_json() == {"ok": False, "error": "no source document is stored for this document"}


# --------------------------------------------------------------------------
# Persistence: Sources panel path (no ingest job on the synchronous route)
# --------------------------------------------------------------------------

def test_sources_upload_persists_under_the_vault_row_id_and_resolves_via_the_report(client, tmp_path, monkeypatch):
    _fake_converter(monkeypatch)
    res = client.post(
        "/api/projects/srcvault/substrate/upload",
        data={"file": (io.BytesIO(crisp_pdf()), "policy.pdf")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    src = body["source_jdf"]
    assert src and src["key"].startswith(f"documents/srcvault/{body['id']}/sha-")
    assert (tmp_path / "objects" / src["key"]).is_file()

    from prompt_matrix.db.parsure_repository import get_latest_report

    report = get_latest_report("srcvault")
    assert report["document_id"] == str(body["id"]) and report["source_jdf"]["key"] == src["key"]

    # No job row on the synchronous path: the route resolves through the report.
    got = client.get(f"/api/projects/srcvault/documents/{body['id']}/source.jdf")
    assert got.status_code == 200
    assert got.get_json()["pages"][0]["elements"][0]["content"] == POLICY_LINES[0]


def test_queued_sources_upload_records_the_key_on_its_job(client, monkeypatch):
    """The queued path (Celery is eager under test): the revision segment is
    the job id and the job row carries the key, so the route resolves through
    ``ingest_jobs`` without a report lookup."""
    monkeypatch.setenv("SUBSTRATE_ASYNC_UPLOAD", "1")
    _fake_converter(monkeypatch)
    res = client.post(
        "/api/projects/srcvault/substrate/upload",
        data={"file": (io.BytesIO(crisp_pdf()), "policy.pdf")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 202, res.get_data(as_text=True)
    job_id = res.get_json()["job_id"]
    from prompt_matrix.db.ingest_jobs_repository import get_job

    job = get_job(job_id)
    assert job["status"] == "done", job
    key = job["source_jdf_key"]
    assert key == f"documents/srcvault/{job['substrate_file_id']}/{job_id}.jdf"
    assert sj.resolve_source_jdf_key("srcvault", str(job["substrate_file_id"])) == key
    got = client.get(f"/api/projects/srcvault/documents/{job['substrate_file_id']}/source.jdf")
    assert got.status_code == 200 and got.headers["ETag"] == f'"{key}"'


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def test_route_streams_the_stored_json_with_etag_and_private_cache(client, monkeypatch):
    _job, payload = _import(monkeypatch)
    key = payload["source_jdf"]["key"]
    res = client.get("/api/projects/srcjdf/documents/doc-srcjdf/source.jdf")
    assert res.status_code == 200
    assert res.mimetype == "application/json"
    assert res.headers["Cache-Control"] == "private, max-age=3600"
    assert res.headers["ETag"] == f'"{key}"'
    doc = res.get_json()
    assert [el["content"] for el in doc["pages"][0]["elements"]] == POLICY_LINES

    again = client.get(
        "/api/projects/srcjdf/documents/doc-srcjdf/source.jdf", headers={"If-None-Match": f'"{key}"'}
    )
    assert again.status_code == 304

    # ?revision= picks one stored revision; an unknown one is 404, not the latest.
    ok = client.get(f"/api/projects/srcjdf/documents/doc-srcjdf/source.jdf?revision={payload['revision_id']}")
    assert ok.status_code == 200
    missing = client.get("/api/projects/srcjdf/documents/doc-srcjdf/source.jdf?revision=rev-nope")
    assert missing.status_code == 404


def test_latest_revision_wins(client, monkeypatch):
    _job1, first = _import(monkeypatch)
    _job2, second = _import(monkeypatch)
    assert first["revision_id"] != second["revision_id"]
    res = client.get("/api/projects/srcjdf/documents/doc-srcjdf/source.jdf")
    assert res.headers["X-Source-Jdf-Key"] == second["source_jdf"]["key"]


def test_source_json_describes_and_maps_a_selection(client, monkeypatch):
    _job, payload = _import(monkeypatch)
    info = client.get("/api/projects/srcjdf/documents/doc-srcjdf/source.json").get_json()
    assert info["ok"] is True
    assert info["key"] == payload["source_jdf"]["key"]
    assert info["url"] == "/api/projects/srcjdf/documents/doc-srcjdf/source.jdf"
    assert info["pages"] == 1 and info["elements"] == len(POLICY_LINES)
    assert info["stored_at"] and info["revision"] == payload["revision_id"]

    hit = client.get(
        "/api/projects/srcjdf/documents/doc-srcjdf/source.json",
        query_string={"text": "total   premium: $1,250.00", "page": "1"},
    ).get_json()
    assert hit["ok"] is True and hit["found"] is True and hit["page"] == 1
    bundle = jdf_cli_bundle()
    index = sj.element_index(bundle["jdf"], bundle["chunks"])
    (want,) = [eid for eid, e in index.items() if e["text"] == "Total Premium: $1,250.00"]
    assert hit["element_ids"] == [want]
    assert hit["bbox_rel"] == index[want]["bbox_rel"]

    miss = client.get(
        "/api/projects/srcjdf/documents/doc-srcjdf/source.json", query_string={"text": "not on this page"}
    ).get_json()
    assert miss["found"] is False and miss["element_ids"] == [] and miss["bbox_rel"] is None

    bad = client.get("/api/projects/srcjdf/documents/doc-srcjdf/source.json", query_string={"text": "x", "page": "one"})
    assert bad.status_code == 400


def test_unknown_document_is_404(client):
    res = client.get("/api/projects/default/documents/doc-nothing/source.jdf")
    assert res.status_code == 404
    assert res.get_json() == {"ok": False, "error": "no source document is stored for this document"}
    assert client.get("/api/projects/default/documents/doc-nothing/source.json").status_code == 404


def test_another_project_cannot_read_the_document(client, monkeypatch):
    from prompt_matrix.db.ingest_jobs_repository import create_job, set_source_jdf_key

    _job, payload = _import(monkeypatch)
    key = payload["source_jdf"]["key"]
    # Same document id, other project: nothing of its own is stored.
    assert client.get("/api/projects/other/documents/doc-srcjdf/source.jdf").status_code == 404
    # A job row in the other project pointing at this project's key is refused too.
    stray = create_job("other", kind="import_pdf", filename="x.pdf")
    set_source_jdf_key(stray, key)
    assert client.get("/api/projects/other/documents/doc-srcjdf/source.jdf").status_code == 404
    assert sj.resolve_source_jdf_key("other", "doc-srcjdf") is None
    assert not sj.key_in_project(key, "other") and sj.key_in_project(key, "srcjdf")


# --------------------------------------------------------------------------
# Element addressing
# --------------------------------------------------------------------------

def test_element_ids_equal_the_parsure_field_ids_on_the_same_bundle(client):
    from prompt_matrix.services import v1_orchestrator as orch

    bundle = jdf_cli_bundle()
    index = sj.element_index(bundle["jdf"], bundle["chunks"])
    layout_ids = {seg["element_id"] for segs in fx.page_layout(bundle) for seg in segs}
    assert set(index) == layout_ids and len(index) == len(POLICY_LINES)
    assert all(eid.startswith("c1:") for eid in index), "the chunk id is the eid-v1 prefix"
    assert all(e["bbox_rel"] and len(e["bbox_rel"]) == 4 for e in index.values())

    report = orch.build_report(
        "default", bundle=bundle, verification=VERIFICATION, filename="policy.pdf", result=RESULT, job_id="job-1", intake=None
    )
    field_ids = {f.get("element_id") for f in report["fields"] if f.get("element_id")}
    assert field_ids, "the fixture yields fields with element ids"
    assert field_ids <= set(index)
    assert report["source_jdf"] is None, "no descriptor on intake: the report says none, not a guessed URL"


def test_stamped_ids_survive_the_round_trip_without_the_chunk_list(client, tmp_path):
    bundle = jdf_cli_bundle()
    desc = sj.persist_source_jdf("default", "doc-rt", "rev-rt", bundle["jdf"], chunks=bundle["chunks"])
    assert desc["key"] == "documents/default/doc-rt/rev-rt.jdf"
    assert "assure" not in bundle["jdf"]["pages"][0]["elements"][0], "the in-memory bundle is left as jdf-cli produced it"
    stored = sj.load_source_jdf(desc["key"])
    assert set(sj.element_index(stored)) == set(sj.element_index(bundle["jdf"], bundle["chunks"]))


def test_find_elements_for_text_spans_elements_and_refuses_the_absent(client):
    bundle = jdf_cli_bundle()
    jdf, chunks = bundle["jdf"], bundle["chunks"]
    index = sj.element_index(jdf, chunks)
    by_text = {e["text"]: eid for eid, e in index.items()}

    one = sj.find_elements_for_text(jdf, "Liability Limit: $100,000", chunks=chunks)
    assert one["found"] and one["element_ids"] == [by_text["Liability Limit: $100,000"]] and one["page"] == 1

    two = sj.find_elements_for_text(jdf, "VIN: 1HGCM82633A004352\n   Total Premium", chunks=chunks)
    assert two["element_ids"] == [by_text["VIN: 1HGCM82633A004352"], by_text["Total Premium: $1,250.00"]]
    b1, b2 = index[two["element_ids"][0]]["bbox_rel"], index[two["element_ids"][1]]["bbox_rel"]
    assert two["bbox_rel"] == [min(b1[0], b2[0]), min(b1[1], b2[1]), max(b1[2], b2[2]), max(b1[3], b2[3])]

    assert sj.find_elements_for_text(jdf, "Total Premium", page=2, chunks=chunks)["found"] is False
    assert sj.find_elements_for_text(jdf, "premium of $9,999", chunks=chunks) == {
        "element_ids": [], "page": None, "bbox_rel": None, "found": False, "matched_by": None,
    }
    assert one["matched_by"] == "verbatim"
    assert sj.find_elements_for_text(jdf, "   ", chunks=chunks)["found"] is False


_WRAPPED_LINES = [
    "NOTICE OF RENEWAL",
    "The insur-",
    "ance premium is due on the \u201ceffective\u201d date.",
    "\ufb01nal notice \u2013 pre\u00admium of $1,250.00",
    "Total\u00a0Premium: $1,250.00",
    "Insured\u2019s address on \ufb02oor 2",
    "Office of the insurer",
]


def _lookup(text, page=None):
    bundle = jdf_cli_bundle(_WRAPPED_LINES)
    jdf, chunks = bundle["jdf"], bundle["chunks"]
    index = sj.element_index(jdf, chunks)
    by_text = {e["text"]: eid for eid, e in index.items()}
    return sj.find_elements_for_text(jdf, text, page=page, chunks=chunks), by_text, index


def test_verbatim_match_is_reported_as_verbatim(client):
    hit, by_text, _ = _lookup("NOTICE   of renewal")
    assert hit["found"] and hit["matched_by"] == "verbatim" and hit["element_ids"] == [by_text["NOTICE OF RENEWAL"]]


@pytest.mark.parametrize(
    "selection, texts, how",
    [
        # line-wrap hyphen across two elements: "insur-\nance" → "insurance"
        ("insurance premium is due", ["The insur-", "ance premium is due on the \u201ceffective\u201d date."], "normalised"),
        # curly quotes in the page, straight quotes in the selection
        ('on the "effective" date', ["ance premium is due on the \u201ceffective\u201d date."], "normalised"),
        # the same curly quotes on both sides: nothing to repair, verbatim
        ("on the \u201ceffective\u201d date.", ["ance premium is due on the \u201ceffective\u201d date."], "verbatim"),
        # ligature ﬁ in the page, plain "fi" in the selection; en dash vs hyphen; soft hyphen removed
        ("final notice - premium of $1,250.00", ["\ufb01nal notice \u2013 pre\u00admium of $1,250.00"], "normalised"),
        # ligature in the selection, plain letters in the page (the reverse direction)
        ("O\ufb03ce of the insurer", ["Office of the insurer"], "normalised"),
        # the same ligature on both sides is verbatim
        ("\ufb01nal notice", ["\ufb01nal notice \u2013 pre\u00admium of $1,250.00"], "verbatim"),
        # a non-breaking space is whitespace to the verbatim comparison already
        ("Total Premium: $1,250.00", ["Total\u00a0Premium: $1,250.00"], "verbatim"),
        # curly apostrophe and ligature ﬂ
        ("Insured's address on floor 2", ["Insured\u2019s address on \ufb02oor 2"], "normalised"),
    ],
)
def test_normalised_matches_are_exact_on_ids_and_say_so(client, selection, texts, how):
    hit, by_text, index = _lookup(selection)
    assert hit["found"] is True and hit["page"] == 1, (selection, hit)
    assert hit["matched_by"] == how
    assert hit["element_ids"] == [by_text[t] for t in texts]
    boxes = [index[e]["bbox_rel"] for e in hit["element_ids"]]
    assert hit["bbox_rel"] == [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def test_normalisation_never_invents_a_match(client):
    for wrong in ("insurence premium", "premium is due tomorrow", "Total Premium: $1,250.01", "well-known"):
        hit, _, _ = _lookup(wrong)
        assert hit == {"element_ids": [], "page": None, "bbox_rel": None, "found": False, "matched_by": None}, wrong
    # A hyphen that is not a line wrap (space before it, capital after) is kept.
    assert sj.normalise_text("Policy - Auto and X-\nRay") == "policy - auto and x- ray"
    assert sj.normalise_text("insur-\nance, insur- ance, pre\u00admium") == "insurance, insurance, premium"


def test_source_json_reports_how_the_selection_matched(client, monkeypatch):
    import prompt_matrix.services.jdf_converter as conv

    monkeypatch.setattr(conv, "pdf_to_parse_bundle", lambda data, **kw: jdf_cli_bundle(_WRAPPED_LINES))
    from prompt_matrix.db.ingest_jobs_repository import create_job
    from prompt_matrix.services.pdf_ingest import ingest_pdf_for_project

    ingest_pdf_for_project("srcjdf", "renewal.pdf", crisp_pdf(), job_id=create_job("srcjdf", kind="import_pdf", filename="renewal.pdf"))
    url = "/api/projects/srcjdf/documents/doc-srcjdf/source.json"
    assert client.get(url, query_string={"text": "NOTICE OF RENEWAL"}).get_json()["matched_by"] == "verbatim"
    fixed = client.get(url, query_string={"text": "insurance premium is due"}).get_json()
    assert fixed["found"] is True and fixed["matched_by"] == "normalised" and len(fixed["element_ids"]) == 2
    assert client.get(url, query_string={"text": "insurence"}).get_json()["matched_by"] is None


# --------------------------------------------------------------------------
# Selection anchor passthrough: compile stream
# --------------------------------------------------------------------------

_SOURCE = "The policy liability limit is set at $5,000,000 for combined single limit."


_ROWS = [{"id": "sub-1", "filename": "policy.pdf", "extracted_text": _SOURCE + SOURCE_FILLER}]


def _draft_frames(monkeypatch, selection, *, rows=None, intent="Restate the limit."):
    from prompt_matrix.routers.draft import run_draft_pipeline

    rows = _ROWS if rows is None else rows
    draft = _SOURCE + "\n\nPolicy liability limit=5000000."

    def fake_stream(_gov, _messages, *, target_ai=None, cancel_check=None):
        yield 'event: token\ndata: {"type": "token", "delta": "The"}\n\n'
        yield (draft, 10, 5, "anthropic/claude-3-5-sonnet-20241022")

    def fake_locks(_text):
        return [
            {"canonical_key": "policy liability limit", "value": 5000000, "metric": "policy liability limit", "confidence": 0.9}
        ], "bedrock/us.anthropic.claude-sonnet-5-5"

    def stub_check(_claim, _source, *, project_id=""):
        return {"verdict": "yes", "reasoning": "The source states it.", "model": "stub/model", "checked_at": "2026-09-28T00:00:00+00:00"}

    monkeypatch.setattr("prompt_matrix.routers.draft._stream_model", fake_stream)
    monkeypatch.setattr("prompt_matrix.routers.draft.run_lock_inference", fake_locks)
    monkeypatch.setattr("prompt_matrix.routers.draft.check_entailment", stub_check)
    monkeypatch.setattr("prompt_matrix.routers.draft.fetch_substrate_entries_by_ids", lambda _pid, _ids: rows)
    frames = list(
        run_draft_pipeline("default", intent=intent, substrate_file_ids=[str(r["id"]) for r in rows], governor=_FakeGovernor(), selection=selection)
    )
    events = [_parse_sse(f) for f in frames if f.startswith("event:") or f.startswith("data:")]
    return next(data for _ev, data in events if isinstance(data, dict) and data.get("type") == "compiled")


def test_compile_records_the_selection_anchor_and_says_verbatim_only_when_refound(client, monkeypatch):
    selection = {
        "text": "liability limit is set at   $5,000,000",
        "page": 1,
        "element_ids": ["c1:abcdef012345"],
        "document_id": "doc-default",
        "source_jdf": "/api/projects/default/documents/doc-default/source.jdf",
    }
    compiled = _draft_frames(monkeypatch, selection)
    anchor = compiled["document"]["meta"]["selection_anchor"]
    assert anchor["text"] == selection["text"] and anchor["page"] == 1
    assert anchor["element_ids"] == ["c1:abcdef012345"] and anchor["document_id"] == "doc-default"
    assert anchor["source_jdf"] == selection["source_jdf"]
    assert anchor["verbatim"] is True and anchor["checked_against"] == 1
    assert compiled["document"]["meta"]["answer_shape"], "the shape is still recorded beside it"

    absent = _draft_frames(monkeypatch, {"text": "a sentence the source never carried", "page": 1, "element_ids": []})
    assert absent["document"]["meta"]["selection_anchor"]["verbatim"] is False

    plain = _draft_frames(monkeypatch, None)
    assert "selection_anchor" not in plain["document"]["meta"]
    assert plain["document"]["meta"]["source_jdf"] is None, "no stored document for sub-1: None, not a guessed URL"
    assert "source_jdfs" not in plain["document"]["meta"]


def _upload_source(client, monkeypatch, filename, project="default"):
    _fake_converter(monkeypatch)
    res = client.post(
        f"/api/projects/{project}/substrate/upload",
        data={"file": (io.BytesIO(crisp_pdf()), filename)},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()


def test_descriptor_for_document_reads_the_report_and_says_none_otherwise(client, monkeypatch):
    body = _upload_source(client, monkeypatch, "a.pdf")
    desc = sj.descriptor_for_document("default", str(body["id"]))
    assert desc == body["source_jdf"], "the descriptor the intake report recorded, verbatim"
    assert desc["key"].startswith(f"documents/default/{body['id']}/") and desc["pages"] == 1
    assert sj.descriptor_for_document("default", "doc-nothing") is None
    assert sj.descriptor_for_document("other", str(body["id"])) is None
    # A key no report carries: the descriptor is read from the stored document itself.
    stored = sj.persist_source_jdf("default", "doc-bare", "rev-bare", jdf_cli_bundle()["jdf"], chunks=jdf_cli_bundle()["chunks"])
    from prompt_matrix.db.ingest_jobs_repository import create_job, set_source_jdf_key

    set_source_jdf_key(create_job("default", kind="import_pdf", filename="bare.pdf"), stored["key"])
    bare = sj.descriptor_for_document("default", "doc-bare")
    assert bare["key"] == stored["key"] and bare["url"] == stored["url"]
    assert bare["pages"] == 1 and bare["elements"] == len(POLICY_LINES) and bare["stored_at"] == stored["stored_at"]


def test_compile_carries_the_source_jdf_of_its_sources(client, monkeypatch):
    a = _upload_source(client, monkeypatch, "a.pdf")
    b = _upload_source(client, monkeypatch, "b.pdf")
    row = lambda body: {"id": body["id"], "filename": body["filename"], "extracted_text": _SOURCE + SOURCE_FILLER}  # noqa: E731

    one = _draft_frames(monkeypatch, None, rows=[row(a)])
    meta = one["document"]["meta"]
    assert meta["source_jdf"] == a["source_jdf"] and "source_jdfs" not in meta

    two = _draft_frames(monkeypatch, None, rows=[row(b), row(a)], intent="Restate the limit again.")
    meta = two["document"]["meta"]
    assert meta["source_jdf"] == b["source_jdf"], "the first cited source is the primary"
    assert meta["source_jdfs"] == [b["source_jdf"], a["source_jdf"]]

    mixed = _draft_frames(monkeypatch, None, rows=[_ROWS[0], row(a)], intent="Restate the limit once more.")
    meta = mixed["document"]["meta"]
    assert meta["source_jdf"] == a["source_jdf"] and "source_jdfs" not in meta, "a source with nothing stored is skipped, never invented"


def test_cache_key_separates_selections_and_keeps_a_plain_compile_warm(client):
    from prompt_matrix.routers import draft

    plain = draft._compile_cache_key("default", "Restate.", None, "ctx", "model/x")
    assert plain == draft._compile_cache_key("default", "Restate.", None, "ctx", "model/x", selection_text=None)
    assert plain == draft._compile_cache_key("default", "Restate.", None, "ctx", "model/x", selection_text="   ")
    s1 = draft._compile_cache_key("default", "Restate.", None, "ctx", "model/x", selection_text="Liability Limit: $100,000")
    s2 = draft._compile_cache_key("default", "Restate.", None, "ctx", "model/x", selection_text="Total Premium: $1,250.00")
    assert len({plain, s1, s2}) == 3
    assert s1 == draft._compile_cache_key("default", "Restate.", None, "ctx", "model/x", selection_text="Liability Limit: $100,000")


def test_a_cache_replay_carries_the_current_selection_not_the_cached_one(client, monkeypatch):
    monkeypatch.setenv("PEM_OMP_CACHE", "1")
    text = "liability limit is set at $5,000,000"
    first = _draft_frames(monkeypatch, {"text": text, "page": 1, "element_ids": ["c1:aaaaaaaaaaaa"]})
    assert not first.get("cache_hit") and first["document"]["meta"]["selection_anchor"]["element_ids"] == ["c1:aaaaaaaaaaaa"]

    replay = _draft_frames(monkeypatch, {"text": text, "page": 2, "element_ids": ["c1:bbbbbbbbbbbb"]})
    assert replay.get("cache_hit") is True, "same ask, same selection text: served from the cache"
    anchor = replay["document"]["meta"]["selection_anchor"]
    assert anchor["element_ids"] == ["c1:bbbbbbbbbbbb"] and anchor["page"] == 2, "the request's selection, not the entry's"
    assert anchor["verbatim"] is True and anchor["checked_against"] == 1, "verbatim re-checked on replay"

    other = _draft_frames(monkeypatch, {"text": "combined single limit", "page": 1, "element_ids": []})
    assert not other.get("cache_hit"), "a different selection text is a different cache entry"


def test_a_cache_replay_drops_a_stale_anchor_when_the_request_has_none(client, monkeypatch):
    monkeypatch.setenv("PEM_OMP_CACHE", "1")
    from prompt_matrix.routers import draft
    from prompt_matrix.services.omp_memory import load_ast_cache, save_ast_cache

    first = _draft_frames(monkeypatch, None)
    assert not first.get("cache_hit") and "selection_anchor" not in first["document"]["meta"]
    key = draft._compile_cache_key("default", "Restate the limit.", None, draft._build_substrate_context(_ROWS), draft._draft_route_model(None))
    entry = load_ast_cache(key)
    assert entry and entry.get("compiled"), "the compile wrote its entry under the composed key"
    # An entry written by earlier code that carried a selection onto every replay.
    for frame in ("compiled", "verified"):
        entry[frame]["document"]["meta"]["selection_anchor"] = {"text": "stale", "element_ids": ["c1:dead"], "verbatim": True}
    save_ast_cache(key, "default", entry)

    replay = _draft_frames(monkeypatch, None)
    assert replay.get("cache_hit") is True
    assert "selection_anchor" not in replay["document"]["meta"]
    assert replay["document"]["meta"]["source_jdf"] is None, "source_jdf is resolved again on replay too"


def test_draft_stream_accepts_a_selection_and_uses_its_text_as_the_excerpt(client, monkeypatch):
    seen = {}

    def fake_pipeline(project_id, **kwargs):
        seen.update(kwargs)
        yield 'event: complete\ndata: {"type": "complete", "ok": true}\n\n'

    monkeypatch.setattr("prompt_matrix.routers.draft.run_draft_pipeline", fake_pipeline)
    selection = {"text": "Liability Limit: $100,000", "page": 1, "element_ids": ["c1:0123456789ab"], "document_id": "doc-default"}
    res = client.post(
        "/api/projects/default/draft/stream",
        json={"intent": "", "compileType": "selection", "selection": selection, "substrate_file_ids": ["sub-1"]},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    res.get_data()
    assert seen["selection"]["text"] == selection["text"] and seen["selection"]["element_ids"] == ["c1:0123456789ab"]
    assert seen["selection"]["source_jdf"] is None
    assert seen["intent"].endswith("Liability Limit: $100,000"), "the selection text is the excerpt when content is absent"

    # Type validation: a page that is not an int, ids that are not strings, text too long.
    for bad in (
        {"text": "x", "page": "1"},
        {"text": "x", "element_ids": [1, 2]},
        {"text": "y" * 4001},
        {"text": ""},
    ):
        r = client.post("/api/projects/default/draft/stream", json={"intent": "Restate.", "selection": bad})
        assert r.status_code == 400, bad

    # A selection compile with neither content nor selection text is still refused.
    r = client.post("/api/projects/default/draft/stream", json={"intent": "", "compileType": "selection"})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# Selection anchor passthrough: inquire stream
# --------------------------------------------------------------------------

def _sse_events(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        name, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            events.append((name, json.loads(data)))
    return events


def test_inquire_records_the_selection_anchor_on_the_rewritten_node(client, monkeypatch):
    seed = client.put("/api/projects/default/jdf", json={"document": _sample_tree("default"), "mutation_type": "seed"})
    assert seed.status_code == 200
    monkeypatch.setattr(
        "prompt_matrix.routers.inquire_stream._substrate_rows",
        lambda _pid: [{"id": "s1", "filename": "q.pdf", "extracted_text": "Revenue is $12M this quarter." + SOURCE_FILLER}],
    )
    monkeypatch.setattr(
        "prompt_matrix.routers.inquire_stream.verify_rewritten_node",
        lambda _pid, node, **_kw: (node, {"anchored": False, "citations": 0, "entailment": None, "sources": 1}),
    )
    selection = {"text": "revenue is $12M", "page": 1, "element_ids": ["c1:00ff00ff00ff"], "document_id": "doc-default"}
    with patch("prompt_matrix.routers.inquire_stream.CostGovernor") as Gov:
        instance = Gov.return_value
        instance.preflight.return_value = object()
        instance.execute_with_retry_budget.side_effect = [_mock_result()]
        res = client.post(
            "/api/projects/default/inquire/stream",
            json={"user_intent": "Verify revenue", "target_node_id": "p-1", "run_redhat": False,
                  "document": _sample_tree("default"), "selection": selection},
        )
        assert res.status_code == 200
        events = _sse_events(res.get_data(as_text=True))
    ready = next(data for name, data in events if name == "jdf_node_ready")
    anchor = ready["node"]["meta"]["selection_anchor"]
    assert anchor["text"] == "revenue is $12M" and anchor["element_ids"] == ["c1:00ff00ff00ff"]
    assert anchor["verbatim"] is True and anchor["checked_against"] == 1
    complete = next(data for name, data in events if name == "complete")
    assert complete["ok"] is True

    # The persisted node carries it too.
    doc = client.get("/api/projects/default/jdf").get_json()
    body = json.dumps(doc)
    assert "selection_anchor" in body and "c1:00ff00ff00ff" in body


def test_inquire_refuses_a_mistyped_selection(client):
    res = client.post(
        "/api/projects/default/inquire/stream",
        json={"user_intent": "Verify", "target_node_id": "p-1", "selection": {"text": "x", "page": "1"}},
    )
    assert res.status_code == 400
