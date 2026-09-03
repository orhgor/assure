"""JDF (JSON Document Format) AST schemas for Assure document engineering.

Node shapes align with uurtech/jdf conventions for surgical mutation.
"""

from __future__ import annotations

import copy
import json
import uuid
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class JDFProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str = ""
    page_or_timestamp: str = ""
    exact_quote: str = ""


class JDFParagraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["paragraph"] = "paragraph"
    id: str
    content: str
    entities_referenced: list[str] = Field(default_factory=list)
    provenance: JDFProvenance | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class JDFCalloutNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["callout"] = "callout"
    id: str
    variant: Literal["warning", "adversarial_redhat", "insight"]
    title: str
    content: str


class JDFTableNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["table"] = "table"
    id: str
    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    bound_entities: list[str] = Field(default_factory=list)


JDFBlockNode = Annotated[
    Union[JDFParagraphNode, JDFCalloutNode, JDFTableNode],
    Field(discriminator="type"),
]


class JDFSectionNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["section"] = "section"
    id: str
    title: str
    children: list[JDFParagraphNode | JDFCalloutNode | JDFTableNode] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


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


def flatten_nodes(tree: JDFDocumentTree | dict[str, Any]) -> list[dict[str, Any]]:
    """Return block-level nodes in document order (sections flattened)."""
    doc = document_to_dict(tree)
    ordered: list[dict[str, Any]] = []
    for section in doc.get("body") or []:
        for child in section.get("children") or []:
            if isinstance(child, dict) and child.get("id"):
                ordered.append(child)
    return ordered


def find_node_index(tree: JDFDocumentTree | dict[str, Any], target_id: str) -> tuple[int, dict[str, Any]] | None:
    for idx, node in enumerate(flatten_nodes(tree)):
        if node.get("id") == target_id:
            return idx, node
    return None


def splice_node(tree: dict[str, Any], target_id: str, new_node: dict[str, Any]) -> tuple[dict[str, Any], bool]:
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


def parse_document(raw: dict[str, Any] | str) -> JDFDocumentTree:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return JDFDocumentTree.model_validate(raw)


def node_text(node: dict[str, Any]) -> str:
    ntype = node.get("type")
    if ntype == "paragraph":
        return str(node.get("content") or "")
    if ntype == "callout":
        return f"{node.get('title') or ''}\n{node.get('content') or ''}".strip()
    if ntype == "table":
        return str(node.get("caption") or "Table")
    return str(node.get("content") or "")


def get_node_by_id(tree: JDFDocumentTree | dict[str, Any], node_id: str) -> dict[str, Any] | None:
    doc = document_to_dict(tree)
    for section in doc.get("body") or []:
        if isinstance(section, dict) and section.get("id") == node_id:
            return section
        for child in section.get("children") or []:
            if isinstance(child, dict) and child.get("id") == node_id:
                return child
    return None


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
