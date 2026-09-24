"""Textract spend is capped per month, atomically, before boto3 is called."""

from __future__ import annotations

import pytest

from prompt_matrix.db.connection import init_db
from prompt_matrix.lib.textract import TextractClient, TextractError
from prompt_matrix.services import textract_budget as tb


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "budget.sqlite"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    init_db()
    yield


def test_reserve_accumulates_and_refuses_past_the_cap(db, monkeypatch):
    monkeypatch.setenv("ASSURE_TEXTRACT_MONTHLY_USD_CAP", "0.01")  # ≈ 6 detect pages
    assert tb.reserve(4, "detect") == pytest.approx(0.006)
    u = tb.usage()
    assert u["pages"] == 4 and u["spent_usd"] == pytest.approx(0.006)
    with pytest.raises(tb.TextractBudgetExceeded):
        tb.reserve(4, "detect")  # 0.012 > 0.01
    # the refused charge was rolled back
    assert tb.usage()["pages"] == 4
    assert tb.reserve(2, "detect") == pytest.approx(0.003)  # 0.009 fits


def test_analyze_is_priced_43x_detect(db):
    assert tb.price_per_page("analyze") / tb.price_per_page("detect") == pytest.approx(65 / 1.5)


class _FakeTextract:
    def __init__(self):
        self.detect_calls = 0
        self.analyze_calls = 0

    def detect_document_text(self, **_kw):
        self.detect_calls += 1
        return {"Blocks": [{"Id": "1", "BlockType": "LINE", "Text": "hello"}]}

    def analyze_document(self, **_kw):
        self.analyze_calls += 1
        return {"Blocks": [{"Id": "1", "BlockType": "LINE", "Text": "hello"}]}


def test_client_uses_detect_by_default_and_stops_at_the_cap(db, monkeypatch):
    monkeypatch.delenv("ASSURE_TEXTRACT_MODE", raising=False)
    monkeypatch.setenv("ASSURE_TEXTRACT_MONTHLY_USD_CAP", "0.002")  # one detect page
    fake = _FakeTextract()
    client = TextractClient(client=fake)
    out = client.extract_text(b"\x89PNG fake", "scan.png")
    assert out["text"] == "hello"
    assert fake.detect_calls == 1 and fake.analyze_calls == 0
    with pytest.raises(TextractError) as exc:
        client.extract_text(b"\x89PNG fake", "scan2.png")
    assert "monthly cap" in str(exc.value)
    assert fake.detect_calls == 1  # nothing was sent past the cap


def test_client_analyze_mode_when_asked(db, monkeypatch):
    monkeypatch.setenv("ASSURE_TEXTRACT_MODE", "analyze")
    monkeypatch.setenv("ASSURE_TEXTRACT_MONTHLY_USD_CAP", "100")
    fake = _FakeTextract()
    TextractClient(client=fake).extract_text(b"\x89PNG fake", "scan.png")
    assert fake.analyze_calls == 1
    assert tb.usage()["spent_usd"] == pytest.approx(0.065)
