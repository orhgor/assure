"""Assure editions. PEM stays the engine. These gates are local, not a payment API.

ASSURE_EDITION or config.json runtime.edition: free | pro | team | self-hosted.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass

_request_plan_id: ContextVar[str | None] = ContextVar("assure_request_plan", default=None)


def set_request_plan(plan_id: str | None) -> None:
    _request_plan_id.set(plan_id)


EDITIONS = ("free", "pro", "team", "self-hosted")

# User-facing Compose labels (UI only). Engine ids stay single | ensemble | redhat.
# Quick Answer = single, Compare & Validate = ensemble, Refine & Verify = redhat.
# Do not wrap these in flask_babel.gettext at import time: there is no locale
# yet, so _() would freeze the first locale that imported this module.
# Translate at request time with i18n.intent_plain(lang) (en, es, zh, fr, de, ja, tr).
INTENT_PLAIN = {
    "research": "Research: structures your question so the AI returns a thesis, verified findings, inferred gaps, and open questions. You do not have to know how to prompt.",
    "design": "Design: a plan or draft you can hand to someone.",
    "comparison": "Comparison: two options, with agreement and fights.",
    "debug": "Debug: Identify and fix a problem.",
    "analysis": "Analysis: what the numbers mean, and what they do not prove.",
}

FREE_ENSEMBLE = ("gemini",)
PRO_CONSENSUS = ("claude", "gemini")
FREE_DEFAULT_TARGET = "gemini"


@dataclass(frozen=True)
class EditionPlan:
    id: str
    label: str
    daily_sends: int | None  # None = unlimited
    max_ensemble: int
    full_text_history: bool
    history_days: int | None  # None = keep forever
    export_formats: tuple[str, ...]
    allow_diff: bool
    personas: tuple[str, ...]
    allow_auth_note: bool

    def allows_persona(self, persona: str | None) -> bool:
        key = (persona or "redhat").strip().lower() or "redhat"
        return key in self.personas

    def allows_export(self, fmt: str) -> bool:
        return (fmt or "").strip().lower() in self.export_formats


PLANS: dict[str, EditionPlan] = {
    "free": EditionPlan(
        id="free",
        label="Free",
        daily_sends=5,
        max_ensemble=1,
        full_text_history=False,
        history_days=7,
        export_formats=(),
        allow_diff=False,
        personas=("redhat",),
        allow_auth_note=False,
    ),
    "pro": EditionPlan(
        id="pro",
        label="Pro",
        daily_sends=100,
        max_ensemble=8,
        full_text_history=True,
        history_days=None,
        export_formats=("cursorrules", "mdc", "fabric", "dspy"),
        allow_diff=True,
        personas=("redhat", "security", "tokens", "schema", "code"),
        allow_auth_note=False,
    ),
    "team": EditionPlan(
        id="team",
        label="Team",
        # Unlimited Sends. Shared workspaces are not in this repo.
        daily_sends=None,
        max_ensemble=8,
        full_text_history=True,
        history_days=None,
        export_formats=("cursorrules", "mdc", "fabric", "dspy"),
        allow_diff=True,
        personas=("redhat", "security", "tokens", "schema", "code"),
        allow_auth_note=True,
    ),
    "self-hosted": EditionPlan(
        id="self-hosted",
        label="Self-hosted",
        daily_sends=None,
        max_ensemble=8,
        full_text_history=True,
        history_days=None,
        export_formats=("cursorrules", "mdc", "fabric", "dspy"),
        allow_diff=True,
        personas=("redhat", "security", "tokens", "schema", "code"),
        allow_auth_note=True,
    ),
}


def normalize_edition(value: str | None) -> str:
    raw = (value or "").strip().lower().replace("_", "-")
    if raw in {"selfhosted", "self-host", "hosted"}:
        raw = "self-hosted"
    if raw == "cloud":
        return "cloud"
    if raw in PLANS:
        return raw
    return "free"


def current_edition(config_edition: str | None = None) -> EditionPlan:
    env = os.environ.get("ASSURE_EDITION") or os.environ.get("PEM_EDITION")
    raw = normalize_edition(env or config_edition)
    if raw == "self-hosted":
        return PLANS["self-hosted"]
    override = _request_plan_id.get()
    if override in PLANS:
        return PLANS[override]
    if raw == "cloud":
        return PLANS["free"]
    return PLANS[raw]


def snapshot(config_edition: str | None = None) -> dict:
    plan = current_edition(config_edition)
    used = 0
    try:
        from .history import count_sends_today
    except ImportError:
        from history import count_sends_today
    used = count_sends_today()
    remaining = None if plan.daily_sends is None else max(0, plan.daily_sends - used)
    data = {
        "id": plan.id,
        "label": plan.label,
        "daily_sends": plan.daily_sends,
        "sends_today": used,
        "sends_remaining": remaining,
        "max_ensemble": plan.max_ensemble,
        "full_text_history": plan.full_text_history,
        "history_days": plan.history_days,
        "export_formats": list(plan.export_formats),
        "allow_diff": plan.allow_diff,
        "personas": list(plan.personas),
        "can_export_work": plan.full_text_history,
        "can_search_full": plan.full_text_history,
        "can_refine_any": plan.full_text_history,
        "product": "Assure",
        "engine": "PEM",
        "cloud": False,
        "gate_ensemble": False,
        "stripe": False,
        "supabase": False,
    }
    try:
        from .cloud_billing import is_cloud_mode, stripe_configured, supabase_configured
    except ImportError:
        try:
            from cloud_billing import is_cloud_mode, stripe_configured, supabase_configured
        except ImportError:
            return data
    cloud = is_cloud_mode()
    data["cloud"] = cloud
    data["gate_ensemble"] = cloud and plan.id == "free"
    data["stripe"] = stripe_configured()
    data["supabase"] = supabase_configured()
    return data


def guard_send(*, direct: bool, config_edition: str | None = None) -> None:
    """Block Send when the UTC-day quota is used up. Copy/compile does not count.

    Caps (local flags, not a payment API): Free 5, Pro 100, Team and
    Self-hosted unlimited. Raise with ASSURE_EDITION / PEM_EDITION / --edition.
    """
    if not direct:
        return
    plan = current_edition(config_edition)
    if plan.daily_sends is None:
        return
    try:
        from .engine import MatrixError
        from .history import count_sends_today
    except ImportError:
        from engine import MatrixError
        from history import count_sends_today
    used = count_sends_today()
    if used >= plan.daily_sends:
        raise MatrixError(
            f"{plan.label} allows {plan.daily_sends} checks per day. "
            "You've reached today's limit. Pro is 100 checks per day. "
            "There is no checkout in this app. Set ASSURE_EDITION=pro "
            "(or team / self-hosted), or open https://getassureai.com/app."
        )


def clamp_persona(persona: str | None, config_edition: str | None = None) -> str:
    plan = current_edition(config_edition)
    key = (persona or "redhat").strip().lower() or "redhat"
    if key in plan.personas:
        return key
    return "redhat"


def clamp_ensemble_extras(
    primary: str,
    extras: list[str] | None,
    config_edition: str | None = None,
) -> list[str]:
    plan = current_edition(config_edition)
    rest = [name for name in (extras or []) if name and name != primary]
    if plan.id == "free" and not rest:
        for name in FREE_ENSEMBLE:
            if name != primary:
                rest.append(name)
                break
    if plan.id == "pro" and not rest:
        for name in PRO_CONSENSUS:
            if name != primary:
                rest.append(name)
                if len(rest) >= max(0, plan.max_ensemble - 1):
                    break
    cap = max(0, plan.max_ensemble - 1)
    return rest[:cap]


def can_view_history(_edition: str | None = None) -> bool:
    return True


def can_view_full_history(config_edition: str | None = None) -> bool:
    return current_edition(config_edition).full_text_history


def can_export_work(config_edition: str | None = None) -> bool:
    return current_edition(config_edition).full_text_history


def can_search_full(config_edition: str | None = None) -> bool:
    return current_edition(config_edition).full_text_history


def can_refine_any(config_edition: str | None = None) -> bool:
    return current_edition(config_edition).full_text_history


def note_for_clamped_persona(requested: str | None, used: str) -> str | None:
    req = (requested or "").strip().lower()
    if req and req != used:
        return f"{req} persona is a Pro feature. Used {used}."
    return None
