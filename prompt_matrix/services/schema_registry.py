"""Parsure schema registry: the document-type taxonomy as data (plan Part 4.1/4.4, 2026-09-27).

The taxonomy used to be static code — ``_f()`` calls in ``field_extractor``
— so adding a document type meant editing the extractor. The benchmark
(``bench-v1``, 2026-09-26) showed the cost: four of its 28 inputs expect a
type with no schema (endorsement, cancellation notice, repair estimate,
schedule of forms) and routing sat at 82 % against a 95 % gate for that
reason alone. This module builds the taxonomy from two sources and rebinds
the names the rest of the code reads (``fx.FIELD_TAXONOMY``,
``fx.TYPE_KEYWORDS``, ``fx.TYPE_FAMILY``, ``fx.DOCUMENT_TYPES``):

1. the **built-in** schemas — the ``_f()`` specs still in ``field_extractor``
   (kept there so their docstrings and history stay next to the regexes);
2. **runtime** schemas — JSON files in ``prompt_matrix/schemas/`` and in the
   directory (or ``os.pathsep``-separated directories) named by
   ``ASSURE_SCHEMA_DIR``.

Runtime schema file format (``parsure-schema-v1``)::

    {
      "format": "parsure-schema-v1",
      "document_type": "cancellation_notice",     # ^[a-z][a-z0-9_]{1,63}$, not reserved
      "family": "auto",                            # one of field_extractor.DOCUMENT_FAMILIES
      "label": "Cancellation notice",
      "description": "why this type exists / what wording it anchors on",
      "keywords": ["notice of cancellation", ...], # >= MIN_KEYWORD_MATCHES phrases, matched
                                                   # case-insensitively as whole phrases
      "fields": [
        {"name": "policy_number", "use": "auto_policy.policy_number"},   # copy a built-in spec
        {"name": "cancellation_reason", "label": "Reason for cancellation",
         "field_type": "text",                     # one of field_extractor.FIELD_TYPES
         "anchors": ["reason\\s+for\\s+cancellation"],   # label regexes, compiled like the label pass
         "compliance_bound": false}
      ]
    }

Validation is strict and failure is quiet: a file that does not parse, names
a bad type/family/field type, or carries an anchor that does not compile the
way ``field_extractor._find_candidates`` compiles it, is **skipped with a
logged reason** (also listed under ``registry().rejected`` and on
``GET /api/parsure/schemas``) — never a crash at import time, because the
extractor imports this module and a broken schema file must not take the
ingest worker down.

Order matters: classification breaks ties by taxonomy order
(``DOCUMENT_TYPES``), so built-ins keep their original order and runtime
schemas follow, sorted by directory then file name. A runtime schema may
override a built-in type name (the plan's "runtime first"); the override is
logged and recorded on the schema (``overrides``).

Families are not extensible here: ``document_family`` is tuned on the cue
lists in ``field_extractor.FAMILY_CUES``, and a type whose family the gate
cannot name would never be chosen. A schema naming an unknown family is
rejected with that reason.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import field_extractor as fx
except ImportError:  # pragma: no cover - flat-import fallback
    import field_extractor as fx  # type: ignore

log = logging.getLogger(__name__)

SCHEMA_FORMAT = "parsure-schema-v1"
#: Shipped runtime schemas: ``prompt_matrix/schemas/*.json``.
BUILTIN_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
#: Extra schema directories, ``os.pathsep``-separated.
SCHEMA_DIR_ENV = "ASSURE_SCHEMA_DIR"
_TYPE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: Fewer keywords than this and ``classify_document`` could never name the
#: type (it needs ``MIN_KEYWORD_MATCHES`` hits), so the schema is unreachable.
MIN_KEYWORDS = 3
MAX_FIELDS = 40


class SchemaError(ValueError):
    """A schema document that must be skipped; ``str(exc)`` is the reason."""


def _reserved_type_names() -> frozenset[str]:
    return frozenset({"uncertain", "mixed_bundle", "unknown", "other"}) | {f"{fam}_unknown" for fam in fx.DOCUMENT_FAMILIES}


@dataclass(frozen=True)
class Schema:
    document_type: str
    family: str
    label: str
    keywords: tuple[str, ...]
    fields: tuple[fx.FieldSpec, ...]
    source: str
    description: str = ""
    overrides: str | None = None

    def to_dict(self, *, anchors: bool = True) -> dict[str, Any]:
        return {
            "document_type": self.document_type,
            "family": self.family,
            "label": self.label,
            "description": self.description,
            "source": self.source,
            "overrides": self.overrides,
            "keywords": list(self.keywords),
            "fields": [
                {
                    "name": s.name, "label": s.label, "field_type": s.field_type, "compliance_bound": bool(s.compliance_bound),
                    **({"anchors": list(s.anchors)} if anchors else {}),
                }
                for s in self.fields
            ],
        }


@dataclass
class Registry:
    schemas: dict[str, Schema]
    rejected: list[dict[str, str]] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    loaded_at: str = ""

    @property
    def builtin(self) -> list[str]:
        return [t for t, s in self.schemas.items() if s.source == "builtin"]

    @property
    def runtime(self) -> list[str]:
        return [t for t, s in self.schemas.items() if s.source != "builtin"]


_REGISTRY: Registry | None = None
#: Schemas added through ``register_schema`` (tests, config code); they survive
#: a ``reload()`` so a process-level registration is not lost when a file dir
#: is re-read.
_IN_MEMORY: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _type_label(document_type: str) -> str:
    return document_type.replace("_", " ").strip().capitalize()


# --------------------------------------------------------------------------
# Built-ins
# --------------------------------------------------------------------------

def builtin_schemas() -> dict[str, Schema]:
    """The ``_f()`` taxonomy as ``Schema`` records, in ``field_extractor``'s
    original order (``fx._BUILTIN_*`` are snapshots taken before the first
    ``reload`` rebinds the public names)."""
    out: dict[str, Schema] = {}
    for doc_type in fx._BUILTIN_DOCUMENT_TYPES:
        out[doc_type] = Schema(
            document_type=doc_type,
            family=fx._BUILTIN_TYPE_FAMILY[doc_type],
            label=_type_label(doc_type),
            keywords=tuple(fx._BUILTIN_TYPE_KEYWORDS[doc_type]),
            fields=tuple(fx._BUILTIN_FIELD_TAXONOMY[doc_type]),
            source="builtin",
        )
    return out


# --------------------------------------------------------------------------
# Runtime schema parsing / validation
# --------------------------------------------------------------------------

def _compile_anchor(anchor: str, field_type: str) -> None:
    """Compile ``anchor`` exactly as the label pass will (``_find_candidates``):
    a regex that compiles alone but clashes with the ``sep``/``val`` groups or
    breaks the lookarounds must be rejected here, not at ingest time."""
    re.compile(
        r"(?<![a-z])" + anchor + r"(?!'|[a-z])" + r"(?P<sep>" + fx._SEP + r")(?P<val>" + fx._value_pattern(field_type) + r")",
        re.I,
    )


def _parse_field(item: Any, index: int, builtins: dict[str, Schema]) -> fx.FieldSpec:
    if not isinstance(item, dict):
        raise SchemaError(f"fields[{index}] is not an object")
    use = item.get("use")
    if use is not None:
        if not isinstance(use, str) or use.count(".") != 1:
            raise SchemaError(f"fields[{index}].use must be '<builtin_type>.<field_name>'")
        src_type, src_field = use.split(".", 1)
        src = builtins.get(src_type)
        base = next((s for s in (src.fields if src else ()) if s.name == src_field), None)
        if base is None:
            raise SchemaError(f"fields[{index}].use names an unknown built-in spec: {use}")
        name = item.get("name") or base.name
        if not isinstance(name, str) or not _FIELD_NAME_RE.match(name):
            raise SchemaError(f"fields[{index}].name is not a valid field name: {name!r}")
        compliance = item.get("compliance_bound", base.compliance_bound)
        if not isinstance(compliance, bool):
            raise SchemaError(f"fields[{index}].compliance_bound must be true/false")
        return fx.FieldSpec(name=name, label=str(item.get("label") or base.label), field_type=base.field_type,
                            anchors=tuple(base.anchors), compliance_bound=compliance, aliases=tuple(base.aliases))
    name = item.get("name")
    if not isinstance(name, str) or not _FIELD_NAME_RE.match(name):
        raise SchemaError(f"fields[{index}].name is not a valid field name: {name!r}")
    field_type = item.get("field_type")
    if field_type not in fx.FIELD_TYPES:
        raise SchemaError(f"fields[{index}] ({name}): field_type {field_type!r} is not one of {', '.join(fx.FIELD_TYPES)}")
    anchors = item.get("anchors")
    if not isinstance(anchors, list) or not anchors or not all(isinstance(a, str) and a.strip() for a in anchors):
        raise SchemaError(f"fields[{index}] ({name}): anchors must be a non-empty list of regex strings")
    for anchor in anchors:
        try:
            _compile_anchor(anchor, field_type)
        except re.error as exc:
            raise SchemaError(f"fields[{index}] ({name}): anchor {anchor!r} does not compile: {exc}") from exc
    compliance = item.get("compliance_bound", False)
    if not isinstance(compliance, bool):
        raise SchemaError(f"fields[{index}] ({name}): compliance_bound must be true/false")
    label = item.get("label") or _type_label(name)
    if not isinstance(label, str) or not label.strip():
        raise SchemaError(f"fields[{index}] ({name}): label must be a non-empty string")
    return fx.FieldSpec(name=name, label=label.strip(), field_type=field_type, anchors=tuple(anchors), compliance_bound=compliance)


def parse_schema(data: Any, *, source: str, builtins: dict[str, Schema] | None = None) -> Schema:
    """A validated ``Schema`` from one JSON document; raises ``SchemaError``
    with the reason to log/skip."""
    builtins = builtins if builtins is not None else builtin_schemas()
    if not isinstance(data, dict):
        raise SchemaError("schema document is not a JSON object")
    fmt = data.get("format", SCHEMA_FORMAT)
    if fmt != SCHEMA_FORMAT:
        raise SchemaError(f"format {fmt!r} is not {SCHEMA_FORMAT}")
    doc_type = data.get("document_type")
    if not isinstance(doc_type, str) or not _TYPE_NAME_RE.match(doc_type):
        raise SchemaError(f"document_type {doc_type!r} must match {_TYPE_NAME_RE.pattern}")
    if doc_type in _reserved_type_names():
        raise SchemaError(f"document_type {doc_type!r} is reserved")
    family = data.get("family")
    if family not in fx.DOCUMENT_FAMILIES:
        raise SchemaError(f"family {family!r} is not one of {', '.join(fx.DOCUMENT_FAMILIES)}")
    keywords = data.get("keywords")
    if not isinstance(keywords, list) or not all(isinstance(k, str) and k.strip() for k in keywords):
        raise SchemaError("keywords must be a list of non-empty strings")
    keywords_norm = tuple(dict.fromkeys(k.strip().lower() for k in keywords))
    if len(keywords_norm) < MIN_KEYWORDS:
        raise SchemaError(f"keywords: {len(keywords_norm)} given, at least {MIN_KEYWORDS} needed for the classifier to name the type")
    raw_fields = data.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise SchemaError("fields must be a non-empty list")
    if len(raw_fields) > MAX_FIELDS:
        raise SchemaError(f"fields: {len(raw_fields)} given, at most {MAX_FIELDS}")
    specs = [_parse_field(item, i, builtins) for i, item in enumerate(raw_fields)]
    names = [s.name for s in specs]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise SchemaError(f"duplicate field names: {', '.join(dupes)}")
    label = data.get("label") or _type_label(doc_type)
    if not isinstance(label, str) or not label.strip():
        raise SchemaError("label must be a non-empty string")
    description = data.get("description") or ""
    if not isinstance(description, str):
        raise SchemaError("description must be a string")
    return Schema(document_type=doc_type, family=family, label=label.strip(), keywords=keywords_norm, fields=tuple(specs),
                  source=source, description=description.strip())


def load_schema_file(path: Path, *, builtins: dict[str, Schema] | None = None) -> Schema:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SchemaError(f"cannot read JSON: {exc}") from exc
    return parse_schema(data, source=str(path), builtins=builtins)


def schema_dirs() -> list[Path]:
    """The shipped directory first, then ``ASSURE_SCHEMA_DIR`` entries in order."""
    dirs = [BUILTIN_SCHEMA_DIR]
    raw = os.environ.get(SCHEMA_DIR_ENV, "").strip()
    for part in raw.split(os.pathsep) if raw else []:
        part = part.strip()
        if part:
            dirs.append(Path(part).expanduser())
    return dirs


def load_runtime_schemas(dirs: list[Path] | None = None, *, builtins: dict[str, Schema] | None = None) -> tuple[list[Schema], list[dict[str, str]]]:
    """Every ``*.json`` under ``dirs`` (sorted per directory) as schemas, plus
    the files skipped and why. A missing directory is skipped silently — the
    env var may point at a mount that is not there in a test process."""
    builtins = builtins if builtins is not None else builtin_schemas()
    loaded: list[Schema] = []
    rejected: list[dict[str, str]] = []
    for d in dirs if dirs is not None else schema_dirs():
        if not d.is_dir():
            continue
        for path in sorted(p for p in d.iterdir() if p.suffix.lower() == ".json" and p.is_file()):
            try:
                loaded.append(load_schema_file(path, builtins=builtins))
            except SchemaError as exc:
                log.warning("schema_registry: skipped %s: %s", path, exc)
                rejected.append({"path": str(path), "reason": str(exc)})
            except Exception as exc:  # noqa: BLE001 — one bad file never stops the others
                log.exception("schema_registry: skipped %s", path)
                rejected.append({"path": str(path), "reason": f"{type(exc).__name__}: {exc}"})
    return loaded, rejected


# --------------------------------------------------------------------------
# Build, apply, query
# --------------------------------------------------------------------------

def build_registry(dirs: list[Path] | None = None) -> Registry:
    builtins = builtin_schemas()
    schemas: dict[str, Schema] = dict(builtins)
    runtime, rejected = load_runtime_schemas(dirs, builtins=builtins)
    for data in _IN_MEMORY.values():
        try:
            runtime.append(parse_schema(data, source="runtime", builtins=builtins))
        except SchemaError as exc:  # registered through register_schema, already validated; defensive
            rejected.append({"path": f"runtime:{data.get('document_type')}", "reason": str(exc)})
    for schema in runtime:
        previous = schemas.get(schema.document_type)
        if previous is not None:
            log.info("schema_registry: %s overrides the %s schema for %s", schema.source, previous.source, schema.document_type)
            schema = Schema(**{**schema.__dict__, "overrides": previous.source})
        schemas[schema.document_type] = schema
    use_dirs = [str(d) for d in (dirs if dirs is not None else schema_dirs())]
    return Registry(schemas=schemas, rejected=rejected, dirs=use_dirs, loaded_at=_now())


def apply(reg: Registry) -> None:
    """Rebind the public taxonomy names on ``field_extractor`` from ``reg``.

    The dicts are mutated in place so a ``from field_extractor import
    FIELD_TAXONOMY`` taken earlier still sees the change; ``DOCUMENT_TYPES``
    is a tuple and is rebound — every caller in the tree reads it as
    ``fx.DOCUMENT_TYPES`` at call time (checked 2026-09-27: routers/parsure_routes,
    services/llm_extraction, scripts/benchmark, tests/test_benchmark_harness)."""
    fx.FIELD_TAXONOMY.clear()
    fx.FIELD_TAXONOMY.update({t: list(s.fields) for t, s in reg.schemas.items()})
    fx.TYPE_KEYWORDS.clear()
    fx.TYPE_KEYWORDS.update({t: tuple(s.keywords) for t, s in reg.schemas.items()})
    fx.TYPE_FAMILY.clear()
    fx.TYPE_FAMILY.update({t: s.family for t, s in reg.schemas.items()})
    fx.DOCUMENT_TYPES = tuple(reg.schemas)


def reload(dirs: list[Path] | None = None) -> Registry:
    """Rebuild from the built-ins, the schema directories and the in-memory
    registrations, apply to ``field_extractor``, return the registry. Never
    raises: on an unexpected error the previous taxonomy stays bound and the
    error is logged."""
    global _REGISTRY
    try:
        reg = build_registry(dirs)
        apply(reg)
        _REGISTRY = reg
    except Exception:  # noqa: BLE001 — the extractor must import even if the registry is broken
        log.exception("schema_registry: reload failed; built-in taxonomy stays in effect")
        if _REGISTRY is None:
            reg = Registry(schemas=builtin_schemas(), rejected=[{"path": "<registry>", "reason": "reload failed; see log"}], loaded_at=_now())
            apply(reg)
            _REGISTRY = reg
    return _REGISTRY


def registry() -> Registry:
    return _REGISTRY if _REGISTRY is not None else reload()


def get_schema(document_type: Any) -> Schema | None:
    return registry().schemas.get(str(document_type or ""))


def list_schemas(*, anchors: bool = False) -> list[dict[str, Any]]:
    return [s.to_dict(anchors=anchors) for s in registry().schemas.values()]


def register_schema(data: dict[str, Any]) -> Schema:
    """Register (or replace) one schema for this process from a dict in the
    file format, validate it, and rebind the taxonomy. Raises ``SchemaError``
    on a bad document — a programmatic caller wants to know, unlike a dropped
    file. Survives later ``reload()`` calls."""
    schema = parse_schema(data, source="runtime")
    _IN_MEMORY[schema.document_type] = data
    reload()
    return schema


def unregister_schema(document_type: str) -> bool:
    """Remove an in-memory registration; file and built-in schemas are not
    touched. Returns whether anything was removed."""
    removed = _IN_MEMORY.pop(str(document_type), None) is not None
    if removed:
        reload()
    return removed
