"""JDF (JSON Document Format) AST schemas for Assure document engineering.

The JDF tree is the immutable core: document content lives in nodes;
audit metadata (Red-Hat, Z3) lives in ``annotations`` and is excluded from export.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Callable, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class JDFProvenance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_type: Literal["internal_doc", "academic_paper", "news_article", "web_url"] = (
        "internal_doc"
    )
    source_name: str = ""
    url_or_doi: str = ""
    source_id: str = ""
    page_number: str = ""
    extracted_quote: str = ""
    accessed_date: str = ""
    # The evidence the anchoring coefficient was actually taken against: the
    # joined run of consecutive source sentences that cleared the floors, and its
    # sentence span inside the source ("7-8"). ``extracted_quote`` stays the single
    # sentence the Evidence pane presents; these two record what vouched for it.
    anchor_window: str = ""
    anchor_window_span: str = ""
    # A *cited* row — the compile's reading of the model's ``[S<N>]`` markers —
    # names the numbered source sentence it came from and the page that sentence
    # sits on. Neither was declared, and ``extra="ignore"`` is what an undeclared
    # field gets, so a persisted row read back as quote + filename with no id and
    # no page: the export could not say which sentence of which page a claim
    # rested on, and the Evidence pane showed the page as empty. ``page`` is the
    # cited row's own int page; ``page_number`` stays the matcher's string.
    cited_id: str = ""
    page: int | str | None = None


class JDFRedhatAnnotation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    text: str
    status: Literal["open", "resolved", "dismissed"] = "open"
    # The node this finding is about. Held on the annotation, not only on the node
    # it hangs on, because a finding is read back on its own: the locator, the
    # evidence pane's list and the export all resolve a finding to a paragraph
    # without walking the tree. ``extra="ignore"`` means an unlisted field never
    # survives validation, so this has to be declared to reach SQLite at all.
    node_id: str = ""
    # --- What closed it ------------------------------------------------------
    # Written when a revision rewrote the paragraph this finding was raised on
    # (``resolve_findings_on_rewrite``). A finding that closes leaves no trace
    # otherwise: the paragraph it convicted is replaced by text the audit never
    # saw, and the document then reads as one that never had a finding. The
    # revision that acted on the warning is the record that it existed.
    #
    # ``resolved_by_mutation_type`` names how the warning was answered
    # ("surgical_rewrite"), which is the level of provenance the system has: the
    # human who pressed Apply is the actor, and the app cannot name them and must
    # not pretend to.
    resolved_by_revision_id: str = ""
    resolved_by_version: int | None = None
    resolved_by_mutation_type: str = ""
    resolved_at: str = ""
    # What the finding was raised against, read off the paragraph as it stood
    # when the audit ran (the per-row ``extracted_quote`` and the entailment
    # verdict at ``meta.provenance.entailment``). The rewritten paragraph keeps
    # neither: its citations are not carried over, because a paragraph that
    # inherited its predecessor's quotes would read as anchored to sentences its
    # new text was never matched against. So the history lives here — the
    # paragraph that lost its anchor keeps saying so ("unanchored"), and the
    # finding keeps saying what the anchor was and how it read.
    prior_anchor_quote: str = ""
    prior_verdict: str = ""
    prior_contradicted: bool = False


class JDFZ3Annotation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    message: str
    status: Literal["violation", "pass"] = "violation"
    canonical_key: str = ""


class JDFNodeAnnotations(BaseModel):
    """Metadata attached to nodes — never exported to .docx."""

    model_config = ConfigDict(extra="ignore")

    redhat: list[JDFRedhatAnnotation] = Field(default_factory=list)
    z3: list[JDFZ3Annotation] = Field(default_factory=list)


def empty_annotations() -> dict[str, Any]:
    return JDFNodeAnnotations().model_dump(mode="json")


class JDFParagraphNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["paragraph"] = "paragraph"
    id: str
    content: str
    entities_referenced: list[str] = Field(default_factory=list)
    provenance: list[JDFProvenance] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


class JDFCalloutNode(BaseModel):
    """In-document callouts (warning/insight). Red-Hat critiques use ``annotations.redhat``."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["callout"] = "callout"
    id: str
    variant: Literal["warning", "adversarial_redhat", "insight"]
    title: str
    content: str
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


class JDFTableNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["table"] = "table"
    id: str
    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    bound_entities: list[str] = Field(default_factory=list)
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


class JDFImageNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["image"] = "image"
    id: str
    src: str
    alt: str = ""
    caption: str = ""
    width: int | None = None
    height: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


class JDFSignatureNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["signature"] = "signature"
    id: str
    signer_name: str = ""
    signed_at: str = ""
    content: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


class JDFCheckboxNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["checkbox"] = "checkbox"
    id: str
    label: str = ""
    checked: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


JDFLeafNode = Annotated[
    Union[
        JDFParagraphNode,
        JDFCalloutNode,
        JDFTableNode,
        JDFImageNode,
        JDFSignatureNode,
        JDFCheckboxNode,
    ],
    Field(discriminator="type"),
]

JDFBlockNode = JDFLeafNode


class JDFSectionNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["section"] = "section"
    id: str
    title: str
    children: list[JDFLeafNode] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    annotations: JDFNodeAnnotations = Field(default_factory=JDFNodeAnnotations)


class JDFDocumentTree(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_id: str
    meta: dict[str, Any] = Field(default_factory=dict)
    truth_ledger: dict[str, float | str | int] = Field(default_factory=dict)
    body: list[JDFSectionNode] = Field(default_factory=list)

    # Confidence fields for verification results — float, not int: scores are
    # ratios and an int forces every caller to round before it can store.
    confidence: float | None = None
    confidenceBreakdown: dict[str, float] | None = None
    lowConfidenceNodes: list[dict[str, Any]] | None = None

    # OMP lineage fields for audit trail
    ompArtifactIds: list[str] | None = None
    sourceArtifactIds: list[str] | None = None
    verificationLineage: list[dict[str, Any]] | None = None

    @model_validator(mode="before")
    @classmethod
    def _compat_frontend_envelope(cls, data: Any) -> Any:
        """Accept canvas/TipTap envelopes that send type/title on the document root."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        data.pop("type", None)
        title = data.pop("title", None)
        if title:
            meta = dict(data.get("meta") or {})
            meta.setdefault("title", title)
            data["meta"] = meta
        return data

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
        # Read through the migration too: it rebuilds the row from a fixed key
        # list, so a citation's id and page are dropped here even after they are
        # declared on the model.
        "cited_id": prov.get("cited_id") or "",
        "page": prov.get("page"),
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


def strip_unknown_jdf_keys(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop canvas-only fields so extra=forbid validation can stay strict."""
    doc_keys = ("document_id", "meta", "truth_ledger", "body", "type", "title")
    section_keys = ("type", "id", "title", "children", "meta", "annotations")
    leaf_keys = {
        "paragraph": (
            "type",
            "id",
            "content",
            "entities_referenced",
            "provenance",
            "meta",
            "annotations",
        ),
        "callout": ("type", "id", "variant", "title", "content", "annotations"),
        "table": ("type", "id", "caption", "headers", "rows", "bound_entities", "annotations"),
        "image": ("type", "id", "src", "alt", "caption", "width", "height", "meta", "annotations"),
        "signature": ("type", "id", "signer_name", "signed_at", "content", "meta", "annotations"),
        "checkbox": ("type", "id", "label", "checked", "meta", "annotations"),
    }
    prov_keys = (
        "source_type",
        "source_name",
        "url_or_doi",
        "source_id",
        "page_number",
        "extracted_quote",
        "accessed_date",
        "anchor_window",
        "anchor_window_span",
        "cited_id",
        "page",
    )
    ann_keys = ("redhat", "z3")

    def pick(src: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        return {k: src[k] for k in keys if k in src}

    doc = pick(raw, doc_keys)
    title = str(doc.pop("title", None) or "").strip()
    doc.pop("type", None)
    if title:
        meta = dict(doc.get("meta") or {})
        if not str(meta.get("title") or "").strip():
            meta["title"] = title
        doc["meta"] = meta
    body: list[dict[str, Any]] = []
    for section in raw.get("body") or []:
        if not isinstance(section, dict):
            continue
        sec = pick(section, section_keys)
        children: list[dict[str, Any]] = []
        for child in section.get("children") or []:
            if not isinstance(child, dict):
                continue
            keys = leaf_keys.get(str(child.get("type") or ""), leaf_keys["paragraph"])
            node = pick(child, keys)
            if isinstance(node.get("provenance"), list):
                node["provenance"] = [
                    pick(_migrate_legacy_provenance_entry(item), prov_keys)
                    if isinstance(item, dict)
                    else item
                    for item in node["provenance"]
                ]
            if isinstance(node.get("annotations"), dict):
                node["annotations"] = pick(node["annotations"], ann_keys)
            children.append(node)
        sec["children"] = children
        if isinstance(sec.get("annotations"), dict):
            sec["annotations"] = pick(sec["annotations"], ann_keys)
        body.append(sec)
    doc["body"] = body
    return doc


def parse_document(raw: dict[str, Any] | str) -> JDFDocumentTree:
    """Strict Pydantic validation before any SQLite write."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        raw = strip_unknown_jdf_keys(raw)
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
        {
            "id": new_node_id("crit"),
            "node_id": node_id,
            "text": text.strip(),
            "status": status,
        }
    )
    return splice_node(tree, node_id, node)


def _first_anchor_quote(node: dict[str, Any] | None) -> str:
    """The source sentence ``node`` was anchored to — "" when it had none.

    The same row ``services/audit_summary._anchoring_quote`` reads: the first
    provenance row carrying an ``extracted_quote``.
    """
    for row in (node or {}).get("provenance") or []:
        if isinstance(row, dict) and str(row.get("extracted_quote") or "").strip():
            return str(row["extracted_quote"]).strip()
    return ""


def _entailment_record(node: dict[str, Any] | None) -> dict[str, Any]:
    """``node.meta.provenance.entailment`` — {} when the claim was never checked."""
    meta = (node or {}).get("meta")
    prov = meta.get("provenance") if isinstance(meta, dict) else None
    record = prov.get("entailment") if isinstance(prov, dict) else None
    return record if isinstance(record, dict) else {}


def resolve_findings_on_rewrite(
    rewritten: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    revision_id: str,
    version: int | None,
    mutation_type: str,
    resolved_at: str | None = None,
) -> dict[str, Any]:
    """Carry ``previous``'s findings onto the node this revision rewrote.

    A finding is evidence about the paragraph it was raised on, and remediating
    that paragraph used to destroy the evidence: the rewritten node replaced the
    convicted one wholesale, so the finding, the node's annotations and its
    citations all went with it and the document read as one that never had a
    finding. The one moment the system should record most carefully is when a
    human acts on its own warning.

    So the finding survives the rewrite, ``resolved``, naming the revision that
    closed it and how ("surgical_rewrite") — and carrying the evidence it was
    raised against: the source sentence the paragraph was anchored to and the
    entailment verdict it was given. The rewritten paragraph keeps none of that
    on itself (its quotes are not copied over: text that inherited its
    predecessor's citations would read as anchored to sentences it was never
    matched against), so ``prior_*`` here is the only record of what was wrong,
    and the paragraph's own ``unanchored`` state stays true and visible.

    A finding already closed — dismissed, or resolved by an earlier revision — is
    carried as it stands; a rewrite does not re-close it. Entries on
    ``rewritten`` that are new (a fresh audit folded in after the rewrite) are
    kept after the carried ones.

    ``extra="ignore"`` on the annotation model means every field written here has
    to be declared on ``JDFRedhatAnnotation`` or it is dropped on the way to
    SQLite.
    """
    carried_source = list((previous or {}).get("annotations", {}).get("redhat") or [])
    if not carried_source:
        return copy.deepcopy(rewritten)

    if not resolved_at:
        resolved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    previous_ids = {str(f.get("id") or "") for f in carried_source if isinstance(f, dict)}
    node = copy.deepcopy(rewritten)
    fresh = [
        f
        for f in ((node.get("annotations") or {}).get("redhat") or [])
        if str(f.get("id") or "") not in previous_ids
    ]

    anchor_quote = _first_anchor_quote(previous)
    entailment = _entailment_record(previous)
    carried: list[dict[str, Any]] = []
    for finding in carried_source:
        if not isinstance(finding, dict):
            continue
        item = copy.deepcopy(finding)
        if str(item.get("status") or "open") == "open":
            item["status"] = "resolved"
            item["resolved_by_revision_id"] = revision_id
            item["resolved_by_version"] = version
            item["resolved_by_mutation_type"] = mutation_type
            item["resolved_at"] = resolved_at
            item["prior_anchor_quote"] = anchor_quote
            item["prior_verdict"] = str(entailment.get("verdict") or "")
            item["prior_contradicted"] = bool(entailment.get("contradicted"))
        carried.append(item)

    node = _ensure_annotations(node)
    node["annotations"]["redhat"] = carried + fresh
    return node


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


def _strip_inline_markdown(text: str) -> str:
    """Remove inline markdown artifacts so JDF node strings are plain text
    (provenance exact-matching sees clean source copy)."""
    t = str(text or "")
    t = t.replace("**", "").replace("__", "")  # bold
    t = t.replace("*", " ").replace("_", " ")  # italics / emphasis
    t = t.replace("`", "")  # code spans / backticks
    t = re.sub(r"^#{1,6}\s*", "", t)  # leading '#' remnants
    return re.sub(r"\s{2,}", " ", t).strip()


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
            current_title = _strip_inline_markdown(heading.group(2))
            continue
        current_children.append(
            {
                "type": "paragraph",
                "id": new_node_id("para"),
                "content": _strip_inline_markdown(block),
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
                        "content": _strip_inline_markdown(stripped),
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
    """Attach Red-Hat text to ``annotations.redhat`` on target node(s).

    An entry with ``status == "error"`` is not a finding — it is a refusal or a
    model failure — and is never attached: a paragraph carrying one would show a
    review that no review produced.
    """
    if not critiques:
        return tree
    texts = [
        str(crit.get("content") or crit.get("text") or "").strip()
        for crit in critiques
        if str(crit.get("status") or "") != "error"
    ]
    texts = [text for text in texts if text]
    if not texts:
        return tree
    mutated = copy.deepcopy(tree)
    node_id = target_node_id
    if not node_id:
        nodes = flatten_nodes(mutated)
        node_id = str(nodes[0]["id"]) if nodes else None
    if not node_id:
        return mutated
    for text in texts:
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


def _tokenize(text):
    import re

    raw = str(text or "")

    def _money_tok(m):
        v = m.group(1).replace(",", "")
        try:
            f = float(v)
        except ValueError:
            return " money" + v + " "
        return " money%d " % int(f) if f == int(f) else " money%s " % f

    raw = re.sub(r"\$\s*(\d[\d,]*(?:\.\d+)?)", _money_tok, raw)
    raw = re.sub(
        r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(?:%|percent\b|per\s+cent\b)",
        lambda m: " pct" + m.group(1).replace(",", "").rstrip(".") + " ",
        raw,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[^\w\s]", " ", raw.lower())
    STOP = {
        "a",
        "an",
        "the",
        "of",
        "and",
        "or",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "with",
        "as",
        "at",
        "this",
        "that",
        "it",
        "its",
        "from",
        "but",
        "shall",
        "will",
        "may",
        "any",
        "all",
    }
    return {t for t in text.split() if len(t) > 2 and t not in STOP}


def _split_sentences(text):
    import re

    text = str(text or "")
    pieces = re.split(r"(--- Page \d+ ---)", text)
    out = []
    current_page = None
    for piece in pieces:
        m = re.match(r"--- Page (\d+) ---", piece.strip())
        if m:
            current_page = int(m.group(1))
            continue
        for sent in re.split(r"[.!?\n]+", piece):
            s = sent.strip()
            if not s:
                continue
            out.append((s, current_page))
    return out


# Anchor rule, shared with the gate counter in services/audit_summary.py:
#   * a paragraph is claim-eligible at >= _MIN_CLAIM_TOKENS content tokens,
#   * a source sentence is eligible at >= _MIN_ANCHOR_OVERLAP content tokens, and
#   * a source sentence anchors the paragraph when they share >= _MIN_ANCHOR_OVERLAP
#     content tokens AND those shared tokens cover >= _MIN_ANCHOR_COEFFICIENT of the
#     shorter of the two texts.
# _MIN_ANCHOR_OVERLAP is the minimum evidence mass for an anchor, so it also bounds
# the shortest text on either side that can anchor: a paragraph below it can never
# reach the overlap, and a source sentence below it can never supply it. Both floors
# used to be a hardcoded 8 while the overlap floor was 6, which made the overlap
# constant dead code: no source sentence shorter than 8 content tokens was even
# considered, and no paragraph shorter than 8 was either. A policy whose sentences
# are 7 and 6 content tokens ("The policy liability limit is set at $5,000,000 for
# combined single limit.") therefore reported every compile ungrounded, including a
# draft that quoted it verbatim — which is exactly how a grounded demo document got
# stamped "none of its claims match the uploaded sources".
_MIN_CLAIM_TOKENS = 4
_MIN_ANCHOR_OVERLAP = 4
_MIN_ANCHOR_COEFFICIENT = 0.60

# How many consecutive source sentences one anchor may span. A claim is routinely
# carried by two or three neighbouring sentences ("…deductible is 2 percent" then
# "…applies in Suffolk, Norfolk and Essex"), and each sentence on its own covers too
# little of the paragraph to reach _MIN_ANCHOR_COEFFICIENT — the paragraph then read
# as ungrounded even though the source states it. Widening the *candidate* does not
# move either floor: the window is scored by the same coefficient, against the union
# of its sentences' tokens.
_MAX_ANCHOR_WINDOW = 3

_NUM_RE = re.compile(r"\d+(?:,\d+)*(?:\.\d+)?")

# A numeral is a *reference*, not a quantity, in three shapes. The claim guard
# below compares quantities only, so reading a cross-reference or the draft's own
# numbering as a figure refuses every candidate sentence: a paragraph numbered
# "1. Wind/hail deductible … 2 percent" carried {1, 2} against a source sentence
# that carries only the 2, and so matched nothing at all.
#   * introduced by a reference keyword — "Section 3", "Sections 5 and 6",
#     "Page 4", "No. 7", "§ 2";
#   * inside a hyphenated identifier — "DEMO-MA-RE-2026-001", "XXXX-NN-NN-NNNN";
#   * a bare line-leading list marker — "1. " / "2) " opening a line.
# Everything else is a quantity: money, percentages, dates, durations, counts.
_REF_KEYWORD_RE = re.compile(
    r"(?:\b(?:Sections?|Articles?|Clauses?|Exhibits?|Appendi(?:x|ces)|"
    r"Schedules?|Paragraphs?|Pages?|Nos?)\.?|§)",
    re.IGNORECASE,
)
# What may stand between that keyword and the numeral it introduces: the bare
# form ("Section 3") and the list form ("Sections 5 and 6"), where only blanks,
# joining words and the other numerals of the list separate the two.
_REF_LIST_ITEM_RE = re.compile(
    r"\d+(?:,\d+)*(?:\.\d+)?|and|or|to|through|thru|&|,|;|–|—|-", re.IGNORECASE
)
_BLANK_RE = re.compile(r"[ \t]*")
_HYPHENATED_ID_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}")
_LINE_LIST_MARKER_RE = re.compile(r"^[ \t]*\d+[.)][ \t\n]", re.MULTILINE)


def _numbers(text: str) -> set[str]:
    """Quantities in text, normalized so $5,000,000 == 5000000.0 == 5,000,000.

    Reference markers and identifiers are dropped (see ``_REF_KEYWORD_RE``):
    the guard in ``attach_substrate_provenance_to_tree`` requires every claim
    quantity to appear in the candidate source sentence, and a paragraph's own
    numbering and cross-references appear in no source sentence at all.
    """
    raw = str(text or "")
    identifiers = [(m.start(), m.end()) for m in _HYPHENATED_ID_RE.finditer(raw)]
    out: set[str] = set()
    for match in _NUM_RE.finditer(raw):
        start, end = match.start(), match.end()
        if any(a <= start and end <= b for a, b in identifiers):
            continue
        line_start = raw.rfind("\n", 0, start) + 1
        if (
            not raw[line_start:start].strip()
            and end < len(raw)
            and raw[end] in ".)"
            and _LINE_LIST_MARKER_RE.match(raw, line_start)
        ):
            continue
        keyword = None
        for found in _REF_KEYWORD_RE.finditer(raw, 0, start):
            keyword = found
        if keyword is not None:
            pos = keyword.end()
            is_reference = True
            while pos < start:
                pos = _BLANK_RE.match(raw, pos).end()
                if pos >= start:
                    break
                item = _REF_LIST_ITEM_RE.match(raw, pos)
                if item is None:
                    is_reference = False
                    break
                pos = item.end()
            if is_reference:
                continue
        try:
            value = float(match.group(0).replace(",", ""))
        except ValueError:
            continue
        out.add(f"{value:.6f}".rstrip("0").rstrip("."))
    return out


def _merge_short_sentences(sentences):
    """Fold a sentence below the overlap floor into the sentence that follows it.

    `_split_sentences` splits on newlines as well as on sentence punctuation, so
    a *line* of the document is a sentence to the rest of this module — including
    the lines that are not prose: a heading, a `Jurisdiction: Massachusetts`-style
    label, a reference-list fragment, a run of citation numbers. Judged on its
    own, each falls below `_MIN_ANCHOR_OVERLAP` and the eligible filter discarded
    it outright. Two things then went wrong at once: a claim whose only evidence
    was such a line could never anchor to it, and the line could never contribute
    its tokens to a window either, so a heading that a paragraph plainly quotes
    was invisible to the match.

    Merging keeps the text instead of dropping it. A short sentence joins the
    next one, and the pair is judged on the union of their tokens; if that still
    falls short, the next sentence joins too. A sentence already at or above the
    floor is emitted on its own, exactly as before — so this changes nothing for
    prose, and rescues the lines that had been thrown away.

    The merge is forward-only and a trailing short run has nothing to join: it is
    kept whole rather than discarded, and the floor still judges it.
    """
    out = []
    pending = []
    for sent, page in sentences:
        text = str(sent or "").strip()
        if not text:
            continue
        pending.append((text, page))
        joined = " ".join(part for part, _page in pending)
        if len(_tokenize(joined)) < _MIN_ANCHOR_OVERLAP:
            continue
        out.append((joined, _first_page(pending)))
        pending = []
    if pending:
        out.append((" ".join(part for part, _page in pending), _first_page(pending)))
    return out


def _first_page(pending):
    """The first real page number in a merged run, so a window keeps a citable page."""
    for _text, page in pending:
        if page is not None:
            return page
    return None


def attach_substrate_provenance_to_tree(
    tree: dict[str, Any],
    locks: list[dict[str, Any]],
    substrate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    # Lexical overlap anchors provenance by wording similarity, not claim
    # truthfulness — so this is the *candidate* anchor, not a verdict. Numbers are
    # checked (a paragraph may not cite a figure its matched sentence does not
    # carry), but a paragraph that drops or flips a negation can still anchor to
    # the sentence it contradicts. Truthfulness is decided one layer up, by
    # services/entailment.py (TaskType.SEMANTIC_VALIDATION), whose verdict the gate
    # in services/audit_summary.py reads.
    """Best-effort provenance: for each paragraph, find the substrate
    sentence with the highest token-overlap coefficient. Stamp the
    real filename, source_id, page, and quote onto node["provenance"].
    """
    if not substrate_rows:
        return tree
    mutated = document_to_dict(tree)

    # Precompute source windows that carry enough content tokens to anchor. The
    # candidate is a sliding window of up to _MAX_ANCHOR_WINDOW consecutive
    # sentences from one source, scored against the union of their tokens; the
    # one-sentence window stays in the set, so this is a superset of the previous
    # sentence-at-a-time matcher rather than a replacement for it.
    source_sentences = []
    for row in substrate_rows:
        text = str(row.get("extracted_text") or "")
        eligible = []
        # Every sentence in order with its own figures, so a window's figure set
        # can include short neighbours that carry a figure but too few content
        # tokens to anchor on their own. A real policy's declarations line splits
        # into fragments like "$5,000,000 Part of $25,000,000 per Occurrence", and
        # dropping the short ones made their figures unmatchable by ANY claim —
        # which is how every real policy anchored zero paragraphs.
        all_sentences: list[tuple[set, Any]] = []
        for sent, sent_page in _merge_short_sentences(_split_sentences(text)):
            toks = _tokenize(sent)
            idx = len(all_sentences)
            all_sentences.append((_numbers(sent), sent_page))
            if len(toks) >= _MIN_ANCHOR_OVERLAP:
                eligible.append((sent, toks, sent_page, _numbers(sent), idx))
        for start in range(len(eligible)):
            window_toks: set = set()
            window_numbers: set = set()
            for end in range(start, min(start + _MAX_ANCHOR_WINDOW, len(eligible))):
                _sent, sent_toks, _page, sent_numbers, _idx = eligible[end]
                window_toks = window_toks | sent_toks
                window_numbers = window_numbers | sent_numbers
                # The window's figures are those of every sentence it SPANS, not
                # only the eligible ones — a figure the window carries is
                # vouched for however short the sentence that states it.
                for k in range(eligible[start][4], eligible[end][4] + 1):
                    window_numbers = window_numbers | all_sentences[k][0]
                source_sentences.append(
                    (
                        row,
                        eligible[start : end + 1],
                        window_toks,
                        eligible[start][2],
                        window_numbers,
                        f"{start}-{end}",
                    )
                )

    if not source_sentences:
        return mutated

    # Materialize — splicing during generator iteration skips siblings.
    for node in list(flatten_nodes(mutated)):
        if node.get("type") != "paragraph":
            continue
        content = str(node.get("content") or "").strip()
        if not content:
            continue
        content_toks = _tokenize(content)
        if len(content_toks) < _MIN_CLAIM_TOKENS:
            continue
        claim_numbers = _numbers(content)

        best_score = 0.0
        best_row = None
        best_window = ""
        best_page = None
        best_window_sentences = []
        best_span = ""
        for row, window, window_toks, window_page, window_numbers, span in source_sentences:
            # A figure the source window does not carry cannot be vouched for by
            # that window. Without this, lexical overlap anchors a fabricated
            # "$250,000" to a source "$5,000,000" — both tokenize to "000".
            if claim_numbers - window_numbers:
                continue
            inter = len(content_toks & window_toks)
            if inter < _MIN_ANCHOR_OVERLAP:
                continue
            score = inter / min(len(content_toks), len(window_toks))
            # A tie goes to the narrowest window: the anchor stays as tight as the
            # evidence allows, so a paragraph one sentence already covers keeps that
            # sentence as its window and does not carry its neighbours into the
            # entailment prompt. Widening only ever happens on a strictly better score.
            if score > best_score or (score == best_score and len(window) < len(best_window_sentences)):
                best_score = score
                best_row = row
                best_window = " ".join(entry[0] for entry in window)
                best_page = window_page
                best_window_sentences = window
                best_span = span

        if best_score < _MIN_ANCHOR_COEFFICIENT or best_row is None:
            continue

        # The cited sentence is the best-matching sentence *inside* the anchor
        # window, so the quote and the window cannot disagree about the evidence.
        # The numeric check belongs to the window — it is what the anchor rests on —
        # and is deliberately not re-applied here: a claim that cites figures from
        # two sentences has no single sentence inside the window that carries them
        # all, and quoting the whole window instead would put two or three sentences
        # in a field the Evidence pane presents as one.
        best_sent = ""
        best_sent_score = 0.0
        for sent, sent_toks, _sent_page, _sent_numbers, _sent_idx in best_window_sentences:
            inter = len(content_toks & sent_toks)
            if inter < _MIN_ANCHOR_OVERLAP:
                continue
            score = inter / min(len(content_toks), len(sent_toks))
            if score > best_sent_score:
                best_sent_score = score
                best_sent = sent
        if not best_sent:
            best_sent = best_window

        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        node_copy = copy.deepcopy(node)
        existing = node_copy.get("provenance") or []
        source_id = str(best_row.get("id") or "")
        if any(isinstance(p, dict) and p.get("source_id") == source_id for p in existing):
            continue

        page_val = best_page if best_page is not None else best_row.get("page_count")
        try:
            page_str = str(int(page_val)) if page_val else ""
        except (TypeError, ValueError):
            page_str = ""

        existing.append(
            {
                "source_type": "internal_doc",
                "source_name": best_row.get("filename") or "",
                "url_or_doi": "",
                "source_id": source_id,
                "page_number": page_str,
                "extracted_quote": best_sent[:280].strip(),
                "accessed_date": "",
                # The evidence the coefficient was actually taken against: the joined
                # window, whole and never truncated, plus its sentence span inside the
                # source, so the anchor can be audited against what cleared the floor.
                "anchor_window": best_window.strip(),
                "anchor_window_span": best_span,
            }
        )
        node_copy["provenance"] = existing
        mutated, _ = splice_node(mutated, node_id, node_copy)

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
    "resolve_findings_on_rewrite",
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
