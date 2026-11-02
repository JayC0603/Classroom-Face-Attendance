from hashlib import sha256
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fr_domain.model_management import ModelManifest, ModelType, ModelVersion
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from control_api.face_upload_limits import FaceUploadRoute
from control_api.model_registry import ModelRegistryService
from control_api.person_models import inspect_yolox_manifest


class PersonModelUploadRoute(FaceUploadRoute):
    max_files = 1
    max_fields = 4

    @staticmethod
    def matches_upload(method: str, path: str) -> bool:
        return method == "POST" and path.rstrip("/") == "/api/v1/person-models"


class PersonModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=80)
    confidence: float | None = Field(default=None, ge=0.01, le=1, allow_inf_nan=False)


def _same_runtime_profile(existing: ModelVersion, requested: ModelManifest) -> bool:
    existing_manifest = existing.manifest.model_dump(mode="json")
    requested_manifest = requested.model_dump(mode="json")
    old_metadata = existing_manifest.get("metadata", {})
    new_metadata = requested_manifest.get("metadata", {})
    if old_metadata.get("output_semantics") == new_metadata.get("output_semantics") == "yolox_xyxy":
        # Earlier imports stored the six-column XYXY contract as class_count=1;
        # current inspection stores its class-id range separately. The runtime
        # reads XYXY class IDs directly and uses one person class in either form.
        old_metadata.pop("class_count", None)
        old_metadata.pop("class_id_limit", None)
        new_metadata.pop("class_count", None)
        new_metadata.pop("class_id_limit", None)
    return bool(existing_manifest == requested_manifest)


def create_person_model_router(registry: ModelRegistryService) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/person-models",
        tags=["Person detection models"],
        route_class=PersonModelUploadRoute,
    )

    def public(version: ModelVersion) -> dict[str, object]:
        return {
            "id": version.id,
            "version": version.version,
            "architecture": version.manifest.metadata.get("family")
            or version.manifest.metadata.get("architecture", "YOLO"),
            "input_shape": version.manifest.inputs[0].shape,
            "output_shape": version.manifest.outputs[0].shape,
            "decode_mode": version.manifest.metadata["output_semantics"],
            "person_class_id": version.manifest.metadata["person_class_id"],
            "confidence": version.manifest.metadata.get("confidence_threshold", 0.25),
            "checksum_prefix": version.checksum[:12],
        }

    @router.get("")
    def list_person_models() -> dict[str, object]:
        items = []
        deployments = registry.list_deployments()
        for definition in registry.list_definitions():
            if definition.model_type is not ModelType.PERSON_DETECTOR:
                continue
            for version in registry.list_versions(definition.id):
                if version.manifest.metadata.get("architecture") not in {"YOLOX", "YOLO"}:
                    continue
                selected = [
                    {
                        "id": d.id,
                        "status": d.status.value,
                        "error_category": d.error_category,
                        "runtime_state": registry.runtime_status(d.id).state.value,
                    }
                    for d in deployments
                    if d.model_version_id == version.id
                ]
                items.append({**public(version), "deployments": selected})
        return {
            "items": items,
            "active_deployment_id": registry.assignments().get("PERSON_DETECTOR"),
            "max_upload_bytes": registry.max_upload_bytes,
        }

    @router.post("", status_code=201)
    async def upload_person_model(
        file: Annotated[UploadFile, File()],
        version: Annotated[str, Form(min_length=1, max_length=80)],
        decode_mode: Annotated[
            Literal["auto", "yolox_raw", "yolox_decoded", "yolox_xyxy"], Form()
        ] = "auto",
        person_class_id: Annotated[int, Form(ge=0, le=999)] = 0,
        confidence: Annotated[float, Form(ge=0.01, le=1)] = 0.3,
    ) -> dict[str, object]:
        try:
            if not (file.filename or "").lower().endswith(".onnx"):
                raise HTTPException(422, "INVALID_MODEL_FORMAT")
            payload = await file.read(registry.max_upload_bytes + 1)
            if len(payload) > registry.max_upload_bytes:
                raise HTTPException(413, "ARTIFACT_TOO_LARGE")

            def create() -> dict[str, object]:
                manifest = inspect_yolox_manifest(payload, decode_mode, person_class_id, confidence)
                definition = next(
                    (
                        d
                        for d in registry.list_definitions()
                        if d.name == "YOLOX Person" and d.model_type is ModelType.PERSON_DETECTOR
                    ),
                    None,
                )
                checksum = sha256(payload).hexdigest()
                if definition is not None:
                    same_checksum = [
                        item
                        for item in registry.list_versions(definition.id)
                        if item.checksum == checksum
                        and item.manifest.metadata.get("architecture") == "YOLOX"
                    ]
                    equivalent = next(
                        (item for item in same_checksum if _same_runtime_profile(item, manifest)),
                        None,
                    )
                    if equivalent is not None:
                        return public(equivalent)
                    if same_checksum:
                        raise ValueError("MODEL_CHECKSUM_PROFILE_CONFLICT")
                if definition is None:
                    definition = registry.create_definition(
                        "YOLOX Person",
                        ModelType.PERSON_DETECTOR,
                        "ONNX",
                        "Uploaded YOLOX person detector",
                    )
                created = registry.create_version(
                    definition.id, version, file.filename or "yolox.onnx", payload, manifest
                )
                return public(created)

            return await run_in_threadpool(create)
        except ValueError as error:
            category = str(error)
            raise HTTPException(
                422,
                category if category.isupper() and " " not in category else "INVALID_YOLOX_PROFILE",
            ) from error
        finally:
            await file.close()

    @router.patch("/{version_id}")
    def update_person_model(version_id: str, request: PersonModelUpdate) -> dict[str, object]:
        try:
            return public(
                registry.update_person_version(version_id, request.version, request.confidence)
            )
        except KeyError as error:
            raise HTTPException(404, "PERSON_MODEL_NOT_FOUND") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.delete("/{version_id}", status_code=204)
    def delete_person_model(version_id: str) -> None:
        try:
            version = registry.get_version(version_id)
            if version.manifest.model_type is not ModelType.PERSON_DETECTOR:
                raise KeyError(version_id)
            registry.delete_version(version_id)
        except KeyError as error:
            raise HTTPException(404, "PERSON_MODEL_NOT_FOUND") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return router
