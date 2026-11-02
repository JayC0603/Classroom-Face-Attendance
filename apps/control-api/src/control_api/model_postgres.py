import json
from dataclasses import asdict
from enum import Enum
from typing import Any, cast

from fr_domain.model_management import (
    ArtifactFormat,
    DeploymentBackend,
    DeploymentStatus,
    ModelDefinition,
    ModelDeployment,
    ModelManifest,
    ModelType,
    ModelVersion,
    ValidationStatus,
)
from pydantic import BaseModel

from control_api.camera_postgres import Connection
from control_api.model_artifacts import ModelArtifact

MODEL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS model_definitions (id text PRIMARY KEY, data jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS model_artifacts (id text PRIMARY KEY, data jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS model_versions (
 id text PRIMARY KEY,
 model_definition_id text NOT NULL REFERENCES model_definitions(id),
 version_label text NOT NULL,
 data jsonb NOT NULL,
 UNIQUE (model_definition_id, version_label)
);
CREATE TABLE IF NOT EXISTS model_deployments (
 id text PRIMARY KEY,
 model_version_id text NOT NULL REFERENCES model_versions(id),
 data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS model_assignments (
 model_type text PRIMARY KEY,
 deployment_id text NOT NULL REFERENCES model_deployments(id)
);
"""


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json(value: Any) -> str:
    return json.dumps(asdict(value), default=_json_default)


def _data(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return cast(dict[str, Any], json.loads(value))
    return dict(value)


def initialize_model_schema(connection: Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(MODEL_SCHEMA_SQL)
    connection.commit()


class PostgresModelRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def _put(self, table: str, item_id: str, payload: str, extra: tuple[object, ...] = ()) -> None:
        columns = "id,data" if not extra else "id,model_definition_id,version_label,data"
        placeholders = "%s,%s" if not extra else "%s,%s,%s,%s"
        values = (item_id, payload) if not extra else (item_id, *extra, payload)
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
                "ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data",
                values,
            )
        self._connection.commit()

    def _one(self, table: str, item_id: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(f"SELECT data FROM {table} WHERE id=%s", (item_id,))
            row = cursor.fetchone()
        return _data(row[0]) if row else None

    def _all(self, table: str, where: tuple[str, object] | None = None) -> list[dict[str, Any]]:
        with self._connection.cursor() as cursor:
            if where is None:
                cursor.execute(f"SELECT data FROM {table} ORDER BY id")
            else:
                cursor.execute(
                    f"SELECT data FROM {table} WHERE {where[0]}=%s ORDER BY id",
                    (where[1],),
                )
            rows = cursor.fetchall()
        return [_data(row[0]) for row in rows]

    def put_definition(self, value: ModelDefinition) -> None:
        self._put("model_definitions", value.id, _json(value))

    def get_definition(self, item_id: str) -> ModelDefinition | None:
        value = self._one("model_definitions", item_id)
        return self._definition(value) if value else None

    def list_definitions(self) -> list[ModelDefinition]:
        return [self._definition(value) for value in self._all("model_definitions")]

    def put_artifact(self, value: ModelArtifact) -> None:
        self._put("model_artifacts", value.id, _json(value))

    def get_artifact(self, item_id: str) -> ModelArtifact | None:
        value = self._one("model_artifacts", item_id)
        return ModelArtifact(**value) if value else None

    def list_artifacts(self) -> list[ModelArtifact]:
        return [ModelArtifact(**value) for value in self._all("model_artifacts")]

    def put_version(self, value: ModelVersion) -> None:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO model_versions (id,model_definition_id,version_label,data) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
                    "version_label=EXCLUDED.version_label,data=EXCLUDED.data",
                    (value.id, value.model_definition_id, value.version, _json(value)),
                )
            self._connection.commit()
        except Exception as error:
            rollback = getattr(self._connection, "rollback", None)
            if callable(rollback):
                rollback()
            if getattr(error, "sqlstate", None) == "23505":
                raise ValueError("MODEL_VERSION_EXISTS") from error
            raise

    def put_artifact_and_version(self, artifact: ModelArtifact, version: ModelVersion) -> None:
        if version.artifact_id != artifact.id:
            raise ValueError("ARTIFACT_VERSION_MISMATCH")
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO model_artifacts (id,data) VALUES (%s,%s)",
                    (artifact.id, _json(artifact)),
                )
                cursor.execute(
                    "INSERT INTO model_versions "
                    "(id,model_definition_id,version_label,data) VALUES (%s,%s,%s,%s)",
                    (version.id, version.model_definition_id, version.version, _json(version)),
                )
            self._connection.commit()
        except Exception:
            rollback = getattr(self._connection, "rollback", None)
            if rollback is not None:
                rollback()
            raise

    def get_version(self, item_id: str) -> ModelVersion | None:
        value = self._one("model_versions", item_id)
        return self._version(value) if value else None

    def list_versions(self, definition_id: str) -> list[ModelVersion]:
        return [
            self._version(value)
            for value in self._all("model_versions", ("model_definition_id", definition_id))
        ]

    def delete_version(self, version_id: str) -> None:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM model_versions WHERE id=%s FOR UPDATE", (version_id,)
                )
                cursor.execute(
                    "SELECT d.id FROM model_deployments d "
                    "WHERE d.model_version_id=%s AND (d.data->>'status' IN "
                    "('ACTIVE','ACTIVATION_PENDING') OR EXISTS "
                    "(SELECT 1 FROM model_assignments a WHERE a.deployment_id=d.id))",
                    (version_id,),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("ARTIFACT_IN_USE")
                cursor.execute(
                    "DELETE FROM model_deployments WHERE model_version_id=%s", (version_id,)
                )
                cursor.execute("DELETE FROM model_versions WHERE id=%s", (version_id,))
            self._connection.commit()
        except Exception:
            rollback = getattr(self._connection, "rollback", None)
            if callable(rollback):
                rollback()
            raise

    def put_deployment(self, value: ModelDeployment) -> None:
        payload = _json(value)
        with self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO model_deployments (id,model_version_id,data) "
                "VALUES (%s,%s,%s) ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data",
                (value.id, value.model_version_id, payload),
            )
        self._connection.commit()

    def get_deployment(self, item_id: str) -> ModelDeployment | None:
        value = self._one("model_deployments", item_id)
        return self._deployment(value) if value else None

    def list_deployments(self) -> list[ModelDeployment]:
        return [self._deployment(value) for value in self._all("model_deployments")]

    def put_assignment(self, model_type: ModelType, deployment_id: str) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO model_assignments (model_type,deployment_id) VALUES (%s,%s) "
                "ON CONFLICT (model_type) DO UPDATE SET deployment_id=EXCLUDED.deployment_id",
                (model_type.value, deployment_id),
            )
        self._connection.commit()

    def list_assignments(self) -> dict[ModelType, str]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT model_type,deployment_id FROM model_assignments ORDER BY model_type"
            )
            rows = cursor.fetchall()
        return {ModelType(row[0]): str(row[1]) for row in rows}

    @staticmethod
    def _definition(value: dict[str, Any]) -> ModelDefinition:
        value["model_type"] = ModelType(value["model_type"])
        return ModelDefinition(**value)

    @staticmethod
    def _version(value: dict[str, Any]) -> ModelVersion:
        value["format"] = ArtifactFormat(value["format"])
        value["validation_status"] = ValidationStatus(value["validation_status"])
        value["manifest"] = ModelManifest.model_validate(value["manifest"])
        return ModelVersion(**value)

    @staticmethod
    def _deployment(value: dict[str, Any]) -> ModelDeployment:
        value["backend"] = DeploymentBackend(value["backend"])
        value["status"] = DeploymentStatus(value["status"])
        if value.get("resolved_backend") is not None:
            value["resolved_backend"] = DeploymentBackend(value["resolved_backend"])
        return ModelDeployment(**value)
