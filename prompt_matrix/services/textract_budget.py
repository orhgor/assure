"""Hard monthly spend cap for Amazon Textract.

Textract is the paid fallback behind jdf-cli's free OCR, and a scanned backlog
could otherwise run it without limit. AWS has no per-service spend cap of its
own (Budgets only alerts, or applies a deny policy minutes after the fact), so
the cap is enforced here, before each call: the pages about to be sent are
priced at list price and added to this month's counter atomically; when the
total would pass ``ASSURE_TEXTRACT_MONTHLY_USD_CAP`` (default 100), the call is
refused with :class:`TextractBudgetExceeded` and the document is reported as
"no readable text" instead of quietly costing more.

Prices (eu-central-1 list, 2026-09) — override with ``ASSURE_TEXTRACT_PRICE_DETECT``
/ ``ASSURE_TEXTRACT_PRICE_ANALYZE`` (USD per page):

* ``detect``  DetectDocumentText            0.0015
* ``analyze`` AnalyzeDocument TABLES+FORMS  0.065   (43× detect — off by default,
  ``ASSURE_TEXTRACT_MODE=analyze`` turns it on)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
    from ..lib.textract import TextractError
except ImportError:  # pragma: no cover
    from db.connection import init_db
    from history import get_db
    from lib.textract import TextractError

COUNTER = "textract"
DEFAULT_CAP_USD = 100.0
PRICES = {"detect": 0.0015, "analyze": 0.065}


class TextractBudgetExceeded(TextractError):
    """The month's Textract spend would pass the cap; nothing was sent."""


def monthly_cap_usd() -> float:
    raw = os.environ.get("ASSURE_TEXTRACT_MONTHLY_USD_CAP", "").strip()
    if not raw:
        return DEFAULT_CAP_USD
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_CAP_USD


def price_per_page(api: str) -> float:
    env = os.environ.get(f"ASSURE_TEXTRACT_PRICE_{api.upper()}", "").strip()
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return PRICES.get(api, PRICES["detect"])


def current_period(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def usage(period: str | None = None) -> dict[str, Any]:
    """This month's pages, spend and cap — for /api/health and the Sources panel."""
    init_db()
    period = period or current_period()
    row = get_db().execute(
        "SELECT count, cost_usd FROM usage_counters WHERE name = ? AND period = ?",
        (COUNTER, period),
    ).fetchone()
    spent = float(row[1]) if row else 0.0
    cap = monthly_cap_usd()
    return {
        "period": period,
        "pages": int(row[0]) if row else 0,
        "spent_usd": round(spent, 4),
        "cap_usd": cap,
        "remaining_usd": round(max(0.0, cap - spent), 4),
    }


def reserve(pages: int, api: str = "detect") -> float:
    """Charge ``pages`` of ``api`` against this month before calling Textract.

    One atomic UPSERT: the new total is computed in the database and returned,
    so concurrent workers cannot both pass the check. If the total passes the
    cap the charge is rolled back and :class:`TextractBudgetExceeded` is
    raised; the caller never reaches boto3.
    """
    pages = max(0, int(pages))
    cost = round(pages * price_per_page(api), 6)
    if pages == 0:
        return 0.0
    init_db()
    db = get_db()
    period = current_period()
    cap = monthly_cap_usd()
    row = db.execute(
        """
        INSERT INTO usage_counters (name, period, count, cost_usd, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (name, period) DO UPDATE SET
            count = usage_counters.count + excluded.count,
            cost_usd = usage_counters.cost_usd + excluded.cost_usd,
            updated_at = CURRENT_TIMESTAMP
        RETURNING cost_usd
        """,
        (COUNTER, period, pages, cost),
    ).fetchone()
    total = float(row[0]) if row else cost
    if total > cap:
        db.execute(
            "UPDATE usage_counters SET count = count - ?, cost_usd = cost_usd - ? "
            "WHERE name = ? AND period = ?",
            (pages, cost, COUNTER, period),
        )
        db.commit()
        raise TextractBudgetExceeded(
            f"Textract monthly cap reached: {total - cost:.2f} USD spent of {cap:.2f} USD "
            f"({period}); {pages} page(s) of {api} (~{cost:.4f} USD) refused. "
            "Raise ASSURE_TEXTRACT_MONTHLY_USD_CAP or wait for next month."
        )
    db.commit()
    return cost
