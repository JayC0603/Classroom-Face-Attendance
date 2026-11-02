# Future Face Recognition database, API and state contract

Status: approved implementation guidance for later milestones. No schema, route, worker,
or React feature in this document is implemented by the YOLO ONNX CPU milestone.
Read alongside [Face Registry and Unknown Verification](face-registry-unknown-verification-future.md).
Keep one Person, model-management, event, evidence, configuration and WebSocket domain;
extend existing boundaries rather than building parallel systems.

## Durable data model

| Entity | Required data and constraints |
|---|---|
| Person | ID, unique required Student ID, required full name, `ACTIVE`/`DISABLED`, created/updated times. Index Student ID, searchable name and status. Disable rather than hard-delete; historical events retain their references. |
| FaceImage | ID, Person FK, opaque storage reference, optional original filename, MIME, width/height, source (`MANUAL_UPLOAD`, `CAMERA_CAPTURE`, `UNKNOWN_CORRECTION`), quality status/score and measured quality diagnostics, creator/time, active flag. Only accepted and active images can contribute vectors. Preserve history when deactivated. |
| FaceEmbedding | ID, Person FK, FaceImage FK, existing model definition/version FKs, dimension, normalization and compatibility metadata, vector, created time, active flag. One image may have embeddings from multiple model versions. Never send or log raw vectors. |
| VectorIndexRevision | ID/revision, status (`BUILDING`, `ACTIVE`, `ERROR`, `SUPERSEDED`), model/version compatibility, dimension, normalization, metric, entry count, opaque artifact reference, checksum, lifecycle times/error. Only a compatible validated revision can become active. |
| VectorIndexEntry | Index revision, FAISS position, stable FaceEmbedding ID and Person ID. Validate mapping count against index count; FAISS positions are never durable identity. |
| RecognitionEvent | Immutable recognition outcome with camera/session/track/time, result, optional Person/best candidate/similarity/observation count, model and config revisions, vector index revision and optional evidence references. Technical failures never create a fake Person. |
| RecognitionReview | Unique active task for an eligible `UNKNOWN_PENDING` event; event FK, status, optional selected Person, reviewer, reviewed/created/updated times and note. Resolved review is immutable in MVP. |
| RecognitionFeedback | Separate audit record linked to event/review, original result/candidate/similarity, correction Person and generated FaceImage/FaceEmbedding IDs, resolution, reviewer and time. Never use as automatic training data. |

FaceImage quality outcomes include accepted, no face, multiple faces, too small,
blur, exposure, low confidence, alignment failure, embedding failure and rejected.
Keep unavailable metrics null or explicitly unevaluated; do not manufacture values.
Enforce FaceImage ownership under Person in services and database FKs. Disabled Person
or FaceImage entries are excluded from *new* index revisions without deleting history.
The representative face should be an accepted image of the same Person, selected by a
simple documented rule; no separate profile-image domain is required.

### Embedding representation decision for its implementation milestone

The current stack is PostgreSQL 17 with `psycopg`, SQL migrations and no pgvector.
Prefer a PostgreSQL `real[]` (float32) column plus a dimension column and a database
array-length constraint. Validate finite values, dimension, normalization and model
compatibility in the service; test deterministic read/write and index rebuild through
`psycopg` before finalizing the migration. Keep FAISS as the search index. Do not add
pgvector merely to persist vectors or store vectors only in an index artifact.

## State boundaries

| Layer | States / transitions |
|---|---|
| Recognition outcome | Optional `PROCESSING` -> `RECOGNIZED`, `UNKNOWN_PENDING`, `NO_FACE`, `QUALITY_REJECTED`, `MODEL_FAILURE` or `VECTOR_INDEX_UNAVAILABLE`. Technical failure cannot transition to unknown without a fresh successful attempt. |
| Human review | `PENDING` -> `CONFIRMED_UNKNOWN`, `CORRECTED_AS_REGISTERED` or `DISMISSED`. Two reviewers cannot produce different terminal results; retries are idempotent or explicit conflicts. |
| Evidence | `PENDING` -> `READY` or `FAILED`; expose download URL only at `READY`. Video encoding must not block review creation. |
| Index | `BUILDING` -> validated `ACTIVE`, `ERROR`, or later `SUPERSEDED`; never search a partial artifact. Record stale/rebuild-required when PostgreSQL contains newer embeddings. |
| Config | Versioned `DRAFT`, `ACTIVE`, `SUPERSEDED`, `INVALID`; only one active revision per assignment scope, with camera assignment and worker-applied revision reported separately. |

The current verifier's internal `VERIFIED`/`UNKNOWN` states are not the product-level
event or human-review states. A successful multi-frame no-match may create one
`UNKNOWN_PENDING` event/review after configured dwell; it does not create a confirmed
unknown alert. A model or index failure creates a technical outcome and no unknown
review task. Deduplicate pending review by camera, stream session and track, using
retry, dwell and cooldown; a fresh encounter may create a new task.

## Service and transaction boundaries

Keep FastAPI routes thin. Person, enrollment, embedding, index, recognition, review
and evidence services own their respective transactions and external work.

Manual enrollment: validate identity and each decoded image (format, size, pixel
dimensions, exactly one target face, quality, alignment, embedding) before writing
active enrollment. Persist Person, accepted FaceImages and compatible FaceEmbeddings
in a transaction. An invalid file gets a per-file diagnostic; partial multi-image
success is reported honestly. No accepted embedding means the Person is not
recognition-ready. Exact file duplicates may be detected by SHA-256; similar faces
of the same Person are allowed. A strong cross-Person match may warn but must not
automatically merge identities.

Confirm unknown: require a pending eligible review, acquire a concurrency guard,
persist terminal review, feedback and one alert/publication intent atomically or
with an equivalent recoverable transaction. Repeated calls cannot duplicate alerts.
Only committed state is sent over the existing WebSocket; REST remains authoritative.

Correct as registered: require a pending review, active selected Person and captured
evidence; revalidate quality, alignment and embedding with the active compatible
embedder. Transactionally persist the new `UNKNOWN_CORRECTION` FaceImage,
FaceEmbedding, terminal review and feedback. After commit, rebuild/reconcile FAISS.
If indexing fails, retain durable human audit data, mark the index stale or queue
reconciliation, and show `CORRECTION_PERSISTED` separately from
`INDEX_SYNCHRONIZED`. Never claim the new vector is searchable until it is in an
active compatible index. Double-clicks, retries and concurrent operators must not
create duplicate correction images or conflicting resolutions.

For a small gallery, prefer building a complete new FAISS revision from a consistent
PostgreSQL snapshot, validating artifact checksum, vector count and entry mapping,
then atomically swapping the active revision. Keep the old valid model/index pair
until replacement is ready. Re-embedding on embedder upgrades is a background job,
not synchronous work inside a UI request. Normal false-unknown correction adds
data and rebuilds the index; it does not retrain AdaFace.

## API and UI contract

Follow `/api/v1` and existing error conventions. Planned capabilities include:

| Area | Routes or equivalent capabilities |
|---|---|
| Person | List/create/get/update/disable; list supports partial `search` over name and Student ID, status and pagination. Responses include face count and safe representative image URL, never storage paths or vectors. |
| Face Gallery | Multipart upload of one or more faces with per-file accepted/rejected results; list/get/deactivate under Person with ownership checks. No arbitrary filesystem path from client input. |
| Embedding/index operations | Controlled re-embed job; status/rebuild/revisions/activate APIs only where operationally useful. No normal-operator FAISS row mutation endpoint. |
| Unknown Review | List/detail filtered by pending state, camera and time; confirm unknown; correct as Person. Include evidence URLs only when ready, candidate/similarity and review state, never embeddings. |
| History/alerts | Query durable recognition events, review outcomes and confirmed alerts. Reuse the existing event/evidence and WebSocket boundaries. |

Return machine-readable domain errors such as `STUDENT_ID_ALREADY_EXISTS`,
`FACE_QUALITY_REJECTED`, `REVIEW_ALREADY_RESOLVED`, `VECTOR_INDEX_UNAVAILABLE` and
`INCOMPATIBLE_EMBEDDING_MODEL` with appropriate 4xx status. Unexpected failures
remain 500. Do not collapse all errors into a generic failure.

Face Registry UI: name/Student-ID search, active/disabled filter, Person list/detail,
multi-image registration with per-image validation states, gallery, add/disable face,
edit/disable Person. Recognition readiness is separate from Person creation. If
registration with zero valid faces is allowed, label it not enrolled; never present
an active recognizable identity with zero compatible embeddings.

Unknown Verification UI: pending list with camera/time/status filters and safe
face/snapshot evidence, candidate and similarity when present. The correction dialog
shows captured and representative faces, supports partial name/Student-ID search,
requires explicit Person selection and confirmation, then displays enrollment and
index synchronization outcomes separately. Another operator's resolution updates
the list through the existing WebSocket and stale actions receive a server conflict.
Alert UI shows confirmed unknown only. History distinguishes recognized, pending,
confirmed, corrected, quality rejected and model failure states.

## Runtime, configuration and evidence

Worker status should expose each model deployment ID/version/checksum, resolved
backend, load and health state. Atomic hot reload validates and warms a replacement
before swap; failure keeps the previous working adapter. Pair active Face Embedder
with a compatible active index revision; otherwise overall readiness is
`NOT_READY / INCOMPATIBLE_INDEX`. Readiness covers camera, worker, tracker, three
models, index and active recognition config. `ACTIVE` in the control plane does not
prove the worker loaded a model or the pipeline is ready.

Persist FaceRecognitionConfig revisions and assign them to cameras. Normal managed
worker mode obtains camera secret, active config, deployments and compatible index
from the control plane; `--feature-config` remains a development option. Apply a
new revision atomically and retain the last valid one if reload fails.

Store enrollment FaceImages and event/review evidence under separate retention
rules through opaque storage references. Validate actual decoded image content,
size, pixel dimensions and file count; generate internal filenames. Prevent path
traversal, collisions and path leakage. Evidence metadata has type, reference,
status, timestamps/error and optional expiry. Clips may use bounded pre/post frame
history later; do not block review on clip creation. Local storage is acceptable
initially and can be replaced with S3/MinIO through the storage abstraction.

## Migration and acceptance rules

Use the repository's SQL migration mechanism; test upgrade, application startup,
constraints, foreign keys, Person/FaceImage ownership, review uniqueness and
transaction rollback. Do not call ad hoc database edits a complete migration.
Use synthetic identities, vectors and image metadata in automated tests; do not
commit personal biometric data. Real E2E acceptance uses explicitly authorized
subjects/artifacts and records model filename, SHA-256, tensor contract and runtime.

Required future tests cover Person CRUD and unique Student ID; full/partial search;
multiple face uploads and diagnostics; gallery/deactivation; compatible index
mapping/rebuild and model migration; multi-frame unknown creation and dedup;
confirm/correct idempotency and competing reviewers; persistence committed before
WebSocket delivery; correction followed by real index retrieval; technical model
failure producing no unknown review. UI tests cover search, registration, review,
correction selection, conflicts and index status. Actual MVP completion requires
real CPU YOLO, BoT-SORT, SCRFD, quality, alignment, AdaFace, enrollment, FAISS,
verification, review, alert, evidence and RTSP execution with no mocks in the
claimed E2E path.

Metrics are based on actual runtime and have bounded labels: attempts, recognized,
unknown pending, confirmed, corrected, quality rejected, model/index failures and
latency. `CORRECTED_AS_REGISTERED / resolved UNKNOWN_PENDING reviews` is a
review-workflow correction rate for a declared time window, not the overall model
false-negative rate. Threshold changes remain versioned config changes, never an
automatic reaction to a few corrections.
