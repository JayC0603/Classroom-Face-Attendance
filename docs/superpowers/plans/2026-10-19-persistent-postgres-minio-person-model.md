# Persistent PostgreSQL, MinIO, and Person Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist application metadata in PostgreSQL, store ONNX/enrollment/evidence objects in private MinIO buckets, and activate the existing YOLOX person model on the live webcam.

**Architecture:** PostgreSQL remains authoritative for structured records and model assignments. A MinIO-backed object-store adapter replaces local files at API storage boundaries; workers download model objects through an authenticated API endpoint into a checksum-verified local cache. The existing ONNX CPU runtime validates and activates the YOLOX model before assignment.

**Tech Stack:** FastAPI, Python 3.11+, psycopg 3, PostgreSQL 17, MinIO S3 API, MinIO Python SDK, ONNX, ONNX Runtime CPU, Docker Compose, pytest.

**Spec:** `docs/superpowers/specs/2026-10-19-persistent-postgres-minio-person-model-design.md`

## Global Constraints

- PostgreSQL is authoritative for metadata and assignments.
- Store ONNX model artifacts, enrolled face images, and recognition evidence as private MinIO objects. PostgreSQL stores opaque object keys and checksums, not binary bodies.
- Keep MinIO credentials on the API side. The worker obtains model bytes through an authenticated API artifact endpoint, caches them locally, and verifies SHA-256 before activation.
- Keep PostgreSQL, Redis, MinIO S3 API, and MinIO console bound to loopback.
- Keep local artifact copies until MinIO contents and database records are verified.
- The first supported deployment is ONNX Runtime CPU.
- Runtime status remains operational process state and is not persisted as high-frequency PostgreSQL data.
- Do not implement face detection, face embeddings, identity comparison, or enrollment processing in this change.
- A configured but unavailable database or object store must fail readiness; do not silently report in-memory success.

## Review Focus

1. **Configured PostgreSQL unavailable:** startup/readiness fails and never falls back to in-memory repositories; test in Task 1.
2. **Object upload succeeds but metadata transaction fails:** the object is compensated or safely reconciled; test in Tasks 2–4.
3. **Truncated or corrupt model object:** worker rejects the checksum and preserves the previous assignment; test in Task 5.
4. **Invalid YOLOX input/output profile or model class selection:** upload/import returns a typed validation error and does not create an active assignment; test in Task 6.
5. **Browser requests private enrollment/evidence content:** content is served through the API and no MinIO URL or credentials are exposed; test in Tasks 3–4.

---

### Task 1: Local PostgreSQL and MinIO services with fail-closed API configuration

**Files:**
- Modify: `infrastructure/local/compose.yaml`
- Modify: `infrastructure/local/.env.example`
- Modify: `infrastructure/local/README.md`
- Create: `apps/control-api/src/control_api/database_migrations.py`
- Create: `apps/control-api/tests/test_database_migrations.py`
- Modify: `apps/control-api/src/control_api/main.py`
- Modify: `apps/control-api/src/control_api/camera_bootstrap.py`
- Modify: `apps/control-api/src/control_api/model_bootstrap.py`
- Modify: `apps/control-api/tests/test_camera_bootstrap.py`
- Create: `apps/control-api/tests/test_model_bootstrap.py`
- Modify: `apps/control-api/tests/test_api.py`

**Interfaces:**
- Consumes: existing `FR_POSTGRES_DSN`, `FR_CAMERA_ENCRYPTION_KEY`, `FR_WORKER_INTERNAL_TOKEN` and model bootstrap functions.
- Produces: `apply_database_migrations(connection: Connection, migrations_dir: Path) -> None`, which applies ordered SQL migrations once using a PostgreSQL migration ledger; API startup runs migrations before repositories and reports PostgreSQL persistence.

- [ ] **Step 1: Write failing tests**: `test_explicit_postgres_failure_does_not_fallback_to_memory` asserts a connector failure raises; `test_apply_database_migrations_runs_in_order_once` asserts sorted migrations execute and recorded migrations are skipped; `test_health_reports_postgres_persistence` asserts PostgreSQL mode in the health payload.
- [ ] **Step 2: Run** `pytest apps/control-api/tests/test_camera_bootstrap.py apps/control-api/tests/test_model_bootstrap.py apps/control-api/tests/test_database_migrations.py apps/control-api/tests/test_api.py -q`; confirm the new failure cases fail before implementation.
- [ ] **Step 3: Add MinIO service configuration** to Compose with private named storage, loopback-only S3/console ports, health check, and an idempotent bucket-initialization service for `models`, `face-images`, and `evidence`.
- [ ] **Step 4: Add non-secret MinIO settings** to `.env.example` and document local secret generation, the Compose start command, health checks, and that existing volumes must not be deleted during startup.
- [ ] **Step 5: Implement `apply_database_migrations(connection, migrations_dir)`** with a `schema_migrations` ledger and one transaction per migration. Apply migrations `001` through `007` on a fresh database and skip recorded versions on restart. Run it before repository construction; preserve in-memory mode only when no DSN is configured.
- [ ] **Step 6: Make configured PostgreSQL fail closed** in camera, model, face-registry, and vector-index bootstrap. Add health output for the selected PostgreSQL persistence mode without revealing credentials.
- [ ] **Step 7: Run** the four Task 1 test files and `docker compose --env-file infrastructure/local/.env.example -f infrastructure/local/compose.yaml config --quiet`; expected: all tests pass and Compose validation exits `0`.
- [ ] **Step 8: Commit** only Task 1 files with message `feat: add local minio and fail-closed service config`.

### Task 2: S3-compatible MinIO object-store adapter

**Files:**
- Create: `apps/control-api/src/control_api/object_storage.py`
- Modify: `pyproject.toml`
- Test: `apps/control-api/tests/test_object_storage.py`
- Test: `apps/control-api/tests/integration/test_minio_object_storage.py`

**Interfaces:**
- Consumes: MinIO endpoint, access key, secret key, and bucket settings from Task 1.
- Produces: `ObjectStorage.put(key: str, content: bytes, content_type: str) -> None`, `ObjectStorage.get(key: str) -> bytes`, `ObjectStorage.iter_bytes(key: str, chunk_size: int = 1048576) -> Iterator[bytes]`, `ObjectStorage.delete(key: str) -> None`, and `ObjectStorage.health_check() -> None`; production implementation `MinioObjectStorage(endpoint, access_key, secret_key, bucket)` validates namespace keys and maps S3 failures to typed storage errors. Each API service gets an instance fixed to its configured bucket; request data never selects a bucket.

- [ ] **Step 1: Write failing tests**: `test_minio_object_storage_round_trip` asserts equal bytes after put/get; `test_object_storage_iter_bytes_is_bounded` asserts chunks do not exceed 1 MiB; `test_object_storage_rejects_invalid_key` asserts a typed error; `test_object_storage_delete_is_idempotent` asserts repeat delete succeeds.
- [ ] **Step 2: Run** `pytest apps/control-api/tests/test_object_storage.py -q`; confirm the missing adapter/API causes the expected failures.
- [ ] **Step 3: Add `minio>=7.2,<8`** to base dependencies and implement `ObjectStorage` plus `MinioObjectStorage` using `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, and fixed per-service bucket settings. `health_check()` verifies the configured bucket; do not allow callers to choose bucket names from request data.
- [ ] **Step 4: Run** `pytest apps/control-api/tests/test_object_storage.py -q`; expected: all fake-client unit tests pass.
- [ ] **Step 5: Add `test_minio_object_storage_integration_round_trip`** that writes, reads, and deletes a uniquely named object in the private development bucket when the Compose profile is running.
- [ ] **Step 6: Commit** Task 2 files with message `feat: add minio object storage adapter`.

### Task 3: Store model artifacts and face-registration images in MinIO

**Files:**
- Modify: `apps/control-api/src/control_api/model_artifacts.py`
- Modify: `apps/control-api/src/control_api/model_bootstrap.py`
- Modify: `apps/control-api/src/control_api/face_image_storage.py`
- Modify: `apps/control-api/src/control_api/face_registry.py`
- Modify: `apps/control-api/src/control_api/face_registry_api.py`
- Modify: `apps/control-api/src/control_api/main.py`
- Test: `apps/control-api/tests/test_model_artifacts.py`
- Test: `apps/control-api/tests/test_model_registry.py`
- Test: `apps/control-api/tests/test_face_registry.py`
- Test: `apps/control-api/tests/test_face_registry_postgres.py`

**Interfaces:**
- Consumes: `ObjectStorage` from Task 2 and existing model/face registries.
- Produces: API model and face-image services use MinIO object keys; the image content route reads validated JPEG bytes through the API and retains `private, no-store` and `nosniff` headers.

- [ ] **Step 1: Write failing tests**: `test_model_ingest_persists_artifact_and_version_atomically` asserts both metadata rows commit together; `test_model_ingest_deletes_object_when_metadata_commit_fails` asserts object compensation; `test_face_image_content_uses_private_object_storage` asserts returned JPEG bytes and no S3 URL.
- [ ] **Step 2: Run** `pytest apps/control-api/tests/test_model_artifacts.py apps/control-api/tests/test_model_registry.py apps/control-api/tests/test_face_registry.py apps/control-api/tests/test_face_registry_postgres.py -q`; confirm the new storage-injection expectations fail.
- [ ] **Step 3: Type `ModelArtifactIngestor` against a storage protocol** implemented by the current local adapter and the MinIO-backed adapter. Preserve local storage for unit tests and developer mode without MinIO.
- [ ] **Step 4: Refactor `FaceImageStorage`** to retain existing image validation/EXIF stripping while persisting opaque object keys through `ObjectStorage`; return content with a FastAPI `Response` instead of requiring a local path.
- [ ] **Step 5: Add `ModelRepository.put_artifact_and_version(artifact: ModelArtifact, version: ModelVersion) -> None`**. Implement it as one PostgreSQL transaction and one locked in-memory operation; `ModelRegistryService.create_version` calls it once and deletes the uploaded object if it raises.
- [ ] **Step 6: Wire MinIO storage in application bootstrap** and add health reporting for PostgreSQL mode and MinIO readiness through `ObjectStorage.health_check()` without revealing credentials. Ensure failed metadata commits remove newly written objects without masking the original failure.
- [ ] **Step 7: Run** `pytest apps/control-api/tests/test_model_artifacts.py apps/control-api/tests/test_model_registry.py apps/control-api/tests/test_face_registry.py apps/control-api/tests/test_face_registry_postgres.py -q`; expected: all tests pass.
- [ ] **Step 8: Commit** Task 3 files with message `feat: store model and enrollment artifacts in minio`.

### Task 4: Persist recognition history and attendance in PostgreSQL with MinIO evidence

**Files:**
- Create: `infrastructure/database/migrations/008_recognition_events.sql`
- Modify: `apps/control-api/src/control_api/recognition_events.py`
- Modify: `apps/control-api/src/control_api/main.py`
- Modify: `apps/control-api/src/control_api/simple_recognition_api.py`
- Create: `apps/control-api/tests/test_recognition_events.py`
- Modify: `apps/control-api/tests/test_recognition_camera_api.py`

**Interfaces:**
- Consumes: `ObjectStorage` from Task 2, existing retention and attendance rules, and the PostgreSQL DSN.
- Produces: `RecognitionEventStore(connection: Connection, storage: ObjectStorage)` with existing `record_verified`, `mark_tracks_absent`, `event_rows`, `attendance_rows`, and `evidence_content() -> bytes | None` behaviors backed by PostgreSQL and MinIO.

- [ ] **Step 1: Write failing tests**: `test_verified_event_deduplicates_by_camera_session_track_person` asserts a duplicate event is ignored; `test_attendance_is_once_per_person_per_local_day` asserts one row after repeated sightings; `test_expiry_clears_evidence_object_and_reference` asserts expiry removes the object and nulls its key; `test_missing_expired_object_cleanup_is_idempotent` asserts cleanup continues.
- [ ] **Step 2: Run** `pytest apps/control-api/tests/test_recognition_events.py apps/control-api/tests/test_recognition_camera_api.py -q`; confirm SQLite-specific storage does not satisfy the PostgreSQL contract.
- [ ] **Step 3: Add migration `008_recognition_events.sql`** for event and attendance tables, constraints, and query indexes matching the current SQLite uniqueness semantics; the Task 1 migration runner applies it once.
- [ ] **Step 4: Replace SQLite persistence** in `RecognitionEventStore` with PostgreSQL transactions and keep deduplication/attendance behavior unchanged. Persist evidence as an opaque MinIO key; delete expired objects idempotently and clear the corresponding database key only after deletion succeeds or the object is already absent.
- [ ] **Step 5: Add one-time legacy import** for an existing `data/recognition-events/recognition.sqlite3` and its local evidence files. Import by stable event/attendance IDs; preserve existing expiry times; verify checksums; make reruns safe and never delete local source files during import.
- [ ] **Step 6: Serve evidence bytes via the API** with private/no-store headers and no direct MinIO URL.
- [ ] **Step 7: Run** `pytest apps/control-api/tests/test_recognition_events.py apps/control-api/tests/test_recognition_camera_api.py -q`; expected: all pass, migration/import is idempotent, and event/evidence failures never create a false committed reference.
- [ ] **Step 8: Commit** Task 4 files with message `feat: persist attendance and evidence metadata in postgres`.

### Task 5: Authenticated MinIO model delivery to the worker

**Files:**
- Modify: `apps/control-api/src/control_api/model_registry.py`
- Modify: `apps/control-api/src/control_api/model_api.py`
- Modify: `workers/vision-worker/src/vision_worker/model_delivery.py`
- Modify: `workers/vision-worker/src/vision_worker/main.py`
- Modify: `apps/control-api/tests/test_model_api.py`
- Modify: `workers/vision-worker/tests/test_model_delivery.py`

**Interfaces:**
- Consumes: MinIO object storage and immutable model artifact metadata from Tasks 2–3.
- Produces: worker-authenticated `GET /api/v1/internal/model-artifacts/{artifact_id}/content`; `HttpDeploymentProvider` streams a deployment artifact into a configured local cache and returns a `PendingModelDeployment` whose `artifact_path` is the verified local cache file.

- [ ] **Step 1: Write failing tests**: `test_internal_artifact_content_requires_worker_token` asserts 403 without the token; `test_worker_download_verifies_checksum_before_cache_replace` asserts a corrupt payload does not replace cache; `test_worker_download_reuses_matching_checksum_cache` asserts no second HTTP download.
- [ ] **Step 2: Run** `pytest apps/control-api/tests/test_model_api.py workers/vision-worker/tests/test_model_delivery.py -q`; confirm current local-path delivery fails the new authenticated-download cases.
- [ ] **Step 3: Update internal deployment metadata** to return `artifact_id`, checksum, size, and a relative content route; do not serialize filesystem paths or MinIO URLs.
- [ ] **Step 4: Implement the API content route** with worker-token authorization, bounded streaming, and correct not-found behavior.
- [ ] **Step 5: Implement worker cache download** using `httpx.stream` into a temporary file, then SHA-256 verification, atomic rename, and checksum-keyed reuse. Keep `ONNX_CPU` runtime selection, warmup, health checks, activation reporting, and old active-model preservation on failure.
- [ ] **Step 6: Run** `pytest apps/control-api/tests/test_model_api.py workers/vision-worker/tests/test_model_delivery.py -q`; expected: valid objects load once, invalid objects fail with a typed error, and previous active assignment remains available.
- [ ] **Step 7: Commit** Task 5 files with message `feat: fetch model artifacts securely from api`.

### Task 6: Idempotently restore and activate the existing person model

**Files:**
- Create: `scripts/import_person_model.py`
- Modify: `apps/control-api/src/control_api/person_model_api.py`
- Modify: `apps/control-api/src/control_api/person_models.py` only if import needs a shared profile builder
- Create: `apps/control-api/tests/test_person_model_import.py`
- Modify: `apps/control-api/tests/test_yolox_upload.py`
- Modify: `workers/vision-worker/tests/test_yolo_onnx.py`

**Interfaces:**
- Consumes: PostgreSQL model repository, MinIO model storage, and authenticated worker delivery from Tasks 1–5.
- Produces: repeatable command `python scripts/import_person_model.py --path <onnx> --version <label>` that uses the running API, validates the ONNX graph, skips a version with the same SHA-256, registers the YOLOX profile, creates an `ONNX_CPU` deployment, waits for worker activation, and assigns only after success.

- [ ] **Step 1: Write failing tests**: `test_existing_yolox_profile_matches_inspected_contract` asserts input `[batch,3,608,1088]`, output `[batch,13566,6]`, mode `yolox_xyxy`, class `0`, threshold `0.3`; `test_import_person_model_is_idempotent_by_checksum` asserts one version for identical bytes; `test_import_does_not_assign_after_activation_failure` asserts the prior assignment remains.
- [ ] **Step 2: Run** `pytest apps/control-api/tests/test_yolox_upload.py workers/vision-worker/tests/test_yolo_onnx.py -q`; confirm missing import/recovery behavior fails.
- [ ] **Step 3: Implement an idempotent person-model import endpoint** that compares uploaded ONNX SHA-256 against existing versions and returns the existing version on a match; keep manifest creation in `inspect_yolox_manifest(payload, "auto", 0, 0.3)`.
- [ ] **Step 4: Implement `python scripts/import_person_model.py --path <onnx> --version <label> --api-url http://127.0.0.1:8000`** to select one identical existing `.onnx` copy, call the idempotent API import route, persist one object and metadata row, and leave both local originals untouched.
- [ ] **Step 5: Create the `ONNX_CPU` deployment**, call the existing activation endpoint, poll its runtime endpoint until it reports successful checksum validation, warmup, and health, then call the model-assignment endpoint. Keep the prior assignment if validation or activation fails.
- [ ] **Step 6: Run** `pytest apps/control-api/tests/test_person_model_import.py apps/control-api/tests/test_yolox_upload.py workers/vision-worker/tests/test_yolo_onnx.py -q`; expected: a second import creates no duplicate artifact/version, and invalid ONNX profile or worker load leaves assignments unchanged.
- [ ] **Step 7: Commit** Task 6 files with message `feat: restore and activate existing yolox person model`.

### Task 7: End-to-end restart and failure validation

**Files:**
- Modify: `infrastructure/local/README.md`
- Create: `apps/control-api/tests/integration/test_postgres_minio_restart.py`
- Create: `docs/acceptance/postgres-minio-person-model.md`

**Interfaces:**
- Consumes: Compose profile and all services from Tasks 1–6.
- Produces: reproducible local acceptance procedure and evidence for persistence, recovery, and webcam detection. No accuracy or sustained-performance claim is made without its dedicated measurement.

- [ ] **Step 1: Add `test_postgres_minio_restart_preserves_records_and_objects`** that creates a camera, registers/assigns the model, creates a face profile/image, records an event/evidence object, and asserts health reports PostgreSQL and MinIO.
- [ ] **Step 2: Run** `pytest apps/control-api/tests/integration/test_postgres_minio_restart.py -q`; expected: all metadata and objects can be read through the API after API restart.
- [ ] **Step 3: Restart the API and recreate containers without deleting volumes**; rerun GET checks and verify the camera, model assignment, face registry, attendance, and image/evidence content survive.
- [ ] **Step 4: Inject MinIO and PostgreSQL unavailability separately**; expected: health is unhealthy, uploads/activation do not report success, and normal behavior recovers after the service is restored.
- [ ] **Step 5: On the live webcam, verify preview JPEGs and person boxes/Track IDs** while the worker reports `CPUExecutionProvider`; record measured processing FPS and latency only if collected during this run.
- [ ] **Step 6: Document executed evidence and untested limits** in the acceptance record and update local setup instructions, including matched PostgreSQL-plus-MinIO backup/restore guidance.
- [ ] **Step 7: Commit** Task 7 files with message `test: validate postgres minio model recovery end to end`.
