"""Exact cosine Top-K over immutable, checksummed FAISS index revisions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from importlib import import_module
from math import isfinite, sqrt
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID

from fr_domain.vector_index import (
    EmbeddingCompatibility,
    EnrollmentVector,
    IndexRevision,
    IndexStatus,
    VectorCandidate,
)


class VectorIndexError(RuntimeError):
    """Safe categorical index error; never means no matching person."""


@dataclass(frozen=True, slots=True)
class _Snapshot:
    revision: IndexRevision
    index: Any
    mapping: tuple[tuple[str, str], ...]  # position -> (embedding ID, person ID)


def _validate_compatibility(key: EmbeddingCompatibility) -> None:
    if (
        not key.model_definition_id or not key.model_version_id or key.dimension <= 0
        or key.normalization != "L2" or key.metric != "cosine"
        or not key.alignment_profile or not key.preprocessing_profile
    ):
        raise VectorIndexError("INCOMPATIBLE_INDEX")


def _validate_vector(values: tuple[float, ...], dimension: int) -> None:
    if len(values) != dimension or not all(isfinite(value) for value in values):
        raise VectorIndexError("INVALID_EMBEDDING")
    norm = sqrt(sum(value * value for value in values))
    if not isfinite(norm) or abs(norm - 1.0) > 1e-3:
        raise VectorIndexError("INVALID_EMBEDDING_NORMALIZATION")


class FaissVectorStore:
    adapter_kind = "faiss_flat_ip"

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = RLock()
        self._active: _Snapshot | None = None

    @property
    def active_revision(self) -> IndexRevision | None:
        with self._lock:
            return self._active.revision if self._active is not None else None

    def _directory(self, revision_id: str) -> Path:
        try:
            parsed = UUID(revision_id)
        except ValueError as error:
            raise VectorIndexError("INVALID_INDEX_REVISION") from error
        if str(parsed) != revision_id:
            raise VectorIndexError("INVALID_INDEX_REVISION")
        return self._root / revision_id

    def build(self, revision: IndexRevision, vectors: list[EnrollmentVector]) -> IndexRevision:
        if revision.status is not IndexStatus.BUILDING:
            raise VectorIndexError("INVALID_INDEX_STATE")
        _validate_compatibility(revision.compatibility)
        selected = sorted(
            (
                value for value in vectors
                if value.person_active and value.face_image_active and value.embedding_active
            ),
            key=lambda value: value.face_embedding_id,
        )
        if not selected:
            raise VectorIndexError("INDEX_EMPTY")
        identifiers = [value.face_embedding_id for value in selected]
        if len(set(identifiers)) != len(identifiers):
            raise VectorIndexError("DUPLICATE_EMBEDDING_ID")
        for value in selected:
            if value.compatibility != revision.compatibility:
                raise VectorIndexError("INCOMPATIBLE_EMBEDDING_SPACE")
            if not value.face_embedding_id or not value.person_id or not value.face_image_id:
                raise VectorIndexError("INVALID_INDEX_MAPPING")
            _validate_vector(value.vector, revision.compatibility.dimension)
        faiss = import_module("faiss")
        np = import_module("numpy")
        matrix = np.asarray([value.vector for value in selected], dtype=np.float32)
        index = faiss.IndexFlatIP(revision.compatibility.dimension)
        index.add(matrix)
        if index.ntotal != len(selected):
            raise VectorIndexError("INDEX_COUNT_MISMATCH")
        probe_scores, probe_ids = index.search(matrix[:1], 1)
        if int(probe_ids[0, 0]) < 0 or float(probe_scores[0, 0]) < 0.999:
            raise VectorIndexError("INDEX_SMOKE_SEARCH_FAILED")
        index_bytes = bytes(faiss.serialize_index(index))
        mapping = [(value.face_embedding_id, value.person_id) for value in selected]
        mapping_bytes = json.dumps(
            {"revision_id": revision.id, "compatibility": asdict(revision.compatibility),
             "entries": mapping}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        directory = self._directory(revision.id)
        try:
            directory.mkdir(parents=True, exist_ok=False)
            (directory / "index.faiss").write_bytes(index_bytes)
            (directory / "mapping.json").write_bytes(mapping_bytes)
        except FileExistsError as error:
            raise VectorIndexError("INDEX_REVISION_EXISTS") from error
        except OSError as error:
            raise VectorIndexError("INDEX_STORAGE_FAILED") from error
        built = replace(
            revision,
            entry_count=len(mapping),
            index_checksum=sha256(index_bytes).hexdigest(),
            mapping_checksum=sha256(mapping_bytes).hexdigest(),
        )
        self._read_snapshot(built)  # checksum, count, mapping and smoke-search validation
        return built

    def _read_snapshot(self, revision: IndexRevision) -> _Snapshot:
        _validate_compatibility(revision.compatibility)
        if not revision.index_checksum or not revision.mapping_checksum:
            raise VectorIndexError("INDEX_CHECKSUM_MISSING")
        directory = self._directory(revision.id)
        try:
            index_bytes = (directory / "index.faiss").read_bytes()
            mapping_bytes = (directory / "mapping.json").read_bytes()
        except OSError as error:
            raise VectorIndexError("VECTOR_INDEX_UNAVAILABLE") from error
        if (
            sha256(index_bytes).hexdigest() != revision.index_checksum
            or sha256(mapping_bytes).hexdigest() != revision.mapping_checksum
        ):
            raise VectorIndexError("INDEX_CHECKSUM_MISMATCH")
        try:
            payload = json.loads(mapping_bytes)
            entries = payload["entries"]
            mapping = tuple((str(item[0]), str(item[1])) for item in entries)
            key = EmbeddingCompatibility(**payload["compatibility"])
        except (ValueError, TypeError, KeyError, IndexError) as error:
            raise VectorIndexError("INVALID_INDEX_MAPPING") from error
        if (
            payload["revision_id"] != revision.id or key != revision.compatibility
            or len(mapping) != revision.entry_count or not mapping
            or len({item[0] for item in mapping}) != len(mapping)
            or any(not embedding_id or not person_id for embedding_id, person_id in mapping)
        ):
            raise VectorIndexError("INVALID_INDEX_MAPPING")
        faiss = import_module("faiss")
        np = import_module("numpy")
        try:
            index = faiss.deserialize_index(np.frombuffer(index_bytes, dtype=np.uint8).copy())
        except Exception as error:
            raise VectorIndexError("INDEX_LOAD_ERROR") from error
        if index.d != key.dimension or index.ntotal != len(mapping):
            raise VectorIndexError("INDEX_COUNT_MISMATCH")
        probe = np.asarray(index.reconstruct(0), dtype=np.float32).reshape(1, -1)
        scores, positions = index.search(probe, 1)
        if int(positions[0, 0]) != 0 or float(scores[0, 0]) < 0.999:
            raise VectorIndexError("INDEX_SMOKE_SEARCH_FAILED")
        return _Snapshot(revision, index, mapping)

    def activate(self, revision: IndexRevision) -> None:
        candidate = self._read_snapshot(revision)
        with self._lock:
            self._active = candidate

    def load(self, revision: IndexRevision) -> None:
        if revision.status is not IndexStatus.ACTIVE:
            raise VectorIndexError("INVALID_INDEX_STATE")
        self.activate(revision)

    def update_active_metadata(self, revision: IndexRevision) -> None:
        with self._lock:
            snapshot = self._active
            if snapshot is None or snapshot.revision.id != revision.id:
                raise VectorIndexError("VECTOR_INDEX_UNAVAILABLE")
            self._active = replace(snapshot, revision=revision)

    def search(
        self, embedding: tuple[float, ...], compatibility: EmbeddingCompatibility, top_k: int
    ) -> list[VectorCandidate]:
        with self._lock:
            snapshot = self._active
        if snapshot is None:
            raise VectorIndexError("VECTOR_INDEX_UNAVAILABLE")
        if compatibility != snapshot.revision.compatibility:
            raise VectorIndexError("INCOMPATIBLE_INDEX")
        if isinstance(top_k, bool) or not 1 <= top_k <= 100:
            raise VectorIndexError("INVALID_TOP_K")
        _validate_vector(embedding, compatibility.dimension)
        np = import_module("numpy")
        try:
            scores, positions = snapshot.index.search(
                np.asarray([embedding], dtype=np.float32), min(top_k, len(snapshot.mapping))
            )
        except Exception as error:
            raise VectorIndexError("INDEX_SEARCH_FAILED") from error
        hits = sorted(
            ((float(score), snapshot.mapping[int(position)])
             for score, position in zip(scores[0], positions[0], strict=True)
             if int(position) >= 0),
            key=lambda item: (-item[0], item[1][0]),
        )
        return [
            VectorCandidate(rank, mapping[0], mapping[1], score, snapshot.revision.id)
            for rank, (score, mapping) in enumerate(hits, start=1)
        ]

    def close(self) -> None:
        with self._lock:
            self._active = None
