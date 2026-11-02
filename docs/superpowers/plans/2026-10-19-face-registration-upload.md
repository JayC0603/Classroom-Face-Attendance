# Face registration upload implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add durable person registration, multiple image upload, and galleries to the running app.
**Architecture:** Extend existing fr_persons/fr_face_images, with thin FastAPI routes, a registry service and storage boundary, PostgreSQL/in-memory repositories, and a typed React registry panel.
**Tech Stack:** FastAPI, psycopg, Pillow, React, TypeScript, pytest, Vitest.
**Spec:** ../specs/2026-10-19-face-registration-upload-design.md

## Global constraints

- All uploaded images remain PENDING_PROCESSING, recognition_ready=false.
- Required trimmed full name 1–200, Student ID 1–100; case-insensitive uniqueness.
- JPG/PNG, maximum 10 MiB/file, 20 million decoded pixels, 10 files/request.
- Generated storage keys, RGB JPEG output, no EXIF, no paths/vectors in responses.
- No changes to existing model/camera data or unrelated work.

## Review focus

- Concurrent duplicate Student IDs must yield a domain conflict and leave connection usable.
- Existing vector repository inserts must remain valid after additive migration.
- Upload retries must use the created person's ID, including partial success.
- A delayed gallery response must not overwrite a newer person selection.
- Persistence/storage failures must clean artifacts and preserve prior per-file results.

## Task 1: Registry API, image storage and persistence

Files: create `apps/control-api/src/control_api/face_registry.py`,
`face_registry_postgres.py`, `face_registry_api.py`, `face_image_storage.py`,
`infrastructure/database/migrations/004_face_registry.sql`,
`apps/control-api/tests/test_face_registry.py`; modify `main.py`, `vector_index.py`, `pyproject.toml`.

Interfaces: `FaceRegistryService(repository, storage)`;
`create_face_registry_router(service)`; `build_face_registry()`.
Person JSON contains id, full_name, student_id, status, image_count, recognition_ready.
Image JSON contains id, person_id, original_filename, mime_type, width, height,
processing_state, created_at, content_url. Internal storage_key never serialized.

- [ ] Write API tests for create/search/duplicate ID, upload mixed images and ownership,
  malformed/oversize/multiframe input, preview metadata stripping and persistence failure cleanup.
- [ ] Run `.venv/Scripts/python.exe -m pytest apps/control-api/tests/test_face_registry.py`; expect missing-feature failure.
- [ ] Implement image validator/storage and repositories with per-operation transactions.
- [ ] Add migration extending existing tables; update legacy vector repository to explicit
  insert columns so old callers remain compatible. Add Pillow to base dependencies.
- [ ] Wire API routes and run new tests to green, then Python suite.

## Task 2: Registration UI

Files: create `apps/web/src/features/registry/FaceRegistryPanel.tsx`,
`ImageUpload.tsx`, `FaceRegistryPanel.test.tsx`, `apps/web/src/api/registry.ts`;
modify `App.tsx`, `styles.css`.

Interfaces: registry client `listPersons(search, offset, signal?)`,
`createPerson(input)`, `listFaceImages(id, signal?)`, `uploadFaceImages(id, files)`.

- [ ] Write tests proving registration then multipart upload, duplicate identity reporting,
  partial success/retry under the same person, loading/error and pending gallery labels.
- [ ] Run Vitest for the new test file, expect missing-component failure.
- [ ] Implement accessible Vietnamese form/list/gallery with previews and object URL cleanup,
  cancellable list/gallery fetches, upload locking and explicit pending status.
- [ ] Run frontend tests and production build to green.

## Task 3: Live integration and delivery

- [ ] Run PostgreSQL tests in an isolated temporary schema: migration twice, case-insensitive
  uniqueness, existing insert compatibility, commit/reopen persistence and rollback.
- [ ] Run full Python and frontend suites, relevant lint/type checks, review changed files.
- [ ] Restart only this workspace's API with existing env, verify new routes, persistent
  upload/content read using synthetic data and Vite proxy. Keep services available.
- [ ] Update README and record validation/results; report URL and actual limitations.

## Execution ledger

Ruling: User's "code đê" explicitly requests execution now; continue inline without another
design or plan approval prompt. Work in this workspace because running services and the
approved existing uncommitted vector/index work depend on it; do not create a clean worktree
that drops those dependencies. No commits containing pre-existing user changes.

Ruling: Extend fr_persons/fr_face_images rather than create duplicate identity tables.
Legacy nullable Student IDs remain valid; new API requires a nonempty unique Student ID.
Legacy images without uploaded artifact metadata are excluded from the upload gallery.
This preserves existing vector/index records and migration compatibility.

## Completion evidence

- Task 1 complete: API/storage tests went from missing module to passing. Added
  decoded-format/size/pixel checks, orientation/metadata stripping, stable public
  states, image ownership, storage cleanup, and stored-artifact SHA-256.
- Task 2 complete: UI tests went from missing component/client to passing. Person
  creation, preview, pending gallery, per-file diagnostics, retry and duplicate ID
  behavior covered. Registration panel is mounted before camera/model settings.
- Task 3 complete: full Python suite 205 passed / 3 skipped; opt-in real PostgreSQL
  tests 2 passed in generated schemas. Frontend 7 passed and production build passed.
  Ruff passes for all new Python sources/tests; mypy passes for all five new modules.
- Independent code review found an ingress spooling issue; fixed with a cumulative
  request-byte limit and parser file-count limit. Re-review found no material issue.
  A direct multi-message ASGI test verifies temporary files close on size rejection.
- Live proxy/API check passed: synthetic person, two stored images plus a rejected
  file, gallery/content reads, pending/non-searchable states, and independent SQL
  verification. Synthetic application data and image artifacts were removed afterward.
- Ruling: command policy rejected stopping the original API. Preserve existing
  sessions and expose the updated preview at http://127.0.0.1:5174 backed by
  http://127.0.0.1:8002 with reload. Preview configuration/PIDs/logs are under the
  ignored `.test-tmp/registry-preview` directory. Both previews use existing PostgreSQL.
- Work remains in the shared checkout for the user; no pre-existing modifications
  were committed or discarded. Actual embedding/recognition remains outside this increment.

## Webcam follow-up (2026-10-19)

User requested a webcam source option in the existing upload flow. Added explicit
camera enable/disable, JPEG capture into upload previews, bounded image selection,
permission/device error states, and media cleanup on reset/unmount/source switch/save.
Late permission and canvas results are discarded safely. Source is sent as a typed
multipart field and persisted as CAMERA_CAPTURE; parser allows one bounded source field.

Validation: 206 Python tests passed / 3 skipped; 2 real PostgreSQL tests passed;
12 frontend tests passed; build, Ruff and targeted mypy passed. Independent review
found no material issue. Live API/proxy smoke verified camera provenance persists
and gallery/content reads succeed; synthetic data was removed. Physical webcam
permission/capture was covered with mocked browser media APIs, not real camera access.

The previous API reload did not take effect, so preview remains on port 5174 and
now targets the fresh API on port 8003. Process/log files remain under
`.test-tmp/registry-preview`; existing sessions are preserved.
