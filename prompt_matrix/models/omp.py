"""OMP (Open Memory Protocol) artifact models for Assure."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


OMPArtifactType = Literal[
    "parse",
    "compile_prompt",
    "evidence",
    "jdf",
    "verification",
    "confidence",
]

OMPRelation = Literal[
    "parent_of",
    "child_of",
    "derived_from",
    "references",
    "verifies",
]


@dataclass
class OMPArtifact:
    """Typed OMP artifact for structured memory/lineage storage."""

    artifact_id: str
    project_id: str
    artifact_type: OMPArtifactType
    source_id: str | None = None
    parent_artifact_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "project_id": self.project_id,
            "artifact_type": self.artifact_type,
            "source_id": self.source_id,
            "parent_artifact_id": self.parent_artifact_id,
            "payload": self.payload,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OMPArtifact:
        return cls(
            artifact_id=data["artifact_id"],
            project_id=data["project_id"],
            artifact_type=data["artifact_type"],
            source_id=data.get("source_id"),
            parent_artifact_id=data.get("parent_artifact_id"),
            payload=data.get("payload", {}),
            confidence=data.get("confidence"),
            provenance=data.get("provenance"),
            created_at=data.get("created_at", datetime.utcnow().isoformat()),
            updated_at=data.get("updated_at", datetime.utcnow().isoformat()),
            metadata=data.get("metadata", {}),
        )


@dataclass
class OMPLinkage:
    """Lineage link between OMP artifacts."""

    parent_artifact_id: str
    child_artifact_id: str
    relation: OMPRelation
    project_id: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_artifact_id": self.parent_artifact_id,
            "child_artifact_id": self.child_artifact_id,
            "relation": self.relation,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass
class OMPStateRecord:
    """State record for an artifact (parse → compile → verify → revision)."""

    artifact_id: str
    state: str
    payload: dict[str, Any] | None = None
    project_id: str | None = None
    recorded_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "state": self.state,
            "payload": self.payload,
            "project_id": self.project_id,
            "recorded_at": self.recorded_at,
        }


@dataclass
class ParseArtifactPayload:
    """Normalized parse artifact payload from substrate ingestion."""

    source_id: str
    source_name: str
    page_count: int
    text: str
    tables: list[dict[str, Any]]
    forms: list[dict[str, Any]]
    file_size_bytes: int
    is_image: bool
    parse_confidence: int | None = None
    ocr_confidence: int | None = None
    spans: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "page_count": self.page_count,
            "text": self.text,
            "tables": self.tables,
            "forms": self.forms,
            "file_size_bytes": self.file_size_bytes,
            "is_image": self.is_image,
            "parse_confidence": self.parse_confidence,
            "ocr_confidence": self.ocr_confidence,
            "spans": self.spans,
        }


@dataclass
class VerifyArtifactPayload:
    """Normalized verification artifact payload from JDF + confidence."""

    jdf: dict[str, Any]
    confidence: dict[str, Any]
    gate_status: str | None = None
    z3_status: str | None = None
    provenance_stats: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "jdf": self.jdf,
            "confidence": self.confidence,
            "gate_status": self.gate_status,
            "z3_status": self.z3_status,
            "provenance_stats": self.provenance_stats,
        }