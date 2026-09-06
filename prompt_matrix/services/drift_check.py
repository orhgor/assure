"""CI/CD drift check: compare structured config against vault policy via Z3."""

from __future__ import annotations

import re
from typing import Any

try:
    from ..ledger.truth_engine import TruthLedgerEngine
except ImportError:
    from ledger.truth_engine import TruthLedgerEngine

_NUMERIC = re.compile(r"-?\d+(?:\.\d+)?")
_KV = re.compile(
    r"([A-Za-z_][\w\s.-]{0,40}?)\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*(%|USD|\$|months?|years?)?",
    re.IGNORECASE,
)


def _slug_key(raw: str) -> str:
    slug = re.sub(r"[^\w]+", "_", (raw or "").strip().lower())
    return slug.strip("_") or "metric"


def flatten_config(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested JSON/YAML config to dot-notation keys."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, val in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten_config(val, path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            path = f"{prefix}[{idx}]"
            out.update(flatten_config(item, path))
    else:
        out[prefix or "value"] = obj
    return out


def extract_policy_constraints(policy_text: str) -> dict[str, float]:
    """Extract numeric constraints from policy prose."""
    constraints: dict[str, float] = {}
    for match in _KV.finditer(policy_text or ""):
        key = _slug_key(match.group(1))
        try:
            constraints[key] = float(match.group(2))
        except (TypeError, ValueError):
            continue
    for raw in re.findall(r"([A-Za-z_][\w]*)\s*=\s*(-?\d+(?:\.\d+)?)", policy_text or ""):
        try:
            constraints[_slug_key(raw[0])] = float(raw[1])
        except (TypeError, ValueError):
            continue
    return constraints


def _config_numeric_values(flat: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, val in flat.items():
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            out[_slug_key(key.split(".")[-1])] = float(val)
            out[_slug_key(key)] = float(val)
        elif isinstance(val, str):
            nums = _NUMERIC.findall(val)
            if len(nums) == 1:
                try:
                    out[_slug_key(key.split(".")[-1])] = float(nums[0])
                except ValueError:
                    pass
    return out


def run_drift_check(
    config: dict[str, Any],
    policy_text: str,
) -> dict[str, Any]:
    """Compare config values against policy constraints using Z3 ledger."""
    flat = flatten_config(config)
    policy_constraints = extract_policy_constraints(policy_text)
    config_values = _config_numeric_values(flat)

    if not policy_text.strip():
        return {
            "status": "UNKNOWN",
            "drift_detected": False,
            "findings": [],
            "explanation": "No policy text available for comparison.",
        }

    if not policy_constraints and not config_values:
        return {
            "status": "UNKNOWN",
            "drift_detected": False,
            "findings": [],
            "explanation": "No numeric constraints found in policy or config.",
        }

    engine = TruthLedgerEngine()
    findings: list[dict[str, Any]] = []
    try:
        for key, expected in policy_constraints.items():
            engine.lock_metric(key, expected)

        checked = 0
        for key, actual in config_values.items():
            if key not in policy_constraints:
                for pkey, expected in policy_constraints.items():
                    if pkey in key or key in pkey:
                        ok, msg = engine.verify_metric(pkey, actual)
                        checked += 1
                        if not ok:
                            findings.append(
                                {
                                    "claim": key,
                                    "expected": expected,
                                    "actual": actual,
                                    "confidence": 0.15,
                                    "detail": msg,
                                }
                            )
                        elif abs(actual - expected) > 1e-9:
                            findings.append(
                                {
                                    "claim": key,
                                    "expected": expected,
                                    "actual": actual,
                                    "confidence": 0.85,
                                    "detail": "Value differs from policy.",
                                }
                            )
                continue
            expected = policy_constraints[key]
            ok, msg = engine.verify_metric(key, actual)
            checked += 1
            if not ok or abs(actual - expected) > 1e-9:
                findings.append(
                    {
                        "claim": key,
                        "expected": expected,
                        "actual": actual,
                        "confidence": 0.15 if not ok else 0.85,
                        "detail": msg,
                    }
                )

        drift = len(findings) > 0
        if drift:
            status = "FAIL"
            explanation = f"Drift detected: {len(findings)} config value(s) conflict with policy."
        elif checked > 0:
            status = "PASS"
            explanation = f"All {checked} checked value(s) match policy constraints."
        else:
            status = "UNKNOWN"
            explanation = "Policy and config present but no overlapping numeric keys to verify."
    finally:
        engine.close()

    return {
        "status": status,
        "drift_detected": drift,
        "findings": findings,
        "explanation": explanation,
    }
