"""JDF (JSON Document Format) AST schemas for Assure document engineering.

The JDF tree is the immutable core: document content lives in nodes;
audit metadata (Red-Hat, Z3) lives in ``annotations`` and is excluded from export.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from typing import Annotated, Any, Callable, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class JDFProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["internal_doc", "academic_paper", "news_article", "web_url"] = (
        "internal_doc"
    )
    source_name: str = ""
    url_or_doi: str = ""
    source_id: str = ""
    page_number: str = ""
    extracted_quote: str = ""
    accessed_date: str = ""


class JDFRedhatAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    status: Literal["open", "resolved", "dismissed"] = "open"


class JDFZ3Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    message: str
    status: Literal["violation", "pass"] = "violation"
    canonical_key: str = ""


class JDFNodeAnnotations(BaseModel):
    """Metadata attached to nodes — never exported to .docx."""

    model_config = ConfigDict(extra="forbid")

    redhat: list[JDFRedhatAnnotation] = Field(default_factory=list)
    z3: list[JDFZ3Annotation] = Field(default_factory=list)


def empty_annotations() -> dict[str, Any]:
    return JDFNodeAnnotations().model_dump(mode="json")


class JDFParagraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["paragraph"] = "paragraph"
    id: str
    content: str
    entities_referenced: list[str] = Field(default_factory=list)
    provenance: list[JDFProvenance] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


class JDFCalloutNode(BaseModel):
    """In-document callouts (warning/insight). Red-Hat critiques use ``annotations.redhat``."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["callout"] = "callout"
    id: str
    variant: Literal["warning", "adversarial_redhat", "insight"]
    title: str
    content: str
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


class JDFTableNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["table"] = "table"
    id: str
    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    bound_entities: list[str] = Field(default_factory=list)
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


class JDFImageNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image"] = "image"
    id: str
    src: str
    alt: str = ""
    caption: str = ""
    width: int | None = None
    height: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


JDFBlockNode = Annotated[
    Union[JDFParagraphNode, JDFCalloutNode, JDFTableNode, JDFImageNode],
    Field(discriminator="type"),
]


class JDFSectionNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["section"] = "section"
    id: str
    title: str
    children: list[JDFParagraphNode | JDFCalloutNode | JDFTableNode | JDFImageNode] = Field(
        default_factory=list
    )
    meta: dict[str, Any] = Field(default_factory=dict)
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


class JDFDocumentTree(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    meta: dict[str, Any] = Field(default_factory=dict)
    truth_ledger: dict[str, float | str | int] = Field(default_factory=dict)
    body: list[JDFSectionNode] = Field(default_factory=list)


def new_node_id(prefix: str = "node") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def document_to_dict(tree: JDFDocumentTree | dict[str, Any]) -> dict[str, Any]:
    if isinstance(tree, JDFDocumentTree):
        return tree.model_dump(mode="json")
    return copy.deepcopy(tree)


_CITE_TAG_RE = re.compile(
    r'<cite\s+((?:[^>"\']|"[^"]*"|\'[^\']*\')*)\s*>(.*?)</cite>',
    re.IGNORECASE | re.DOTALL,
)
_CITE_ATTR_RE = re.compile(r'([\w-]+)=["\']([^"\']*)["\']')


def _parse_cite_attributes(attr_blob: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _CITE_ATTR_RE.finditer(attr_blob or "")}


def _migrate_legacy_provenance(prov: Any) -> list[dict[str, Any]]:
    if prov is None:
        return []
    if isinstance(prov, list):
        out: list[dict[str, Any]] = []
        for item in prov:
            if isinstance(item, dict):
                out.append(_migrate_legacy_provenance_entry(item))
        return out
    if isinstance(prov, dict):
        return [_migrate_legacy_provenance_entry(prov)]
    return []


def _migrate_legacy_provenance_entry(prov: dict[str, Any]) -> dict[str, Any]:
    if prov.get("source_type") in ("internal_doc", "academic_paper", "news_article", "web_url"):
        return prov
    migrated = {
        "source_type": prov.get("source_type") or "internal_doc",
        "source_name": prov.get("source_name") or prov.get("source_file") or "",
        "url_or_doi": prov.get("url_or_doi") or "",
        "source_id": prov.get("source_id") or prov.get("id") or "",
        "page_number": prov.get("page_number") or prov.get("page_or_timestamp") or "",
        "extracted_quote": prov.get("extracted_quote") or prov.get("exact_quote") or "",
        "accessed_date": prov.get("accessed_date") or "",
    }
    if prov.get("source_type") not in ("internal_doc", "academic_paper", "news_article", "web_url"):
        if migrated["url_or_doi"]:
            migrated["source_type"] = "web_url"
        else:
            migrated["source_type"] = "internal_doc"
    return migrated


def extract_citations_from_content(content: str) -> tuple[str, list[dict[str, Any]]]:
    """Pull inline ``<cite …>`` tags into provenance entries; return cleaned content."""
    provenance: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        attrs = _parse_cite_attributes(match.group(1))
        inner = (match.group(2) or "").strip()
        source_id = attrs.get("id") or attrs.get("source_id") or new_node_id("cite")
        if source_id in seen:
            return inner
        seen.add(source_id)
        source_type = attrs.get("source_type") or "internal_doc"
        if source_type not in ("internal_doc", "academic_paper", "news_article", "web_url"):
            source_type = "web_url" if attrs.get("url_or_doi") else "internal_doc"
        provenance.append(
            {
                "source_type": source_type,
                "source_name": attrs.get("source_name") or attrs.get("source_file") or "",
                "url_or_doi": attrs.get("url_or_doi") or "",
                "source_id": source_id,
                "page_number": attrs.get("page_number") or attrs.get("page_or_timestamp") or "",
                "extracted_quote": inner
                or attrs.get("extracted_quote")
                or attrs.get("exact_quote")
                or "",
                "accessed_date": attrs.get("accessed_date") or "",
            }
        )
        return inner

    cleaned = _CITE_TAG_RE.sub(_replace, content or "")
    return cleaned, provenance


def _merge_provenance(existing: list[Any], extracted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = _migrate_legacy_provenance(existing)
    seen = {p.get("source_id") for p in merged if p.get("source_id")}
    for item in extracted:
        sid = item.get("source_id")
        if sid and sid in seen:
            continue
        if sid:
            seen.add(sid)
        merged.append(item)
    return merged


def enrich_document_citations(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract inline cite tags and normalize provenance lists on paragraph nodes."""
    doc = copy.deepcopy(raw)
    for section in doc.get("body") or []:
        if not isinstance(section, dict):
            continue
        for child in section.get("children") or []:
            if not isinstance(child, dict) or child.get("type") != "paragraph":
                continue
            content = str(child.get("content") or "")
            cleaned, extracted = extract_citations_from_content(content)
            child["content"] = cleaned
            child["provenance"] = _merge_provenance(child.get("provenance"), extracted)
    return doc


def collect_unique_provenance(tree: JDFDocumentTree | dict[str, Any]) -> list[dict[str, Any]]:
    """Dedupe provenance entries across the document tree (for export References)."""
    seen: set[str] = set()
    refs: list[dict[str, Any]] = []
    for node in flatten_nodes(tree):
        for prov in _migrate_legacy_provenance(node.get("provenance")):
            key = (
                prov.get("source_id")
                or prov.get("url_or_doi")
                or f"{prov.get('source_name')}:{prov.get('page_number')}"
            )
            if not key or key in seen:
                continue
            seen.add(key)
            refs.append(prov)
    return refs


def parse_document(raw: dict[str, Any] | str) -> JDFDocumentTree:
    """Strict Pydantic validation before any SQLite write."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        raw = enrich_document_citations(raw)
    return JDFDocumentTree.model_validate(raw)


def validate_document(raw: dict[str, Any] | str) -> tuple[JDFDocumentTree | None, str | None]:
    try:
        return parse_document(raw), None
    except ValidationError as exc:
        return None, str(exc)


def flatten_nodes(tree: JDFDocumentTree | dict[str, Any]) -> list[dict[str, Any]]:
    """Return block-level nodes in document order (sections flattened)."""
    doc = document_to_dict(tree)
    ordered: list[dict[str, Any]] = []
    for section in doc.get("body") or []:
        for child in section.get("children") or []:
            if isinstance(child, dict) and child.get("id"):
                ordered.append(child)
    return ordered


def find_node_index(
    tree: JDFDocumentTree | dict[str, Any], target_id: str
) -> tuple[int, dict[str, Any]] | None:
    for idx, node in enumerate(flatten_nodes(tree)):
        if node.get("id") == target_id:
            return idx, node
    return None


def get_node_by_id(tree: JDFDocumentTree | dict[str, Any], node_id: str) -> dict[str, Any] | None:
    doc = document_to_dict(tree)
    for section in doc.get("body") or []:
        if isinstance(section, dict) and section.get("id") == node_id:
            return section
        for child in section.get("children") or []:
            if isinstance(child, dict) and child.get("id") == node_id:
                return child
    return None


def splice_node(
    tree: dict[str, Any], target_id: str, new_node: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Recursively replace a node by id. Returns (mutated_tree, found)."""
    mutated = copy.deepcopy(tree)

    def _walk_section(section: dict[str, Any]) -> bool:
        children = section.get("children") or []
        for i, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            if child.get("id") == target_id:
                children[i] = copy.deepcopy(new_node)
                section["children"] = children
                return True
        return False

    for section in mutated.get("body") or []:
        if isinstance(section, dict) and _walk_section(section):
            return mutated, True
    return mutated, False


def insert_node_after_anchor(
    tree: dict[str, Any],
    insert_after_id: str | None,
    new_node: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Dock new_node after a section id or child id; append to section 1 if anchor missing."""
    mutated = copy.deepcopy(tree)
    body = mutated.setdefault("body", [])
    if not body:
        body.append(
            {
                "type": "section",
                "id": new_node_id("sec"),
                "title": "Section 1",
                "children": [copy.deepcopy(new_node)],
                "meta": {},
                "annotations": empty_annotations(),
            }
        )
        return mutated, True

    anchor = (insert_after_id or "").strip()
    if not anchor:
        body[0].setdefault("children", []).append(copy.deepcopy(new_node))
        return mutated, True

    for section in body:
        if not isinstance(section, dict):
            continue
        if section.get("id") == anchor:
            children = section.setdefault("children", [])
            children.insert(0, copy.deepcopy(new_node))
            return mutated, True
        children = section.get("children") or []
        for idx, child in enumerate(children):
            if isinstance(child, dict) and child.get("id") == anchor:
                children.insert(idx + 1, copy.deepcopy(new_node))
                section["children"] = children
                return mutated, True

    body[0].setdefault("children", []).append(copy.deepcopy(new_node))
    return mutated, True


def upsert_block_node(
    tree: dict[str, Any],
    node_id: str,
    node_data: dict[str, Any],
    *,
    insert_after_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Update an existing block node by id, or insert it (append / after anchor)."""
    mutated, found = splice_node(tree, node_id, node_data)
    if found:
        return mutated, True
    mutated, _ = insert_node_after_anchor(tree, insert_after_id, node_data)
    return mutated, False


def _ensure_annotations(node: dict[str, Any]) -> dict[str, Any]:
    node.setdefault("annotations", empty_annotations())
    ann = node["annotations"]
    ann.setdefault("redhat", [])
    ann.setdefault("z3", [])
    return node


def attach_redhat_annotation(
    tree: dict[str, Any],
    node_id: str,
    text: str,
    *,
    status: Literal["open", "resolved", "dismissed"] = "open",
) -> tuple[dict[str, Any], bool]:
    """Inject Red-Hat critique into ``annotations.redhat`` — never as a sibling node."""
    node = get_node_by_id(tree, node_id)
    if not node:
        return tree, False
    node = copy.deepcopy(node)
    node = _ensure_annotations(node)
    node["annotations"]["redhat"].append(
        {"id": new_node_id("crit"), "text": text.strip(), "status": status}
    )
    return splice_node(tree, node_id, node)


def attach_z3_annotation(
    tree: dict[str, Any],
    node_id: str,
    message: str,
    *,
    canonical_key: str = "",
    status: Literal["violation", "pass"] = "violation",
) -> tuple[dict[str, Any], bool]:
    """Store Z3 result in ``annotations.z3`` on the offending node."""
    node = get_node_by_id(tree, node_id)
    if not node:
        return tree, False
    node = copy.deepcopy(node)
    node = _ensure_annotations(node)
    node["annotations"]["z3"].append(
        {
            "id": new_node_id("z3"),
            "message": message.strip(),
            "status": status,
            "canonical_key": canonical_key,
        }
    )
    return splice_node(tree, node_id, node)


def draft_text_to_sections(text: str) -> list[dict[str, Any]]:
    """Convert draft prose into validated section/paragraph AST fragments."""
    stripped = (text or "").strip()
    if not stripped:
        return []

    sections: list[dict[str, Any]] = []
    current_title = "Draft"
    current_children: list[dict[str, Any]] = []

    def _flush_section() -> None:
        nonlocal current_title, current_children
        if not current_children:
            return
        sections.append(
            {
                "type": "section",
                "id": new_node_id("sec"),
                "title": current_title or "Draft",
                "children": current_children,
                "meta": {"source": "generate_draft"},
                "annotations": empty_annotations(),
            }
        )
        current_children = []

    for block in re.split(r"\n\n+", stripped):
        block = block.strip()
        if not block:
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", block)
        if heading:
            _flush_section()
            current_title = heading.group(2).strip()
            continue
        current_children.append(
            {
                "type": "paragraph",
                "id": new_node_id("para"),
                "content": block,
                "entities_referenced": [],
                "meta": {"source": "generate_draft"},
                "annotations": empty_annotations(),
            }
        )

    _flush_section()

    if not sections:
        sections.append(
            {
                "type": "section",
                "id": new_node_id("sec"),
                "title": "Draft",
                "children": [
                    {
                        "type": "paragraph",
                        "id": new_node_id("para"),
                        "content": stripped,
                        "entities_referenced": [],
                        "meta": {"source": "generate_draft"},
                        "annotations": empty_annotations(),
                    }
                ],
                "meta": {"source": "generate_draft"},
                "annotations": empty_annotations(),
            }
        )
    return sections


def build_document_from_draft(
    project_id: str,
    draft_text: str,
    *,
    truth_ledger: dict[str, float | str | int] | None = None,
) -> JDFDocumentTree:
    """Parse streamed draft into a strictly typed ``JDFDocumentTree``."""
    body = draft_text_to_sections(draft_text)
    return JDFDocumentTree(
        document_id=f"doc-{project_id}",
        meta={"project_id": project_id, "source": "generate_draft"},
        truth_ledger=truth_ledger or {},
        body=body,  # type: ignore[arg-type]
    )


def merge_document_bodies(
    base: JDFDocumentTree | dict[str, Any],
    extra_sections: list[dict[str, Any]],
) -> JDFDocumentTree:
    """Append generated sections onto an existing tree (for dock)."""
    doc = parse_document(document_to_dict(base))
    merged_body = [s.model_dump(mode="json") if hasattr(s, "model_dump") else s for s in doc.body]
    merged_body.extend(extra_sections)
    return JDFDocumentTree(
        document_id=doc.document_id,
        meta=dict(doc.meta),
        truth_ledger=dict(doc.truth_ledger),
        body=merged_body,  # type: ignore[arg-type]
    )


def node_text(node: dict[str, Any]) -> str:
    ntype = node.get("type")
    if ntype == "paragraph":
        return str(node.get("content") or "")
    if ntype == "callout":
        return f"{node.get('title') or ''}\n{node.get('content') or ''}".strip()
    if ntype == "table":
        return str(node.get("caption") or "Table")
    return str(node.get("content") or "")


def apply_z3_violations_to_tree(
    tree: dict[str, Any],
    violations: list[str],
    *,
    metric_parser: Callable[[str], list[tuple[str, float]]] | None = None,
) -> dict[str, Any]:
    """Attach Z3 violations to the first paragraph that references the metric key."""
    if not violations:
        return tree
    mutated = copy.deepcopy(tree)
    for violation in violations:
        key_match = re.search(r"Metric '([^']+)'", violation or "")
        key = key_match.group(1) if key_match else ""
        target_id: str | None = None
        for node in flatten_nodes(mutated):
            if node.get("type") != "paragraph":
                continue
            content = str(node.get("content") or "")
            if key and key.lower() in content.lower():
                target_id = str(node.get("id"))
                break
        if not target_id:
            nodes = flatten_nodes(mutated)
            if nodes:
                target_id = str(nodes[0].get("id"))
        if target_id:
            mutated, _ = attach_z3_annotation(
                mutated,
                target_id,
                violation,
                canonical_key=key,
                status="violation",
            )
    return mutated


def apply_redhat_critiques_to_tree(
    tree: dict[str, Any],
    critiques: list[dict[str, Any]],
    *,
    target_node_id: str | None = None,
) -> dict[str, Any]:
    """Attach Red-Hat text to ``annotations.redhat`` on target node(s)."""
    if not critiques:
        return tree
    mutated = copy.deepcopy(tree)
    node_id = target_node_id
    if not node_id:
        nodes = flatten_nodes(mutated)
        node_id = str(nodes[0]["id"]) if nodes else None
    if not node_id:
        return mutated
    for crit in critiques:
        text = str(crit.get("content") or crit.get("text") or "").strip()
        if text:
            mutated, _ = attach_redhat_annotation(mutated, node_id, text)
    return mutated


def _numeric_string_forms(value: float) -> list[str]:
    """Plausible ways ``value`` might appear as text (whole, comma-grouped, decimal)."""
    forms: set[str] = set()
    if value == int(value):
        forms.add(str(int(value)))
        forms.add(f"{int(value):,}")
    else:
        forms.add(str(value))
        forms.add(f"{value:,.2f}")
        forms.add(f"{value:.2f}")
    return [f for f in forms if f and len(f) >= 2]


def attach_substrate_provenance_to_tree(
    tree: dict[str, Any],
    locks: list[dict[str, Any]],
    substrate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Best-effort provenance stamping for the Substrate Vault.

    If a lock's numeric value shows up in both a substrate file's extracted
    text and a paragraph's content, attach a ``JDFProvenance`` entry naming
    that file to that paragraph. This is a text-match heuristic — the same
    rigor ``apply_z3_violations_to_tree`` already uses for locating the node
    a violation belongs to — not claim-level NLP attribution.
    """
    if not locks or not substrate_rows:
        return tree
    mutated = document_to_dict(tree)

    for lock in locks:
        try:
            value = float(lock.get("value"))
        except (TypeError, ValueError):
            continue
        value_forms = _numeric_string_forms(value)
        if not value_forms:
            continue

        matched_row: dict[str, Any] | None = None
        matched_quote = ""
        for row in substrate_rows:
            text = str(row.get("extracted_text") or "")
            for form in value_forms:
                idx = text.find(form)
                if idx >= 0:
                    matched_row = row
                    start = max(0, idx - 40)
                    end = min(len(text), idx + len(form) + 40)
                    matched_quote = text[start:end].strip()
                    break
            if matched_row:
                break
        if not matched_row:
            continue

        target_id: str | None = None
        for node in flatten_nodes(mutated):
            if node.get("type") != "paragraph":
                continue
            content = str(node.get("content") or "")
            if any(form in content for form in value_forms):
                target_id = str(node.get("id"))
                break
        if not target_id:
            continue

        node = get_node_by_id(mutated, target_id)
        if not node:
            continue
        node = copy.deepcopy(node)
        existing = node.get("provenance") or []
        source_id = str(matched_row.get("id") or "")
        if any(isinstance(p, dict) and p.get("source_id") == source_id for p in existing):
            continue
        existing.append(
            {
                "source_type": "internal_doc",
                "source_name": matched_row.get("filename") or "",
                "url_or_doi": "",
                "source_id": source_id,
                "page_number": "",
                "extracted_quote": matched_quote,
                "accessed_date": "",
            }
        )
        node["provenance"] = existing
        mutated, _ = splice_node(mutated, target_id, node)

    return mutated


# Re-export for type checkers
__all__ = [
    "JDFDocumentTree",
    "JDFSectionNode",
    "JDFParagraphNode",
    "JDFTableNode",
    "JDFCalloutNode",
    "JDFNodeAnnotations",
    "JDFRedhatAnnotation",
    "JDFZ3Annotation",
    "attach_redhat_annotation",
    "attach_z3_annotation",
    "build_document_from_draft",
    "draft_text_to_sections",
    "flatten_nodes",
    "get_node_by_id",
    "insert_node_after_anchor",
    "merge_document_bodies",
    "new_node_id",
    "parse_document",
    "splice_node",
    "upsert_block_node",
    "validate_document",
    "apply_z3_violations_to_tree",
    "apply_redhat_critiques_to_tree",
    "attach_substrate_provenance_to_tree",
    "collect_unique_provenance",
    "enrich_document_citations",
    "extract_citations_from_content",
]
