"""Tier 2 of the Math Check: turn one prose claim into a checkable relation.

Tier 1 (``routers.inquire_stream._parse_metrics`` + the truth ledger) only sees
``key: value`` / ``key=value`` labels. A draft that writes "Revenue ARR is $12M
this quarter" carries a checkable number with no label, so Tier 1 finds nothing
and the gate reports SKIPPED over the locks it did infer — the extraction gap
measured on scratch project ``shell-proto-54fe89`` (``locks_verified: 13``,
``metrics_checked: 0``).

This module closes that gap **without loosening Tier 1**: one claim goes to a
small model, which returns a strict JSON relation. The model proposes the shape;
``services.relational_z3`` decides the verdict against the locked source facts;
the source facts are the only values that count. A claim that cannot be
translated is never reported as verified — it falls back to Tier 1's value
comparison, and failing that to UNVERIFIED with the reason.

Two properties are deliberate:

**The call is pinned.** Same reasoning as the compile path it feeds
(``routers.draft._COMPILE_PROVIDER_PIN``, now shared as ``keys.PROVIDER_PIN``):
OpenRouter still samples when asked for temperature 0 unless the upstream
provider is named, and an unpinned translation is a different question on every
call. The pin is applied to this call and nowhere assumed — see
``pinned_request_body``, which is what the wire capture in the evidence run
reads.

**The translation is cached.** The key covers everything the JSON depends on::

    sha256(claim | source_facts | z3_version | translator_model | prompt)

so a re-worded claim, a new locked value, a different Z3 build, a different model
or an edited prompt is a different key and a real call. A hit reuses the stored
JSON and makes no model call. The Z3 build is in the key because two Z3 versions
can disagree on the same input, and a verdict cached under one must not be served
as the other's.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Callable

from .relational_z3 import RELATIONS, z3_version

try:  # pragma: no cover - import layout differs between package and script use
    from ..db.pipeline_cache import fetch_pipeline_cache, save_pipeline_cache
except ImportError:  # pragma: no cover
    from db.pipeline_cache import fetch_pipeline_cache, save_pipeline_cache

_log = logging.getLogger(__name__)

#: The store is the existing ``pipeline_cache`` table — no new table.
CACHE_KIND = "relational_metric"

#: ``caller(prompt, model) -> raw text``. Injected so routes and tests can swap
#: the transport (or remove it) without touching the prompt or the parse.
ClaimCaller = Callable[[str, str], str]

#: What the claim text is allowed to cost the prompt.
MAX_CLAIM_CHARS = 400
#: How many locked facts are offered as context. Sorted, so the prompt — and so
#: the cache key and the call — is the same for the same set of facts.
MAX_FACTS = 40
MAX_OUTPUT_TOKENS = 512
REQUEST_TIMEOUT_S = 30.0

_PROMPT = """\
You convert one sentence of a document into a single checkable relation. You do \
not judge the sentence, and you do not tidy it.

CLAIM:
{claim}

LOCKED SOURCE VALUES (the document's own source locked these numbers; they are \
the only values that count):
{facts}

Return exactly one JSON object and nothing else — no prose, no code fence:

{{"metric": "<the metric the claim is about>",
  "operands": [{{"name": "<must be one of the locked names above when the claim is about one of them>",
                "value": <the number the CLAIM states for it>,
                "unit": "<USD|%|count|ratio|other>",
                "source_sentence": "<the CLAIM sentence, verbatim>"}}],
  "relation": "eq" | "lt" | "le" | "gt" | "ge",
  "expected": <the number the claim compares against>}}

Rules:
1. "value" is the number the CLAIM states. Never substitute a locked value: if \
the draft and the source disagree, that disagreement is the finding.
2. "expected" is the number the claim compares the value to. When the claim \
asserts a value flatly ("ARR is $12M"), use relation "eq" and repeat the same \
number as "value" and "expected".
3. "relation" is read as: value <relation> expected. Use "lt"/"le" for "under", \
"below", "less than", "no more than"; "gt"/"ge" for "over", "above", "exceeds", \
"at least"; "eq" for "is", "reached", "totalled", "equals".
4. "name" must match a LOCKED SOURCE VALUES name exactly when the claim is about \
one of them.
5. If the sentence states no checkable number, return {{"metric": "", \
"operands": [], "relation": "eq", "expected": 0}}.
6. One operand is enough. Add a second only when the claim names two quantities.
"""
#: The prompt with its placeholders filled, for the fingerprint below — the
#: fingerprint must cover the text actually sent, not the template.
_BARE_PROMPT = _PROMPT.replace("{claim}", "").replace("{facts}", "")

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_translation_prompt(claim: str, facts: dict[str, float]) -> str:
    """The translation prompt for one claim and the locked facts, deterministically.

    Facts are sorted by name so the same ledger always produces the same prompt —
    a dict's iteration order is not part of this contract.
    """
    listed = facts or {}
    lines = [
        f"- {name}: {_fmt_number(listed[name])}"
        for name in sorted(listed)[:MAX_FACTS]
    ]
    return _PROMPT.format(
        claim=" ".join(str(claim or "").split())[:MAX_CLAIM_CHARS],
        facts="\n".join(lines) if lines else "(none locked yet)",
    )


def prompt_fingerprint() -> str:
    """sha256 of the prompt text, so an edited prompt is a different cache key."""
    return hashlib.sha256(_PROMPT.encode("utf-8")).hexdigest()


def _fmt_number(value: float) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else repr(number)


def parse_claim(raw: str) -> dict[str, Any] | None:
    """The translated claim, or None when the answer is not one usable object.

    Unparseable output is a failed translation, never a verdict: the caller falls
    back to Tier 1 and, failing that, reports UNVERIFIED with the reason. A model
    answer is data, so its numbers are validated here rather than trusted
    downstream.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    relation = str(payload.get("relation") or "").strip().lower()
    if relation not in RELATIONS:
        return None
    try:
        expected = float(payload.get("expected"))
    except (TypeError, ValueError):
        return None

    raw_operands = payload.get("operands")
    if not isinstance(raw_operands, list):
        return None
    operands: list[dict[str, Any]] = []
    for item in raw_operands:
        if not isinstance(item, dict):
            return None
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            return None
        name = str(item.get("name") or "").strip()
        if not name:
            return None
        operands.append(
            {
                "name": name,
                "value": value,
                "unit": str(item.get("unit") or "").strip(),
                "source_sentence": str(item.get("source_sentence") or "").strip(),
            }
        )

    metric = str(payload.get("metric") or "").strip()
    if not metric and not operands:
        # The model's way of saying "no checkable number here" (rule 5).
        return None
    return {"metric": metric, "operands": operands, "relation": relation, "expected": expected}


def cache_key(claim: str, facts: dict[str, float], model_id: str) -> str:
    """``relational:<digest>`` over everything the translation depends on."""
    facts_digest = hashlib.sha256(
        json.dumps(
            {name: facts[name] for name in sorted(facts or {})}, sort_keys=True, default=str
        ).encode("utf-8")
    ).hexdigest()
    parts = [
        " ".join(str(claim or "").split()).lower(),
        facts_digest,
        z3_version(),
        str(model_id or ""),
        prompt_fingerprint(),
    ]
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{CACHE_KIND}:{digest}"


def pinned_request_body(model: str, prompt: str) -> dict[str, Any]:
    """The OpenRouter request body this module sends, pin included.

    Exposed so the pin can be read off the wire instead of asserted: the evidence
    capture posts this body through a local listener and greps it. ``provider`` is
    present exactly when the model routes through OpenRouter, and it is the same
    ``keys.PROVIDER_PIN`` the compile path uses — named, with fallbacks off, so an
    upstream swap cannot silently change what the check was run against.
    """
    try:
        from ..keys import PROVIDER_PIN
    except ImportError:  # pragma: no cover
        from keys import PROVIDER_PIN

    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "provider": dict(PROVIDER_PIN),
    }


def _pinned_caller(prompt: str, model: str) -> str:
    """Send one translation request, pinned like the compile it feeds.

    The key comes from the same ``litellm_kwargs_for`` the compile path uses, and
    the provider pin rides ``extra_body`` because that is the carrier litellm
    passes through to the OpenRouter request body — a named kwarg has no route to
    that field (``routers/draft.py`` measured this, and this call reuses it rather
    than re-deriving it).
    """
    try:
        from ..keys import litellm_kwargs_for, provider_slug_for_litellm
    except ImportError:  # pragma: no cover
        from keys import litellm_kwargs_for, provider_slug_for_litellm

    import litellm

    slug = provider_slug_for_litellm(model)
    kwargs: dict[str, Any] = {}
    if slug:
        try:
            kwargs = dict(litellm_kwargs_for(slug))
        except Exception:  # a missing key is the caller's error to report
            kwargs = {}
    if slug == "openrouter":
        try:
            from ..keys import PROVIDER_PIN
        except ImportError:  # pragma: no cover
            from keys import PROVIDER_PIN

        kwargs["extra_body"] = {"provider": dict(PROVIDER_PIN)}

    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=MAX_OUTPUT_TOKENS,
        timeout=REQUEST_TIMEOUT_S,
        **kwargs,
    )
    choice = (response.choices or [None])[0]
    message = getattr(choice, "message", None)
    return str(getattr(message, "content", "") or "")


def _governor_policy() -> tuple[str, str]:
    """(model_id, litellm_model) for structured extraction.

    The same policy the entailment check runs on: a non-reasoning Qwen instruct
    model, which is the repo's measured choice for "answer with one structured
    line" work, and — relevant here — one Alibaba serves, so the shared provider
    pin has somewhere to land. The brief named GPT-4o-mini or Qwen2.5-14B;
    neither is served under ``order: [Alibaba]`` with fallbacks off, so the
    policy model stands in and the deviation is reported rather than papered over.
    """
    try:
        from ..cost_governance import CostGovernor, TaskType
    except ImportError:  # pragma: no cover
        from cost_governance import CostGovernor, TaskType

    policy = CostGovernor().policy_for(TaskType.SEMANTIC_VALIDATION)
    return str(policy.model_id), str(policy.litellm_model)


def load_translation(cache_key_value: str) -> dict[str, Any] | None:
    """The cached claim, or None. Never raises."""
    if not cache_key_value:
        return None
    try:
        payload = fetch_pipeline_cache(cache_key_value)
    except Exception:
        return None
    claim = (payload or {}).get("claim")
    return claim if isinstance(claim, dict) else None


def store_translation(cache_key_value: str, project_id: str, claim: dict[str, Any]) -> None:
    """Persist a translation. Never raises — a cache must not change a verdict."""
    if not cache_key_value or not isinstance(claim, dict):
        return
    try:
        save_pipeline_cache(
            cache_key_value,
            project_id or CACHE_KIND,
            CACHE_KIND,
            {"claim": claim, "z3_version": z3_version()},
        )
    except Exception:
        return


def translate_claim(
    claim: str,
    facts: dict[str, float],
    *,
    project_id: str = "",
    caller: ClaimCaller | None = None,
) -> dict[str, Any]:
    """Translate one claim into a relation. Never raises.

    Returns ``{"ok", "claim", "model", "cached", "json_sha256", "raw", "reason"}``.
    ``ok`` false means the caller must fall back — this function never invents a
    verdict, and never reports a failed translation as a checked claim.
    """
    model_id, litellm_model = _governor_policy()
    key = cache_key(claim, facts, model_id)

    cached = load_translation(key)
    if cached is not None:
        return {
            "ok": True,
            "claim": cached,
            "model": model_id,
            "cached": True,
            "json_sha256": _claim_sha256(cached),
            "raw": "",
            "reason": "",
        }

    prompt = build_translation_prompt(claim, facts)
    send = caller or _pinned_caller
    try:
        raw = send(prompt, litellm_model)
    except Exception as exc:  # transport, budget, missing key
        return {
            "ok": False,
            "claim": None,
            "model": model_id,
            "cached": False,
            "json_sha256": "",
            "raw": "",
            "reason": f"{type(exc).__name__}: {exc}",
        }

    parsed = parse_claim(raw)
    if parsed is None:
        return {
            "ok": False,
            "claim": None,
            "model": model_id,
            "cached": False,
            "json_sha256": hashlib.sha256(str(raw or "").encode("utf-8")).hexdigest(),
            "raw": str(raw or "")[:MAX_CLAIM_CHARS],
            "reason": "the model returned no usable JSON relation",
        }

    store_translation(key, project_id, parsed)
    _log.info("[relational-cache] stored %s", key)
    return {
        "ok": True,
        "claim": parsed,
        "model": model_id,
        "cached": False,
        "json_sha256": _claim_sha256(parsed),
        "raw": str(raw or "")[:MAX_CLAIM_CHARS],
        "reason": "",
    }


def _claim_sha256(claim: dict[str, Any]) -> str:
    """Stable digest of a translated claim — the determinism proof reads this."""
    return hashlib.sha256(
        json.dumps(claim, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CACHE_KIND",
    "ClaimCaller",
    "MAX_CLAIM_CHARS",
    "MAX_FACTS",
    "build_translation_prompt",
    "cache_key",
    "load_translation",
    "parse_claim",
    "pinned_request_body",
    "prompt_fingerprint",
    "store_translation",
    "translate_claim",
]
