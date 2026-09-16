"""Tests for JDF converter (PDF -> JDF -> chunks via jdf-cli)."""
import json
import subprocess

from prompt_matrix.services.jdf_converter import (
    JdfConversionError,
    jdf_to_chunks,
    pdf_to_jdf,
)


def test_pdf_to_jdf_parses_written_file(monkeypatch):
    jdf_obj = {"$jdf": "1.0", "meta": {}, "pages": []}

    def fake_run(cmd, **kw):
        pdf_path = cmd[2]
        jdf_path = pdf_path[:-4] + ".jdf"
        with open(jdf_path, "w") as fh:
            json.dump(jdf_obj, fh)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    assert pdf_to_jdf(b"%PDF-1.4 fake") == jdf_obj


def test_pdf_to_jdf_raises_on_error(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(args=cmd, returncode=1, stderr="boom")

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    try:
        pdf_to_jdf(b"x")
        raise AssertionError("expected JdfConversionError")
    except JdfConversionError:
        pass


def test_jdf_to_chunks_parses_jsonl(monkeypatch):
    chunk = {"id": "c1", "text": "hello", "types": ["text"], "tokens": 4}

    def fake_run(cmd, **kw):
        jdf_path = cmd[2]
        chunks_path = jdf_path[:-4] + ".chunks.jsonl"
        with open(chunks_path, "w") as fh:
            fh.write(json.dumps(chunk) + "\n")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    assert jdf_to_chunks({"$jdf": "1.0", "pages": []}) == [chunk]


def test_jdf_to_chunks_raises_on_error(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(args=cmd, returncode=2, stderr="nope")

    monkeypatch.setattr("prompt_matrix.services.jdf_converter._run", fake_run)
    try:
        jdf_to_chunks({})
        raise AssertionError("expected JdfConversionError")
    except JdfConversionError:
        pass