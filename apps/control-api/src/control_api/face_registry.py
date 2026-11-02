"""Person and uploaded image intake; no biometric acceptance is implied."""

import os
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from threading import RLock
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from control_api.face_image_storage import FaceImageStorage
from control_api.object_storage import ObjectStorage
from control_api.storage_bootstrap import build_configured_object_storage


class StudentIdAlreadyExists(ValueError):
    pass


class PersonCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    full_name: str = Field(min_length=1, max_length=200)
    student_id: str = Field(min_length=1, max_length=100)


class PersonPublic(BaseModel):
    id: str
    person_number: int | None = None
    full_name: str
    student_id: str | None
    status: Literal["ACTIVE", "DISABLED"] = "ACTIVE"
    image_count: int = 0
    recognition_ready: bool = False
    created_at: datetime
    updated_at: datetime


ImageSource = Literal["MANUAL_UPLOAD", "CAMERA_CAPTURE"]


class FaceImagePublic(BaseModel):
    id: str
    person_id: str
    original_filename: str
    mime_type: Literal["image/jpeg"] = "image/jpeg"
    width: int
    height: int
    processing_state: Literal["PENDING_PROCESSING"] = "PENDING_PROCESSING"
    review_state: Literal["PENDING_REVIEW", "APPROVED", "REJECTED"] = "PENDING_REVIEW"
    source: ImageSource = "MANUAL_UPLOAD"
    created_at: datetime
    content_url: str


class StoredFaceImage(BaseModel):
    public: FaceImagePublic
    storage_key: str
    checksum: str


class FaceRegistryRepository(Protocol):
    persistence: str

    def create_person(self, value: PersonPublic) -> PersonPublic: ...
    def list_persons(
        self,
        search: str,
        limit: int,
        offset: int,
    ) -> tuple[list[PersonPublic], int]: ...
    def get_person(self, person_id: str) -> PersonPublic | None: ...
    def add_image(self, value: StoredFaceImage) -> None: ...
    def list_images(self, person_id: str) -> list[StoredFaceImage]: ...
    def list_all_images(self) -> list[StoredFaceImage]: ...
    def get_image(self, image_id: str) -> StoredFaceImage | None: ...
    def review_image(
        self,
        image_id: str,
        reviewer_id: str,
        decision: str,
        reviewed_at: datetime,
        teacher_id: str | None = None,
    ) -> FaceImagePublic | None: ...
    def review_audit(self) -> list[dict[str, object]]: ...
    def replace_image_storage_key(self, image_id: str, storage_key: str) -> None: ...


class InMemoryFaceRegistryRepository:
    persistence = "in_memory"

    def __init__(self) -> None:
        self.persons: dict[str, PersonPublic] = {}
        self.images: dict[str, StoredFaceImage] = {}
        self._review_log: list[dict[str, object]] = []
        self._next_person_number = 1
        self._lock = RLock()

    def create_person(self, value: PersonPublic) -> PersonPublic:
        with self._lock:
            if any(
                (p.student_id or "").lower() == (value.student_id or "").lower()
                for p in self.persons.values()
            ):
                raise StudentIdAlreadyExists
            person = value
            if person.person_number is None:
                person = person.model_copy(update={"person_number": self._next_person_number})
            assert person.person_number is not None
            self._next_person_number = max(self._next_person_number, person.person_number + 1)
            self.persons[person.id] = person
            return person

    def get_person(self, person_id: str) -> PersonPublic | None:
        with self._lock:
            person = self.persons.get(person_id)
            if person is None:
                return None
            count = sum(image.public.person_id == person_id for image in self.images.values())
            return person.model_copy(update={"image_count": count})

    def list_persons(self, search: str, limit: int, offset: int) -> tuple[list[PersonPublic], int]:
        with self._lock:
            matches = [
                person
                for key in self.persons
                if (person := self.get_person(key)) is not None
                and (
                    search.lower() in person.full_name.lower()
                    or search.lower() in (person.student_id or "").lower()
                )
            ]
            matches.sort(key=lambda p: p.person_number or 0)
            return matches[offset : offset + limit], len(matches)

    def add_image(self, value: StoredFaceImage) -> None:
        with self._lock:
            if value.public.person_id not in self.persons:
                raise KeyError("PERSON_NOT_FOUND")
            self.images[value.public.id] = value
            person = self.persons[value.public.person_id]
            self.persons[person.id] = person.model_copy(update={"updated_at": datetime.now(UTC)})

    def list_images(self, person_id: str) -> list[StoredFaceImage]:
        with self._lock:
            return sorted(
                (image for image in self.images.values() if image.public.person_id == person_id),
                key=lambda image: (image.public.created_at, image.public.id),
            )

    def list_all_images(self) -> list[StoredFaceImage]:
        with self._lock:
            return sorted(
                self.images.values(), key=lambda image: (image.public.created_at, image.public.id)
            )

    def get_image(self, image_id: str) -> StoredFaceImage | None:
        with self._lock:
            return self.images.get(image_id)

    def replace_image_storage_key(self, image_id: str, storage_key: str) -> None:
        with self._lock:
            image = self.images[image_id]
            self.images[image_id] = image.model_copy(update={"storage_key": storage_key})

    def review_image(
        self,
        image_id: str,
        reviewer_id: str,
        decision: str,
        reviewed_at: datetime,
        teacher_id: str | None = None,
    ) -> FaceImagePublic | None:
        with self._lock:
            image = self.images.get(image_id)
            if image is None or image.public.review_state != "PENDING_REVIEW":
                return None
            state = "APPROVED" if decision == "APPROVE" else "REJECTED"
            updated = image.model_copy(
                update={"public": image.public.model_copy(update={"review_state": state})}
            )
            self.images[image_id] = updated
            self._review_log.append(
                {
                    "reviewer_id": reviewer_id,
                    "person_id": image.public.person_id,
                    "image_id": image_id,
                    "decision": state,
                    "reviewed_at": reviewed_at,
                }
            )
            return updated.public

    def review_audit(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(item) for item in self._review_log]


class FaceRegistryService:
    def __init__(self, repository: FaceRegistryRepository, storage: FaceImageStorage) -> None:
        self.repository = repository
        self.storage = storage

    def require_person(self, person_id: str) -> PersonPublic:
        person = self.repository.get_person(person_id)
        if person is None:
            raise KeyError("PERSON_NOT_FOUND")
        return person

    def create_person(self, request: PersonCreate) -> PersonPublic:
        now = datetime.now(UTC)
        return self.repository.create_person(
            PersonPublic(
                id=str(uuid4()),
                full_name=request.full_name,
                student_id=request.student_id,
                created_at=now,
                updated_at=now,
            )
        )

    def save_image(
        self,
        person_id: str,
        filename: str,
        payload: bytes,
        source: ImageSource = "MANUAL_UPLOAD",
    ) -> FaceImagePublic:
        self.require_person(person_id)
        key, width, height, checksum = self.storage.save(payload)
        image_id = str(uuid4())
        name = filename.replace("\\", "/").split("/")[-1]
        safe_name = "".join(ch for ch in name if ch.isprintable())[:200] or "image"
        public = FaceImagePublic(
            id=image_id,
            person_id=person_id,
            original_filename=safe_name,
            width=width,
            height=height,
            source=source,
            created_at=datetime.now(UTC),
            content_url=f"/api/v1/persons/{person_id}/face-images/{image_id}/content",
        )
        try:
            self.repository.add_image(
                StoredFaceImage(
                    public=public,
                    storage_key=key,
                    checksum=checksum,
                )
            )
        except Exception:
            self.storage.remove(key)
            raise
        return public

    def review_image(
        self, image_id: str, reviewer_id: str, decision: str, *, teacher_id: str | None = None
    ) -> FaceImagePublic:
        if decision not in {"APPROVE", "REJECT"}:
            raise ValueError("INVALID_REVIEW_DECISION")
        reviewed = self.repository.review_image(
            image_id, reviewer_id, decision, datetime.now(UTC), teacher_id
        )
        if reviewed is None:
            raise KeyError("FACE_IMAGE_NOT_PENDING")
        return reviewed

    def require_image(self, image_id: str) -> StoredFaceImage:
        image = self.repository.get_image(image_id)
        if image is None:
            raise KeyError("FACE_IMAGE_NOT_FOUND")
        return image

    def review_audit(self) -> list[dict[str, object]]:
        return self.repository.review_audit()


def migrate_legacy_face_images(
    repository: FaceRegistryRepository,
    storage: ObjectStorage,
    root: Path,
) -> int:
    """Copy legacy local JPEGs to MinIO after checksum verification, retaining sources."""
    migrated = 0
    for image in repository.list_all_images():
        key = image.storage_key
        if re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{32}\.jpg", key):
            continue
        is_path = Path(key).is_absolute() or PureWindowsPath(key).is_absolute()
        if not is_path and not re.fullmatch(r"[0-9a-f]{32}\.jpg", key):
            continue
        source = Path(key) if is_path else root / key
        try:
            payload = source.read_bytes()
        except OSError as error:
            raise RuntimeError("LEGACY_FACE_IMAGE_UNAVAILABLE") from error
        if sha256(payload).hexdigest() != image.checksum:
            raise RuntimeError("LEGACY_FACE_IMAGE_CHECKSUM_MISMATCH")
        destination_key = f"{image.checksum[:2]}/{image.public.id.replace('-', '')}.jpg"
        storage.put(destination_key, payload, "image/jpeg")
        try:
            repository.replace_image_storage_key(image.public.id, destination_key)
        except Exception:
            try:
                storage.delete(destination_key)
            except Exception:
                pass
            raise
        migrated += 1
    return migrated


def build_face_registry(object_storage: ObjectStorage | None = None) -> FaceRegistryService:
    repository: FaceRegistryRepository
    dsn = os.getenv("FR_POSTGRES_DSN")
    if dsn:
        from control_api.face_registry_postgres import PostgresFaceRegistryRepository

        repository = PostgresFaceRegistryRepository(dsn)
    else:
        repository = InMemoryFaceRegistryRepository()
    configured_storage = object_storage or build_configured_object_storage(
        "MINIO_FACE_IMAGES_BUCKET", "face-images"
    )
    image_root = Path(os.getenv("FR_FACE_IMAGE_ROOT", "data/face-images"))
    if configured_storage is not None:
        migrate_legacy_face_images(repository, configured_storage, image_root)
    return FaceRegistryService(
        repository,
        FaceImageStorage(
            None if configured_storage is not None else image_root,
            object_storage=configured_storage,
        ),
    )
