"""Convert JSON/YAML configs into JDF tree sections."""

from __future__ import annotations

import json
import uuid
from typing import Any


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _value_to_paragraph(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, indent=2, ensure_ascii=False)
    else:
        text = f"{key}: {value}"
    return {
        "type": "paragraph",
        "id": _new_id("p"),
        "content": text,
        "meta": {"config_key": key},
        "annotations": {"redhat": [], "z3": []},
    }


def _dict_to_section(title: str, data: dict[str, Any]) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    for key, val in data.items():
        if isinstance(val, dict):
            for sub_key, sub_val in val.items():
                children.append(_value_to_paragraph(f"{key}.{sub_key}", sub_val))
        elif isinstance(val, list):
            for idx, item in enumerate(val):
                children.append(_value_to_paragraph(f"{key}[{idx}]", item))
        else:
            children.append(_value_to_paragraph(str(key), val))
    return {
        "type": "section",
        "id": _new_id("sec"),
        "title": title,
        "children": children,
        "meta": {},
        "annotations": {"redhat": [], "z3": []},
    }


def config_to_jdf_section(
    config: dict[str, Any], *, title: str = "Imported Config"
) -> dict[str, Any]:
    """Build a JDF section from a parsed config object."""
    if not isinstance(config, dict):
        return {
            "type": "section",
            "id": _new_id("sec"),
            "title": title,
            "children": [_value_to_paragraph("value", config)],
            "meta": {"import_type": "config"},
            "annotations": {"redhat": [], "z3": []},
        }
    return _dict_to_section(title, config)


def merge_config_into_document(doc: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    """Append config section to document body."""
    body = list(doc.get("body") or [])
    body.append(section)
    doc = dict(doc)
    doc["body"] = body
    meta = dict(doc.get("meta") or {})
    meta["has_config_import"] = True
    doc["meta"] = meta
    return doc


def parse_config_bytes(raw: bytes, filename: str = "") -> dict[str, Any]:
    """Parse JSON or YAML bytes into a dict."""
    text = raw.decode("utf-8", errors="replace").strip()
    ext = (filename or "").rsplit(".", 1)[-1].lower() if filename else ""
    if (
        ext in ("yaml", "yml")
        or text.startswith("---")
        or ": " in text
        and not text.startswith("{")
    ):
        try:
            import yaml
        except ImportError as exc:
            raise ValueError("PyYAML is required for YAML import.") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        return {"value": data}
    return data
