"""Operational index endpoints; deliberately no raw-vector search endpoint."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fr_domain.vector_index import EmbeddingCompatibility
from pydantic import BaseModel, ConfigDict, Field
from vision_worker.vector_store.faiss_store import VectorIndexError

from control_api.vector_index import VectorIndexService


class RebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_definition_id: str = Field(min_length=1)
    model_version_id: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    normalization: str = "L2"
    metric: str = "cosine"
    alignment_profile: str = "arcface_5point_112"
    preprocessing_profile: str = "adaface_v1"


def create_vector_index_router(service: VectorIndexService | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/vector-index")

    def require_service() -> VectorIndexService:
        if service is None:
            raise HTTPException(503, detail="POSTGRESQL_REQUIRED")
        return service

    @router.get("/status")
    def status() -> dict[str, object]:
        if service is None:
            return {"state": "NOT_READY", "active_revision": None,
                    "error_category": "POSTGRESQL_REQUIRED"}
        return service.status()

    @router.get("/revisions")
    def revisions() -> dict[str, object]:
        return {"items": [asdict(item) for item in require_service().revisions()]}

    @router.post("/rebuild", status_code=201)
    def rebuild(request: RebuildRequest) -> dict[str, object]:
        try:
            key = EmbeddingCompatibility(**request.model_dump())
            return asdict(require_service().rebuild(key))
        except (VectorIndexError, ImportError) as error:
            category = "FAISS_UNAVAILABLE" if isinstance(error, ImportError) else str(error)
            raise HTTPException(409, detail=category) from error

    @router.post("/revisions/{revision_id}/activate")
    def activate(revision_id: str) -> dict[str, object]:
        try:
            return asdict(require_service().activate(revision_id))
        except KeyError as error:
            raise HTTPException(404, detail="INDEX_REVISION_NOT_FOUND") from error
        except (VectorIndexError, ImportError) as error:
            category = "FAISS_UNAVAILABLE" if isinstance(error, ImportError) else str(error)
            raise HTTPException(409, detail=category) from error

    return router
