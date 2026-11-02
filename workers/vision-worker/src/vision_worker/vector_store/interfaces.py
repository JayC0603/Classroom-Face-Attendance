from typing import Protocol

from fr_domain.vector_index import EmbeddingCompatibility, EnrollmentVector, IndexRevision
from fr_domain.vector_index import VectorCandidate as VectorCandidate


class VectorStore(Protocol):
    adapter_kind: str

    def build(self, revision: IndexRevision, vectors: list[EnrollmentVector]) -> IndexRevision: ...
    def activate(self, revision: IndexRevision) -> None: ...
    def load(self, revision: IndexRevision) -> None: ...
    def search(
        self, embedding: tuple[float, ...], compatibility: EmbeddingCompatibility, top_k: int
    ) -> list[VectorCandidate]: ...


class StubVectorStore:
    adapter_kind = "stub"

    def search(
        self, embedding: tuple[float, ...], compatibility: EmbeddingCompatibility, top_k: int
    ) -> list[VectorCandidate]:
        raise RuntimeError("VECTOR_INDEX_UNAVAILABLE")

    def build(self, revision: IndexRevision, vectors: list[EnrollmentVector]) -> IndexRevision:
        raise RuntimeError("VECTOR_INDEX_UNAVAILABLE")

    def activate(self, revision: IndexRevision) -> None:
        raise RuntimeError("VECTOR_INDEX_UNAVAILABLE")

    def load(self, revision: IndexRevision) -> None:
        raise RuntimeError("VECTOR_INDEX_UNAVAILABLE")
