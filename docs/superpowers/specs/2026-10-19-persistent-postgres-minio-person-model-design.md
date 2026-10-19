# Persistent PostgreSQL, MinIO, and Person Model Design

## Purpose

Make the existing local face-recognition project survive API and container restarts, store binary artifacts outside the API filesystem, and run the user's existing YOLOX person model on the live worker webcam so person boxes appear in the UI.

## Verified starting point

- The vision worker can capture the active local webcam and publish JPEG preview frames. Its current runtime is capture-only because it has no assigned person detector.
- The API was restarted with in-memory repositories. The camera UI configuration and model registry are therefore not durable in this process.
- Two 35,997,222-byte ONNX files exist under `data/models/`. Their SHA-256 digests match. Inspection of one file succeeds and reports input `[batch, 3, 608, 1088]`, output `[batch, 13566, 6]`, YOLOX `yolox_xyxy` semantics, person class `0`, and default confidence `0.3`.
- `infrastructure/local/compose.yaml` already defines PostgreSQL and Redis, but those local services are stopped. It does not define MinIO. No existing model, face-image, or evidence storage path uses MinIO.
- Camera, model, face registry, and vector-index repositories already have PostgreSQL implementations. Recognition events and attendance currently use SQLite. Model and face-image artifacts use local files; the evidence storage abstraction has a local implementation.
- Worker model delivery currently interprets the artifact storage key as a worker-local filesystem path. It must change when artifacts live in MinIO.

## Goals

1. Run loopback-only local PostgreSQL and MinIO with health checks and named persistent volumes.
2. Use PostgreSQL as the durable source of truth for cameras, model metadata/deployments/assignments, face registry metadata, recognition history, attendance, and evidence retention metadata.
3. Store ONNX model artifacts, enrolled face images, and recognition evidence as private MinIO objects. PostgreSQL stores opaque object keys and checksums, not binary bodies.
4. Re-import one copy of the existing YOLOX artifact, validate its contract, create an ONNX CPU deployment, assign it to `PERSON_DETECTOR`, and have the worker load it and publish detections.
5. Keep MinIO credentials on the API side. The worker obtains model bytes through an authenticated API artifact endpoint, caches them locally, and verifies the checksum before activation.
6. Preserve truthful readiness and error behavior: storage or model-loading failures remain visible and never appear as successful detection.

## Non-goals

- Implementing face detection, face embeddings, identity comparison, or enrollment processing. The existing registration images remain pending-processing.
- Claiming GPU support, measured inference FPS, or detection accuracy. The first supported deployment is ONNX Runtime CPU.
- Removing local artifact copies as part of the first migration. They remain rollback sources until MinIO contents and database records are verified.
- Exposing MinIO to browsers or the public network, or putting MinIO credentials in frontend configuration.
- Persisting high-frequency runtime telemetry in PostgreSQL; current runtime status remains operational, replaceable process state.

## Target architecture

### Local infrastructure

Extend `infrastructure/local/compose.yaml` with a MinIO server and an idempotent bucket-initialization job. Keep PostgreSQL, Redis, MinIO S3 API, and MinIO console bound to loopback. Add readiness checks and named volumes. Keep development credentials in ignored local environment configuration, document generation of secrets, and provide non-secret placeholders in the example environment file. The API must wait for PostgreSQL and MinIO health before reporting ready when those dependencies are configured.

Create private buckets for model artifacts, face images, and evidence. Use opaque UUID-based keys with stable prefixes; never derive object paths from uploaded filenames. The API owns S3 credentials and validates object reads and writes. Model objects are immutable once a model version is registered. Evidence deletion follows each event's retention deadline; face-image retention is controlled by explicit delete operations.

### PostgreSQL

Keep existing camera, model, face-registry, and vector-index PostgreSQL repositories and their idempotent schema initialization/migrations. Move recognition-event and attendance tables from SQLite to PostgreSQL, preserving the existing uniqueness and deduplication rules. Store evidence object key and expiry alongside each event. Configure one database DSN for the API; do not silently fall back to in-memory repositories when PostgreSQL was explicitly configured but unavailable. Health/readiness must identify the selected persistence mode.

PostgreSQL is authoritative for metadata and assignments. MinIO object operations and relational transactions cannot be atomic together: upload the immutable object first, commit metadata second, and perform idempotent compensating deletion if the database write fails. Cleanup jobs must tolerate already-missing objects and retry object deletion failures without deleting committed event or registry rows prematurely.

### Storage adapters and API behavior

Implement one S3-compatible MinIO adapter behind the existing model-artifact, face-image, and evidence storage boundaries. Keep local filesystem adapters for dependency-free unit tests. The face-image content endpoint reads a private object and returns the JPEG through the API with the existing private/no-store and content-sniffing protections; it does not expose an object URL.

The internal model-deployment metadata response must contain artifact identity, checksum, size, and an authenticated relative content endpoint rather than an API-host filesystem path. Add a worker-token-protected artifact content endpoint that streams the matching object. The worker downloads to a temporary local cache file, verifies SHA-256, atomically renames it, and then uses the existing ONNX validation, warmup, health check, activation, and runtime-reporting lifecycle. Keep worker access bounded and avoid downloading an unchanged artifact on every synchronization cycle.

### Existing YOLOX import and activation

Import one of the identical existing ONNX files. Preserve the local source files. Register it as a `YOLOX Person` / `PERSON_DETECTOR` version after the existing YOLOX profile inspection validates its graph. The inspected profile is `608x1088`, dense BNC output with six columns, `yolox_xyxy`, class `0`, confidence `0.3`; retain checksum verification and ONNX CPU compatibility. Create an `ONNX_CPU` deployment, request activation, and assign it to `PERSON_DETECTOR` only after the worker reports successful load and warmup. A failed validation or activation must leave any prior active assignment intact.

The camera runtime must report the active deployment and CPU execution provider. The existing preview overlay displays person boxes and Track IDs when detections exist; an empty frame remains an honest no-detection result. If the webcam is unavailable or the model cannot load, show the typed error and do not report the camera/model as ready.

### Migration and recovery

1. Start healthy PostgreSQL and MinIO services with persistent named volumes.
2. Start the API with explicit PostgreSQL, MinIO, encryption-key, and worker-token configuration; verify the health response reports PostgreSQL persistence and object storage readiness.
3. Apply idempotent schema migrations and provision private buckets.
4. Import one local ONNX artifact to MinIO, verify its checksum, and persist its definition/version metadata. Recreate a camera in PostgreSQL if it only existed in the prior in-memory API process.
5. Create and activate the person deployment; verify worker download, cache checksum, warmup, assignment, and live webcam boxes.
6. Verify face-image uploads, event evidence, attendance, and model metadata survive API restart. Keep local model source files until these checks pass.

PostgreSQL and MinIO volumes are the local source of durability. Document backup/restore of both the database and object buckets as a matched pair; restoring only one side may leave metadata pointing to missing content or unreferenced objects.

## Configuration

Use backend-only environment variables for PostgreSQL DSN, stable camera encryption key, worker internal token, MinIO endpoint, access key, secret key, bucket names, and worker artifact-cache directory. The browser receives no storage credentials. Do not load a local development `.env.example` containing placeholder values as production secrets. Missing credentials or an unreachable configured dependency must fail startup/readiness clearly.

## Verification and acceptance

- Compose reports PostgreSQL and MinIO healthy; their ports bind only to loopback and their named volumes preserve data after container recreation.
- API health reports PostgreSQL persistence and MinIO readiness; stopping either configured dependency produces a visible unhealthy state rather than an in-memory success fallback.
- The existing model is listed after API restart with its inspected shape, YOLOX output mode, and checksum. No duplicate model object is created for the identical local copy.
- The deployment reaches active runtime state on `CPUExecutionProvider`; the worker's cache checksum matches the object checksum.
- With the live webcam available, the UI preview continues and shows person boxes/Track IDs for detections. Camera status, inference status, and errors agree with worker status.
- Camera, model registry, assignments, face-registry records, event history, and attendance remain after API restart. Face-image and evidence content is retrievable only through the API and is absent after its configured deletion/retention action.
- Injected MinIO/PostgreSQL failures do not create false successful uploads, activations, detections, or evidence references. Recovery reuses committed state and removes or safely tolerates orphaned objects.
- Report CPU performance only from an executed benchmark on this machine; leave accuracy and sustained-load claims untested until separately measured.

## Decisions captured from the user

- PostgreSQL stores metadata and structured records.
- MinIO stores ONNX files, face-registration images, and recognition evidence.
- The existing person YOLOX model should be activated and produce person boxes on the webcam preview.
- First deployment targets local development and ONNX Runtime CPU.
