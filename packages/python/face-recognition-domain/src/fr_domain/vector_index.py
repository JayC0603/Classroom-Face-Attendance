"""Durable enrollment and immutable vector-index contracts, independent of FAISS."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IndexStatus(StrEnum):
    BUILDING = "BUILDING"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class EmbeddingCompatibility:
    model_definition_id: str
    model_version_id: str
    dimension: int
    normalization: str = "L2"
    metric: str = "cosine"
    alignment_profile: str = "arcface_5point_112"
    preprocessing_profile: str = "adaface_v1"


@dataclass(frozen=True, slots=True)
class EnrollmentVector:
    face_embedding_id: str
    face_image_id: str
    person_id: str
    vector: tuple[float, ...]
    compatibility: EmbeddingCompatibility
    person_active: bool = True
    face_image_active: bool = True
    embedding_active: bool = True


@dataclass(frozen=True, slots=True)
class PersonRecord:
    id: str
    full_name: str
    student_id: str | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class FaceImageRecord:
    id: str
    person_id: str
    evidence_ref: str | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class FaceEmbeddingRecord:
    id: str
    face_image_id: str
    vector: tuple[float, ...]
    compatibility: EmbeddingCompatibility
    active: bool = True


@dataclass(frozen=True, slots=True)
class VectorCandidate:
    rank: int
    face_embedding_id: str
    person_id: str
    score: float  # cosine similarity; higher is better
    index_revision_id: str


@dataclass(frozen=True, slots=True)
class IndexRevision:
    id: str
    status: IndexStatus
    compatibility: EmbeddingCompatibility
    entry_count: int
    index_checksum: str | None
    mapping_checksum: str | None
    created_at: str
    activated_at: str | None = None
    superseded_at: str | None = None
    error_category: str | None = None
