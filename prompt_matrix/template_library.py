"""Local template library: format strings scored by the bandit.

Does not rewrite config.json. Does not share data across machines.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

try:
    from .bandit import epsilon_from_env, pick as bandit_pick
    from .history import DB_PATH
    from .variation_generator import maybe_ask_model_for_formats, seed_texts
except ImportError:
    from bandit import epsilon_from_env, pick as bandit_pick
    from history import DB_PATH
    from variation_generator import maybe_ask_model_for_formats, seed_texts


def improve_enabled() -> bool:
    raw = os.environ.get("PEM_IMPROVE", "").strip().lower()
    if raw in {"0", "false", "off", "no"}:
        return False
    if raw in {"1", "true", "on", "yes"}:
        return True
    try:
        from .engine import load_matrix
    except ImportError:
        from engine import load_matrix
    data = load_matrix().runtime.model_dump()
    block = data.get("improve") if isinstance(data.get("improve"), dict) else {}
    return bool(block.get("enabled", True))


def improve_epsilon() -> float:
    try:
        from .engine import load_matrix
    except ImportError:
        from engine import load_matrix
    data = load_matrix().runtime.model_dump()
    block = data.get("improve") if isinstance(data.get("improve"), dict) else {}
    default = float(block.get("epsilon", 0.2) or 0.2)
    return epsilon_from_env(default)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_variations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                intent TEXT NOT NULL,
                domain TEXT NOT NULL,
                model TEXT NOT NULL,
                variation_text TEXT NOT NULL,
                kind TEXT NOT NULL,
                performance_score REAL,
                usage_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_used TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_hash TEXT,
                variation_id INTEGER,
                intent TEXT,
                domain TEXT,
                model TEXT,
                consensus_score REAL,
                coherence_score REAL,
                hallucination_rate REAL,
                token_efficiency REAL,
                overall_score REAL,
                total_tokens INTEGER,
                rating INTEGER,
                timestamp TEXT NOT NULL
            )
            """
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(prompt_performance)")}
        if "total_tokens" not in cols:
            conn.execute("ALTER TABLE prompt_performance ADD COLUMN total_tokens INTEGER")
        if "rating" not in cols:
            conn.execute("ALTER TABLE prompt_performance ADD COLUMN rating INTEGER")
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_seeds(intent: str, *, model: str = "*", domain: str = "general") -> None:
    ensure_tables()
    try:
        from .engine import load_matrix
    except ImportError:
        from engine import load_matrix
    matrix = load_matrix()
    intent_cfg = matrix.intents.get(intent)
    base = intent_cfg.output_format if intent_cfg else ""
    texts = seed_texts(intent, base)
    conn = _connect()
    try:
        existing = {
            row["variation_text"]
            for row in conn.execute(
                "SELECT variation_text FROM prompt_variations WHERE intent = ?",
                (intent,),
            )
        }
        for text in texts:
            if text in existing:
                continue
            conn.execute(
                """
                INSERT INTO prompt_variations
                (intent, domain, model, variation_text, kind, performance_score, usage_count, created_at)
                VALUES (?, ?, ?, ?, 'seed', NULL, 0, ?)
                """,
                (intent, domain, model, text, _now()),
            )
            existing.add(text)
        conn.commit()
    finally:
        conn.close()
    _maybe_generate(intent, base, model)


def _maybe_generate(intent: str, base: str, model: str) -> None:
    from .variation_generator import generate_with_model_enabled

    if not generate_with_model_enabled():
        return
    conn = _connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM prompt_variations WHERE intent = ? AND kind = 'generated'",
            (intent,),
        ).fetchone()[0]
    finally:
        conn.close()
    if n:
        return
    extras = maybe_ask_model_for_formats(intent, base, model if model != "*" else "gemini")
    if not extras:
        return
    conn = _connect()
    try:
        have = {
            row["variation_text"]
            for row in conn.execute(
                "SELECT variation_text FROM prompt_variations WHERE intent = ?",
                (intent,),
            )
        }
        for text in extras:
            if text in have:
                continue
            conn.execute(
                """
                INSERT INTO prompt_variations
                (intent, domain, model, variation_text, kind, performance_score, usage_count, created_at)
                VALUES (?, ?, ?, ?, 'generated', NULL, 0, ?)
                """,
                (intent, "general", "*", text, _now()),
            )
        conn.commit()
    finally:
        conn.close()


def _candidates(intent: str, domain: str, model: str) -> list[dict]:
    ensure_tables()
    conn = _connect()
    try:
        queries = [
            (intent, domain, model),
            (intent, "general", model),
            (intent, domain, "*"),
            (intent, "general", "*"),
        ]
        seen: set[int] = set()
        rows: list[dict] = []
        for intent_k, domain_k, model_k in queries:
            found = conn.execute(
                """
                SELECT * FROM prompt_variations
                WHERE intent = ? AND domain = ? AND model = ?
                """,
                (intent_k, domain_k, model_k),
            ).fetchall()
            if not found:
                found = conn.execute(
                    """
                    SELECT * FROM prompt_variations
                    WHERE intent = ? AND (domain = ? OR domain = 'general')
                      AND (model = ? OR model = '*')
                    """,
                    (intent, domain, model),
                ).fetchall()
            for row in found:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                rows.append(dict(row))
            if rows:
                return rows
        leftover = conn.execute(
            "SELECT * FROM prompt_variations WHERE intent = ?",
            (intent,),
        ).fetchall()
        return [dict(row) for row in leftover]
    finally:
        conn.close()


def pick_variation(intent: str, domain: str, model: str) -> dict | None:
    ensure_seeds(intent, model="*", domain="general")
    rows = _candidates(intent, domain, model)
    chosen = bandit_pick(rows, epsilon=improve_epsilon())
    if not chosen:
        return None
    conn = _connect()
    try:
        conn.execute(
            "UPDATE prompt_variations SET last_used = ? WHERE id = ?",
            (_now(), chosen["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return chosen


def record_outcome(variation_id: int | None, overall: float) -> None:
    if not variation_id:
        return
    ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT performance_score, usage_count FROM prompt_variations WHERE id = ?",
            (variation_id,),
        ).fetchone()
        if not row:
            return
        n = int(row["usage_count"] or 0)
        prev = row["performance_score"]
        if prev is None:
            nxt = float(overall)
        else:
            nxt = (float(prev) * n + float(overall)) / (n + 1)
        conn.execute(
            """
            UPDATE prompt_variations
            SET performance_score = ?, usage_count = ?, last_used = ?
            WHERE id = ?
            """,
            (round(nxt, 4), n + 1, _now(), variation_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_performance(
    *,
    run_hash: str,
    variation_id: int | None,
    intent: str,
    domain: str,
    model: str,
    consensus_score: float | None,
    coherence_score: float | None,
    hallucination_rate: float | None,
    token_efficiency: float | None,
    overall_score: float | None,
    total_tokens: int | None = None,
) -> None:
    ensure_tables()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO prompt_performance (
                run_hash, variation_id, intent, domain, model,
                consensus_score, coherence_score, hallucination_rate,
                token_efficiency, overall_score, total_tokens, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_hash,
                variation_id,
                intent,
                domain,
                model,
                consensus_score,
                coherence_score,
                hallucination_rate,
                token_efficiency,
                overall_score,
                total_tokens,
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def best_variation(intent: str, domain: str, model: str) -> dict | None:
    rows = _candidates(intent, domain, model)
    if not rows:
        return None
    scored = [row for row in rows if row.get("performance_score") is not None]
    pool = scored or rows
    return max(pool, key=lambda row: float(row.get("performance_score") or 0.0))


def get_variation_id_for_run(run_hash: str) -> int | None:
    if not run_hash:
        return None
    ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT variation_id FROM prompt_performance
            WHERE run_hash = ? AND variation_id IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (run_hash,),
        ).fetchone()
        return int(row["variation_id"]) if row and row["variation_id"] is not None else None
    finally:
        conn.close()


def get_best_variations(min_uses: int = 10) -> list[dict]:
    ensure_tables()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT intent, variation_text, performance_score, usage_count
            FROM prompt_variations
            WHERE usage_count >= ?
            ORDER BY (performance_score IS NULL), performance_score DESC
            """,
            (min_uses,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def print_best_variations(min_uses: int = 10) -> None:
    results = get_best_variations(min_uses)
    if not results:
        return
    print("Best variations (10+ uses)", file=sys.stderr)
    for row in results:
        text = (row.get("variation_text") or "")[:80]
        print(
            f"  {row.get('intent')}: score={row.get('performance_score')} "
            f"used={row.get('usage_count')} {text}",
            file=sys.stderr,
        )


def apply_feedback(
    run_hash: str,
    rating: int,
    variation_id: int | None = None,
) -> dict:
    """Apply a thumbs rating to the variation used on that run.

    Does not bump usage_count. Automatic quality already counted the run.
    """
    digest = (run_hash or "").strip()
    if not digest:
        return {"ok": False, "error": "Missing run."}
    rating_n = 1 if int(rating) else 0
    ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM prompt_performance WHERE run_hash = ? ORDER BY id DESC LIMIT 1",
            (digest,),
        ).fetchone()
        if row and row["rating"] is not None:
            return {"ok": True, "already": True, "variation_id": row["variation_id"]}
        vid = variation_id
        if row and row["variation_id"] is not None:
            vid = int(row["variation_id"])
        elif vid is not None:
            vid = int(vid)
        hall = row["hallucination_rate"] if row else None
        tokens = row["total_tokens"] if row else None
        if tokens is None:
            try:
                exec_row = conn.execute(
                    """
                    SELECT total_tokens FROM executions
                    WHERE prompt_hash = ? ORDER BY id DESC LIMIT 1
                    """,
                    (digest,),
                ).fetchone()
            except sqlite3.OperationalError:
                exec_row = None
            if exec_row and exec_row["total_tokens"] is not None:
                tokens = exec_row["total_tokens"]
        delta = 0.15 if rating_n == 1 else -0.15
        if hall is not None and float(hall) == 0:
            delta += 0.05
        if tokens is not None:
            if int(tokens) < 1500:
                delta += 0.05
            elif int(tokens) > 3000:
                delta -= 0.05
        nxt = None
        if vid:
            current = conn.execute(
                "SELECT performance_score FROM prompt_variations WHERE id = ?",
                (vid,),
            ).fetchone()
            if current:
                prev = current["performance_score"]
                nxt = (0.5 + delta) if prev is None else float(prev) + delta
                nxt = max(0.0, min(1.0, nxt))
                conn.execute(
                    "UPDATE prompt_variations SET performance_score = ? WHERE id = ?",
                    (round(nxt, 4), vid),
                )
        if row:
            conn.execute(
                "UPDATE prompt_performance SET rating = ? WHERE id = ?",
                (rating_n, row["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO prompt_performance (run_hash, variation_id, rating, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (digest, vid, rating_n, _now()),
            )
        conn.commit()
        label = "helpful" if rating_n else "not"
        print(
            f"[Improve] feedback={label} variation={vid} delta={delta:.2f} score={nxt}",
            file=sys.stderr,
        )
        if os.environ.get("PEM_IMPROVE_SUMMARY", "").strip() in {"1", "true", "on", "yes"}:
            print_best_variations()
        return {
            "ok": True,
            "already": False,
            "variation_id": vid,
            "delta": round(delta, 4),
        }
    finally:
        conn.close()
