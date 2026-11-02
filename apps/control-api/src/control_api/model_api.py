import json
from dataclasses import asdict
from typing import Annotated, Any, cast

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fr_domain.model_management import (
    DeploymentBackend,
    ModelDefinition,
    ModelDeployment,
    ModelManifest,
    ModelType,
    ModelVersion,
)
from pydantic import BaseModel, ConfigDict, Field

from control_api.model_artifacts import ArtifactValidationError
from control_api.model_registry import ModelRegistryService
from control_api.object_storage import ObjectStorageError


class DefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    model_type: ModelType
    framework: str = Field(min_length=1)
    description: str = ""


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: DeploymentBackend
    precision: str = "FP32"


class AssignmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_type: ModelType
    deployment_id: str = Field(min_length=1)


class ActivationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    resolved_backend: DeploymentBackend | None = None
    error_category: str | None = None
    execution_provider: str | None = None
    fallback_reason: str | None = None


def _public_definition(value: ModelDefinition) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(asdict(value)))


def _public_version(value: ModelVersion) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(asdict(value)))


def _public_deployment(value: ModelDeployment) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(asdict(value)))


def create_model_router(
    registry: ModelRegistryService,
    worker_token: str | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def authorize(token: str | None) -> None:
        if worker_token is None or token != worker_token:
            raise HTTPException(403, "worker authorization required")

    @router.get("/models")
    def list_models() -> dict[str, list[object]]:
        items: list[object] = []
        deployments = registry.list_deployments()
        for definition in registry.list_definitions():
            versions = registry.list_versions(definition.id)
            version_ids = {item.id for item in versions}
            model_deployments = [
                item for item in deployments if item.model_version_id in version_ids
            ]
            items.append(
                {
                    **asdict(definition),
                    "versions": [_public_version(item) for item in versions],
                    "deployments": [_public_deployment(item) for item in model_deployments],
                }
            )
        return {"items": items}

    @router.post("/models", status_code=http_status.HTTP_201_CREATED)
    def create_model(request: DefinitionCreate) -> object:
        return _public_definition(
            registry.create_definition(
                request.name,
                request.model_type,
                request.framework,
                request.description,
            )
        )

    @router.get("/models/{definition_id}")
    def get_model(definition_id: str) -> object:
        try:
            return _public_definition(registry.get_definition(definition_id))
        except KeyError as error:
            raise HTTPException(404, "model not found") from error

    @router.get("/models/{definition_id}/versions")
    def list_versions(definition_id: str) -> dict[str, list[object]]:
        try:
            return {
                "items": [_public_version(item) for item in registry.list_versions(definition_id)]
            }
        except KeyError as error:
            raise HTTPException(404, "model not found") from error

    @router.post(
        "/models/{definition_id}/versions",
        status_code=http_status.HTTP_201_CREATED,
    )
    async def upload_version(
        definition_id: str,
        version: Annotated[str, Form(min_length=1)],
        manifest: Annotated[str, Form(min_length=2)],
        file: Annotated[UploadFile, File()],
    ) -> object:
        payload = await file.read(registry.max_upload_bytes + 1)
        try:
            parsed_manifest = ModelManifest.model_validate(json.loads(manifest))
            created = registry.create_version(
                definition_id,
                version,
                file.filename or "model.onnx",
                payload,
                parsed_manifest,
            )
            return _public_version(created)
        except KeyError as error:
            raise HTTPException(404, "model not found") from error
        except (ValueError, ArtifactValidationError, json.JSONDecodeError) as error:
            category = str(error)
            safe = category if category.isupper() and " " not in category else "INVALID_MANIFEST"
            raise HTTPException(422, safe) from error

    @router.post("/model-versions/{version_id}/validate")
    def validate_version(version_id: str) -> object:
        try:
            return _public_version(registry.get_version(version_id))
        except KeyError as error:
            raise HTTPException(404, "model version not found") from error

    @router.post(
        "/model-versions/{version_id}/deployments",
        status_code=http_status.HTTP_201_CREATED,
    )
    def create_deployment(version_id: str, request: DeploymentCreate) -> object:
        try:
            return _public_deployment(
                registry.create_deployment(version_id, request.backend, request.precision)
            )
        except KeyError as error:
            raise HTTPException(404, "model version not found") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/deployments/{deployment_id}/activate")
    def activate(deployment_id: str) -> object:
        try:
            return _public_deployment(registry.request_activation(deployment_id))
        except KeyError as error:
            raise HTTPException(404, "deployment not found") from error

    @router.post("/deployments/{deployment_id}/deactivate")
    def deactivate(deployment_id: str) -> object:
        try:
            return _public_deployment(registry.deactivate(deployment_id))
        except KeyError as error:
            raise HTTPException(404, "deployment not found") from error

    @router.get("/deployments/{deployment_id}/runtime")
    def deployment_runtime(deployment_id: str) -> object:
        try:
            deployment = registry.get_deployment(deployment_id)
        except KeyError as error:
            raise HTTPException(404, "deployment not found") from error
        return jsonable_encoder(
            {
                "deployment_id": deployment.id,
                "configured_status": deployment.status,
                **asdict(registry.runtime_status(deployment_id)),
            }
        )

    @router.get("/model-assignments")
    def get_assignments() -> dict[str, object]:
        return {"assignments": registry.assignments()}

    @router.put("/model-assignments")
    def update_assignment(request: AssignmentUpdate) -> dict[str, object]:
        try:
            return {"assignments": registry.assign(request.model_type, request.deployment_id)}
        except KeyError as error:
            raise HTTPException(404, "deployment not found") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/recognition/readiness")
    def recognition_readiness() -> dict[str, object]:
        return registry.readiness()

    @router.get("/internal/model-deployments/pending")
    def pending(
        x_worker_token: str | None = Header(default=None),
    ) -> dict[str, list[object]]:
        authorize(x_worker_token)
        return {
            "items": [
                jsonable_encoder(registry.internal_deployment(item.id))
                for item in registry.pending_deployments()
            ]
        }

    @router.get("/internal/model-deployments/assigned")
    def assigned_deployments(
        x_worker_token: str | None = Header(default=None),
    ) -> dict[str, list[object]]:
        authorize(x_worker_token)
        return {
            "items": [
                jsonable_encoder(registry.internal_deployment(deployment_id))
                for deployment_id in registry.assignments().values()
                if registry.get_deployment(deployment_id).status.value == "ACTIVE"
            ]
        }

    @router.get("/internal/model-artifacts/{artifact_id}/content")
    def artifact_content(
        artifact_id: str,
        x_worker_token: str | None = Header(default=None),
    ) -> StreamingResponse:
        authorize(x_worker_token)
        try:
            chunks = registry.artifact_content(artifact_id)
        except KeyError as error:
            raise HTTPException(404, "model artifact not found") from error
        except ObjectStorageError as error:
            raise HTTPException(503, "model artifact storage unavailable") from error
        return StreamingResponse(
            chunks,
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/internal/model-deployments/{deployment_id}/activation-result")
    def activation_result(
        deployment_id: str,
        request: ActivationResult,
        x_worker_token: str | None = Header(default=None),
    ) -> object:
        authorize(x_worker_token)
        try:
            result = registry.record_activation(
                deployment_id,
                success=request.success,
                resolved_backend=request.resolved_backend,
                error_category=request.error_category,
                execution_provider=request.execution_provider,
                fallback_reason=request.fallback_reason,
            )
            return _public_deployment(result)
        except KeyError as error:
            raise HTTPException(404, "deployment not found") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return router
