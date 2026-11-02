from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from control_api.face_image_storage import (
    MAX_FILE_BYTES,
    MAX_UPLOAD_FILES,
    ImageValidationError,
)
from control_api.face_registry import (
    FaceImagePublic,
    FaceRegistryService,
    ImageSource,
    PersonCreate,
    PersonPublic,
    StudentIdAlreadyExists,
)
from control_api.face_upload_limits import FaceUploadRoute


def create_face_registry_router(service: FaceRegistryService) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/persons",
        tags=["Face registration"],
        route_class=FaceUploadRoute,
    )

    def require_person(person_id: str) -> PersonPublic:
        try:
            return service.require_person(person_id)
        except KeyError as error:
            raise HTTPException(404, "PERSON_NOT_FOUND") from error

    @router.get("")
    def list_persons(
        search: Annotated[str, Query(max_length=200)] = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        items, total = service.repository.list_persons(search.strip(), limit, offset)
        return {"items": items, "total": total, "persistence": service.repository.persistence}

    @router.post("", status_code=201, response_model=PersonPublic)
    def create_person(request: PersonCreate) -> PersonPublic:
        try:
            return service.create_person(request)
        except StudentIdAlreadyExists as error:
            raise HTTPException(409, "STUDENT_ID_ALREADY_EXISTS") from error

    @router.get("/{person_id}", response_model=PersonPublic)
    def get_person(person_id: str) -> PersonPublic:
        return require_person(person_id)

    @router.get("/{person_id}/face-images")
    def list_images(person_id: str) -> dict[str, list[FaceImagePublic]]:
        require_person(person_id)
        return {"items": [item.public for item in service.repository.list_images(person_id)]}

    @router.post("/{person_id}/face-images")
    def upload_images(
        person_id: str,
        files: Annotated[list[UploadFile], File()],
        source: Annotated[ImageSource, Form()] = "MANUAL_UPLOAD",
    ) -> dict[str, list[dict[str, object]]]:
        require_person(person_id)
        if len(files) > MAX_UPLOAD_FILES:
            raise HTTPException(422, "TOO_MANY_FILES")
        results: list[dict[str, object]] = []
        for file in files:
            # Display filename only; no storage path is derived from it.
            filename = (file.filename or "image").replace("\\", "/").split("/")[-1]
            filename = "".join(ch for ch in filename if ch.isprintable())[:200] or "image"
            try:
                payload = file.file.read(MAX_FILE_BYTES + 1)
                image = service.save_image(person_id, filename, payload, source)
                results.append({"filename": filename, "status": "STORED_PENDING", "image": image})
            except ImageValidationError as error:
                results.append(
                    {
                        "filename": filename,
                        "status": "REJECTED",
                        "error_category": str(error),
                    }
                )
            except Exception:
                # Preserve prior results and avoid disclosing storage/database details.
                results.append(
                    {
                        "filename": filename,
                        "status": "FAILED",
                        "error_category": "IMAGE_SAVE_FAILED",
                    }
                )
            finally:
                file.file.close()
        return {"results": results}

    @router.get("/{person_id}/face-images/{image_id}/content")
    def image_content(person_id: str, image_id: str) -> Response:
        require_person(person_id)
        image = next(
            (
                item
                for item in service.repository.list_images(person_id)
                if item.public.id == image_id
            ),
            None,
        )
        if image is None:
            raise HTTPException(404, "FACE_IMAGE_NOT_FOUND")
        try:
            content = service.storage.read(image.storage_key)
        except (FileNotFoundError, KeyError):
            raise HTTPException(404, "FACE_IMAGE_NOT_FOUND") from None
        return Response(
            content=content,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
