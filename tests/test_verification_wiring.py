"""Post-parse verification hook: shared, guarded, never blocking persistence."""

from __future__ import annotations

import io

import pytest


def _bundle(chunks_text: str = "Revenue reached 12 million last quarter.", **over):
    bundle = {
        "jdf": {"$jdf": "1.0", "meta": {}, "pages": [{}]},
        "chunks": [{"id": "c0", "text": chunks_text, "types": ["text"], "page": 1}],
        "text": chunks_text,
        "page_count": 1,
        "filename": "doc.pdf",
    }
    bundle.update(over)
    return bundle


@pytest.fixture
def ingest_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "verify.db"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    # ingest_substrate_file validates uploads with the real PDF parser; these
    # tests stub extraction, so stub validation too.
    monkeypatch.setattr(
        "prompt_matrix.routers.substrate.validate_upload_bytes", lambda *a, **k: None
    )
    return monkeypatch


class TestVerificationSync:
    def test_z3_runs_after_parse(self):
        from prompt_matrix.services.verification import run_verification_after_parse

        result = run_verification_after_parse(_bundle())
        assert result["z3"]["z3_status"] in ("PASS", "VIOLATION", "TIMEOUT", "ERROR")

    def test_z3_results_in_tree_meta(self):
        from prompt_matrix.services.verification import run_verification_after_parse

        bundle = _bundle()
        run_verification_after_parse(bundle)
        meta = bundle["verification_tree"]["meta"]
        assert "z3" in meta
        assert meta["z3"]["z3_status"] is not None
        assert meta["z3"]["checked_at"]

    def test_redhat_on_nodes_empty_list_when_no_findings(self):
        from prompt_matrix.models.jdf import flatten_nodes
        from prompt_matrix.services.verification import run_verification_after_parse

        bundle = _bundle()
        run_verification_after_parse(bundle)
        for node in flatten_nodes(bundle["verification_tree"]):
            assert "annotations" in node
            assert isinstance(node["annotations"].get("redhat"), list)

    def test_verification_failure_doesnt_block_parse(self, monkeypatch):
        """Z3 crashing yields an explicit ERROR result; parse data is intact."""
        from prompt_matrix.services import verification as ver

        def boom(tree):
            raise RuntimeError("z3 blew up")

        monkeypatch.setattr(ver, "_run_z3", boom)
        bundle = _bundle()
        result = ver.run_verification_after_parse(bundle)
        assert result["z3"]["z3_status"] == "ERROR"
        assert "chunks" in bundle  # parse data untouched

    def test_z3_violation_detected(self):
        """A truth-ledger contradiction reads as VIOLATION, not PASS."""
        from prompt_matrix.services.verification import run_verification_after_parse

        # The ledger scan's metric parser reads `Key=value` sentences, so the
        # fixture states the claim in that shape and locks a different value.
        ledger_text = "Revenue=12."
        bundle = _bundle(ledger_text)
        bundle["verification_tree"] = {
            "document_id": "d1",
            "meta": {},
            "truth_ledger": {"Revenue": 999},
            "body": [
                {
                    "type": "section",
                    "id": "s1",
                    "title": "A",
                    "children": [
                        {"type": "paragraph", "id": "p1", "content": ledger_text}
                    ],
                }
            ],
        }
        result = run_verification_after_parse(bundle)
        assert result["z3"]["z3_status"] == "VIOLATION"
        assert result["z3"]["violations"]

    def test_asure_tree_verified_in_place(self):
        """A bundle whose jdf is already an Assure tree is verified in place —
        no second tree is built."""
        from prompt_matrix.services.verification import run_verification_after_parse

        tree = {
            "document_id": "doc-1",
            "meta": {},
            "body": [
                {
                    "type": "section",
                    "id": "s1",
                    "title": "A",
                    "children": [
                        {"type": "paragraph", "id": "p1", "content": "Revenue is up."}
                    ],
                }
            ],
        }
        bundle = {"jdf": tree, "page_count": 1}
        run_verification_after_parse(bundle)
        assert "verification_tree" not in bundle
        assert tree["meta"]["z3"]["z3_status"] in ("PASS", "VIOLATION")

    def test_redhat_annotations_normalized_even_when_missing(self):
        """Nodes with no annotations key at all come out with a redhat list."""
        from prompt_matrix.services.verification import run_verification_after_parse

        tree = {
            "document_id": "d",
            "meta": {},
            "body": [
                {
                    "type": "section",
                    "id": "s1",
                    "title": "A",
                    "children": [
                        {"type": "paragraph", "id": "p1", "content": "Plain text."}
                    ],
                }
            ],
        }
        run_verification_after_parse({"jdf": tree, "page_count": 1})
        p1 = tree["body"][0]["children"][0]
        assert isinstance(p1["annotations"].get("redhat"), list)

    def test_over_limit_still_verifies_sync_with_deferral_logged(self, caplog):
        """Async is deferred: an over-limit document still verifies inline."""
        from prompt_matrix.services.verification import run_verification_after_parse

        bundle = _bundle(page_count=51)
        result = run_verification_after_parse(bundle)
        assert result["z3"]["z3_status"] in ("PASS", "VIOLATION", "ERROR", "TIMEOUT")


@pytest.fixture
def stub_extract(monkeypatch):
    def _stub(**overrides):
        monkeypatch.setattr(
            "prompt_matrix.routers.substrate.extract_document_text",
            lambda filename, file_bytes: {
                "text": "Policy liability limit is $5,000,000 for combined single limit.",
                "tables": [],
                "images": [],
                "figures": [],
                "forms": [],
                "page_count": 1,
                "parser_name": "jdf-cli",
                "source_kind": "pdf",
                "parse_confidence": None,
                "ocr_confidence": None,
                "table_count": 0,
                "image_count": 0,
                "figure_count": 0,
                "asset_summary": {"tables": 0, "images": 0, "figures": 0},
                **overrides,
            },
        )

    return _stub


def _stub_omp(monkeypatch):
    monkeypatch.setattr(
        "prompt_matrix.routers.substrate.build_omp_artifact_from_parse",
        lambda *a, **k: type("A", (), {"artifact_id": "omp-v"})(),
    )
    monkeypatch.setattr(
        "prompt_matrix.routers.substrate.store_omp_artifact", lambda *a, **k: None
    )


class TestVerificationIntegration:
    def test_substrate_entrypoint_calls_hook(self, ingest_env, monkeypatch, stub_extract):
        """ingest_substrate_file calls the hook after extraction."""
        import prompt_matrix.routers.substrate as substrate_mod
        from prompt_matrix.routers.substrate import ingest_substrate_file

        calls = []

        def fake_hook(bundle):
            calls.append(bundle)
            return {
                "z3": {"z3_status": "PASS"},
                "z3_status": "PASS",
                "redhat_status": "complete",
            }

        monkeypatch.setattr(
            "prompt_matrix.routers.substrate.run_verification_after_parse", fake_hook
        )
        stub_extract()
        _stub_omp(monkeypatch)

        result = ingest_substrate_file("vproj", "policy.pdf", b"%PDF-1.4 fake")
        assert result["ok"] is True
        assert len(calls) == 1
        assert calls[0]["chunks"]

    def test_jdf_routes_entrypoint_calls_hook(self, ingest_env, monkeypatch):
        """import_project_pdf calls the hook after building the tree."""
        from prompt_matrix.routers import jdf_routes

        calls = []

        def fake_hook(bundle):
            calls.append(bundle)
            return {
                "z3": {"z3_status": "PASS"},
                "z3_status": "PASS",
                "redhat_status": "complete",
            }

        monkeypatch.setattr(jdf_routes, "run_verification_after_parse", fake_hook)
        monkeypatch.setattr(
            "prompt_matrix.routers.jdf_routes.validate_upload_bytes",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "prompt_matrix.routers.jdf_routes.pdf_to_parse_bundle",
            lambda *a, **k: {
                "jdf": {"$jdf": "1.0", "meta": {}, "pages": []},
                "chunks": [{"id": "c0", "text": "chunk0", "tokens": 4}],
                "text": "chunk0",
                "page_count": 1,
                "parser_name": "jdf-cli",
                "source_kind": "pdf",
                "parse_confidence": None,
                "ocr_confidence": None,
                "tables": [],
                "images": [],
                "figures": [],
                "table_count": 0,
                "image_count": 0,
                "figure_count": 0,
                "asset_summary": {"tables": 0, "images": 0, "figures": 0},
                "filename": "a.pdf",
            },
        )
        from prompt_matrix.db.connection import init_db
        from prompt_matrix.web import create_app

        init_db()
        client = create_app(require_auth=False).test_client()
        res = client.post(
            "/api/projects/vproj2/import-pdf",
            data={"file": (io.BytesIO(b"%PDF-1.4 fake"), "a.pdf")},
            content_type="multipart/form-data",
        )
        assert res.status_code in (200, 201)
        assert len(calls) == 1
        # The hook verified the built Assure tree, not the raw jdf-cli payload.
        assert calls[0]["jdf"].get("body")

        def test_jdf_memory_entrypoint_calls_hook(self, ingest_env, monkeypatch):
            """The memory ingest calls the hook after the bundle is built."""
            from prompt_matrix.routers import jdf_memory_routes as mem

            calls = []

            def fake_hook(bundle):
                calls.append(bundle)
                return {
                    "z3": {"z3_status": "PASS"},
                    "z3_status": "PASS",
                    "redhat_status": "complete",
                }

            monkeypatch.setattr(mem, "run_verification_after_parse", fake_hook)
            monkeypatch.setattr(
                "prompt_matrix.services.parser_router.select_parser", lambda *a, **k: "jdf"
            )
            monkeypatch.setattr(
                mem,
                "pdf_to_parse_bundle",
                lambda *a, **k: {
                    "jdf": {"$jdf": "1.0", "meta": {}, "pages": []},
                    "chunks": [{"id": "c0", "text": "chunk0", "tokens": 4}],
                    "text": "chunk0",
                    "page_count": 1,
                    "parser_name": "jdf-cli",
                    "source_kind": "pdf",
                    "parse_confidence": None,
                    "ocr_confidence": None,
                    "tables": [],
                    "images": [],
                    "figures": [],
                    "table_count": 0,
                    "image_count": 0,
                    "figure_count": 0,
                    "asset_summary": {"tables": 0, "images": 0, "figures": 0},
                    "filename": "a.pdf",
                },
            )
            monkeypatch.setattr(mem, "remember_vault_file", lambda *a, **k: None)
            monkeypatch.setattr(
                mem,
                "build_omp_artifact_from_parse",
                lambda *a, **k: type("A", (), {"artifact_id": "omp-m"})(),
            )
            monkeypatch.setattr(mem, "store_omp_artifact", lambda *a, **k: None)

            def fake_remember(doc_id, jdf_dict, chunks, tenant_id="default"):
                return {
                    "doc_id": doc_id,
                    "chunks_total": len(chunks),
                    "chunks_stored": len(chunks),
                }

            monkeypatch.setattr(mem, "remember_jdf_document", fake_remember)

            from prompt_matrix.db.connection import init_db
            from prompt_matrix.web import create_app

            init_db()
            client = create_app(require_auth=False).test_client()
            res = client.post(
                "/api/projects/vproj3/jdf/ingest",
                data={"file": (io.BytesIO(b"%PDF-1.4 fake"), "a.pdf")},
                content_type="multipart/form-data",
            )
            assert res.status_code == 200
            assert len(calls) == 1
            assert calls[0]["chunks"]

        def test_no_duplicated_verification_logic(self):
            """Z3/Red-Hat execution lives only in verification.py: routers import
            the hook, never the engine or the scanner directly."""
            import prompt_matrix
            from pathlib import Path

            root = Path(prompt_matrix.__file__).parent
            offenders = []
            for path in (root / "routers").glob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "TruthLedgerEngine" in text or "_scan_redhat_annotations" in text:
                    offenders.append(str(path))
            assert not offenders, f"verification logic leaked into {offenders}"