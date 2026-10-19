# Face registration upload

Date: 2026-10-19
Status: proposed written specification; initial in-chat scope approved.

## Intended outcome

The operator can register a person's full name and unique Student ID, upload
multiple JPG/PNG images, and reopen that person's image gallery after an API
restart. The feature appears in the existing Face Recognition page.

The user approved this initial scope in chat. Current runtime has no assigned
face models or active vector index. Saving an image must therefore display
"Đã tải lên — chờ xử lý" and never imply that the person is searchable.

This increment delivers durable identity and image intake. It does not implement
embedding generation, processing jobs, automatic reprocessing, FAISS activation,
camera capture, unknown review, or recognition. These remain separate milestones.
Pending uploads must remain pending even if models are configured later until a
real processing implementation is available. The UI explains this limitation.

## Existing boundaries

Use FastAPI `/api/v1`, PostgreSQL through psycopg, SQL migrations, and local
artifact storage. Keep routes thin, with validation/storage and persistence in
separate services. Extend `App.tsx`, centralized API contracts/client, and a new
`features/registry` slice. Preserve existing camera/model/runtime features and
the many uncommitted changes already in the workspace.

The preliminary `Person` and `FaceEnrollment` dataclasses are not sufficient for
image intake. Do not repurpose a FaceEnrollment as an uploaded image or synthesize
embeddings. This increment adds a distinct persisted Person/FaceImage boundary
consistent with the approved future requirements.

## Operator flow

1. Open "Đăng ký khuôn mặt" within the existing page.
2. Enter full name and Student ID, select up to 10 images, and inspect previews.
3. Submit. Create a person, then upload images under that person. If uploads
   fail, retain the created person and offer retry against that same person.
   Never create another person on a retry of the upload step.
4. Show each file's stored-pending or rejected result with an actionable reason.
5. Select a person from a searchable list to view their gallery or add images.

The person list supports partial full-name/Student-ID search and bounded
pagination. A newly created person with no images is explicitly "Chưa có ảnh".
All stored images in this increment are "Chờ xử lý", not accepted biometric
enrollments. Searchability remains false. No disable/edit/delete controls are
included in this increment.

## Persistence and file lifecycle

Add migration `004_face_registry.sql` using idempotent schema initialization
consistent with existing startup behavior. Person has UUID text ID, required
trimmed full name (1–200 characters), required trimmed Student ID (1–100),
ACTIVE status, and UTC creation/update times. Student ID uniqueness is enforced
case-insensitively in PostgreSQL and in the development repository.

FaceImage has UUID text ID, Person FK, opaque generated artifact reference,
sanitized display filename, MIME, dimensions, SHA-256, MANUAL_UPLOAD source,
PENDING_PROCESSING state, UTC creation time, and active flag. Person ownership
is checked on metadata and image reads. Images never create FaceEmbedding rows.

Use backend-only `FR_FACE_IMAGE_ROOT`, default `data/face-images`. Decode with
Pillow, apply EXIF orientation, and re-encode RGB JPEG to remove uploaded metadata.
Store with generated filenames; client filenames never become storage paths.
Bound each file to 10 MiB, decoded dimensions to 20 million pixels, and each
request to 10 files. Reject animated/multi-frame input and unsupported decoded
formats. Do not trust the extension or client MIME alone. Pillow is a direct API
dependency because uploads must work without the optional CPU extra.

Validate each file before storage. Store its artifact before committing metadata;
on persistence failure roll back the transaction and remove that file. Use unique
artifact names and atomic replacement. Invalid files leave no artifact or active
metadata. A crash between file write and commit may leave an unreferenced file,
but must never create a gallery record pointing at a partial file. No automatic
orphan cleanup or destructive migration is introduced.

When `FR_POSTGRES_DSN` exists, registry metadata must use PostgreSQL. Without it,
use an injectable in-memory repository for development/tests, explicitly showing
that metadata will not survive restart. Do not silently fall back after database
failure. Keep connection/transaction use safe across concurrent API requests.

## API contract

- `GET /api/v1/persons?search=&limit=20&offset=0`: items and total; limit 1–100.
- `POST /api/v1/persons`: full_name and student_id; return 201 or 409
  `STUDENT_ID_ALREADY_EXISTS`.
- `GET /api/v1/persons/{id}`: person metadata or 404.
- `GET /api/v1/persons/{id}/face-images`: owned gallery metadata.
- `POST /api/v1/persons/{id}/face-images`: multipart `files`; return per-file
  results with `STORED_PENDING` or `REJECTED`, image metadata where persisted,
  and stable error category. Partial success is explicit. Unknown person is 404;
  too many files is 422. Technical storage/database failures are not mislabeled
  as bad images and must be reported as failures, preserving earlier committed
  files with explicit per-file diagnostics and retry guidance.
- `GET /api/v1/persons/{id}/face-images/{image_id}/content`: re-encoded image
  content, owned by that person, with private/no-store caching and nosniff.

Public person metadata includes image count and `recognition_ready: false`.
Public image metadata includes an API content URL, dimensions and processing
state; never raw vectors, artifact paths, credentials, or original EXIF metadata.
Return stable upload reasons such as `INVALID_IMAGE`, `UNSUPPORTED_IMAGE_FORMAT`,
`IMAGE_TOO_LARGE`, and `IMAGE_DIMENSIONS_EXCEEDED`. Duplicate-image detection,
authentication changes, and quotas across people are out of scope.

## Frontend behavior

Separate page composition, person list/form, file selection/previews, gallery,
and client/contracts as needed. Reuse the existing visual style with Vietnamese
labels for the new flow. Use typed errors and FormData without a forced JSON
Content-Type. Provide loading, empty, busy, partial-success, and error states.
Disable repeat submissions while busy and preserve identity on upload retry.
Cancel/ignore stale search and gallery requests so changing selections cannot
show another person's images. Revoke preview object URLs when replaced/unmounted.
Do not put images or biometric metadata in localStorage or log upload payloads.
Labels, keyboard controls, alert/status semantics and text states are required.

## Verification and acceptance

Backend tests use synthetic images and identities. Cover unique Student ID,
partial search/pagination, multiple image results, invalid bytes, spoofed MIME,
size/pixel/file-count bounds, ownership, safe filenames, metadata stripping,
pending status, rollback/cleanup, and safe public responses. Test PostgreSQL
migration, uniqueness and restart persistence against the local database with
isolated synthetic test records; do not alter existing model/camera records.

Frontend tests cover creation/upload, preview and gallery, duplicate ID,
per-file rejection, retry without duplicate creation, loading/error states and
the explicit pending/non-searchable status. Run frontend build/tests and Python
suite in an isolated test environment, then restart the API with the existing
environment and verify the live UI/proxy and new API routes. Keep services running
for the user. Do not claim face-quality, embedding or recognition acceptance.

## Specification review

Reviewed for consistency with existing registration requirements, actual missing
model/index capabilities, typed frontend boundaries and safe storage. All saved
uploads remain pending; no model-dependent success is fabricated. Implementation
planning follows user review of this written specification.
