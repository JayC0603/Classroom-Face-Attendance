import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from control_api.camera_postgres import PostgresCameraRepository
from control_api.cameras import CameraCreate, CameraService, FernetCameraSecretProtector
from control_api.database_migrations import apply_database_migrations
from control_api.model_artifacts import (
    LocalModelArtifactStorage,
    ModelArtifactIngestor,
    OnnxArtifactInspector,
)
from control_api.model_postgres import PostgresModelRepository
from control_api.model_registry import ModelRegistryService
from control_api.person_models import inspect_yolox_manifest
from fr_domain.model_management import DeploymentBackend, ModelType
from onnx import TensorProto, helper
from psycopg import sql
from psycopg.conninfo import make_conninfo


def controlled_graph():
    model = helper.make_model(
        helper.make_graph(
            [
                helper.make_node(
                    "Constant",
                    [],
                    ["output"],
                    value=helper.make_tensor(
                        "v", TensorProto.FLOAT, [1, 13566, 6], [0.0] * (13566 * 6)
                    ),
                )
            ],
            "crud-test",
            [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, 608, 1088])],
            [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 13566, 6])],
        ),
        opset_imports=[helper.make_opsetid("", 13)],
    )
    model.ir_version = 10
    return model.SerializeToString()


def test_postgres_crud_protects_references_and_persists_model_labels(tmp_path):
    dsn = os.getenv("FR_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("FR_TEST_POSTGRES_DSN required")
    schema = "fr_crud_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = make_conninfo(dsn, options=f"-csearch_path={schema}")
    try:
        with psycopg.connect(scoped) as connection:
            apply_database_migrations(connection, Path("infrastructure/database/migrations"))
            cameras = CameraService(
                PostgresCameraRepository(connection), FernetCameraSecretProtector.generate()
            )
            camera = cameras.create(CameraCreate(name="Delete test", source_type="webcam"))
            connection.execute(
                "CREATE TABLE deletion_reference("
                "camera_id text REFERENCES cameras(id) ON DELETE RESTRICT)"
            )
            connection.execute("INSERT INTO deletion_reference VALUES (%s)", (camera.id,))
            connection.commit()
            with pytest.raises(ValueError, match="CAMERA_IN_USE_BY_CLASS_SESSION"):
                cameras.delete(camera.id, camera.revision)
            assert cameras.get_public(camera.id).name == camera.name
            connection.execute("DELETE FROM deletion_reference")
            connection.commit()
            cameras.delete(camera.id, camera.revision)
            assert cameras.public_list() == []

            models = ModelRegistryService(
                PostgresModelRepository(connection),
                ModelArtifactIngestor(
                    LocalModelArtifactStorage(tmp_path), OnnxArtifactInspector(), 1024 * 1024
                ),
            )
            definition = models.create_definition("Person", ModelType.PERSON_DETECTOR, "ONNX")
            payload = controlled_graph()
            manifest = inspect_yolox_manifest(payload, "yolox_raw", 0, 0.3)
            first = models.create_version(definition.id, "v1", "person.onnx", payload, manifest)
            models.update_person_version(first.id, "renamed", 0.4)
            assert models.get_version(first.id).version == "renamed"
            second = models.create_version(definition.id, "v1", "person.onnx", payload, manifest)
            with pytest.raises(ValueError, match="MODEL_VERSION_EXISTS"):
                models.update_person_version(second.id, "renamed")
            deployment = models.create_deployment(first.id, DeploymentBackend.ONNX_CPU, "FP32")
            models.request_activation(deployment.id)
            with pytest.raises(ValueError, match="ARTIFACT_IN_USE"):
                models.delete_version(first.id)
            models.deactivate(deployment.id)
            models.repository.put_assignment(ModelType.PERSON_DETECTOR, deployment.id)
            with pytest.raises(ValueError, match="ARTIFACT_IN_USE"):
                models.delete_version(first.id)
            connection.execute("DELETE FROM model_assignments")
            connection.commit()
            models.delete_version(first.id)
            assert models.list_deployments() == []
            assert [v.id for v in models.list_versions(definition.id)] == [second.id]
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
