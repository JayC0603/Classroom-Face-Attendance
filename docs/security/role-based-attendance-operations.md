# Role-based face attendance operations

## Components and trust boundaries

The React/Vite app talks to the FastAPI Control API. PostgreSQL stores accounts,
sessions, cameras, class assignments, enrollment metadata, and verified-event
metadata. MinIO stores private model files, enrollment photos, and evidence images
when configured. The vision worker receives a separate internal token and does not
use a browser account. The route inventory is in
[role-route-matrix.md](role-route-matrix.md).

Browser authentication uses a random HttpOnly, SameSite=Strict cookie; the API
stores only its hash. Mutations require same-origin checks and a CSRF token held in
browser memory. Sessions expire after 12 hours. Production must use HTTPS so the
session cookie is Secure. Never put an account role, session token, CSRF token,
password, face photo, or embedding in local storage or logs. Theme preference is a
non-sensitive UI setting and may be saved in local storage.

## First administrator and initial setup

1. Start PostgreSQL, Redis, and MinIO using the commands in
   [local development](../../infrastructure/local/README.md). Keep Postgres and
   MinIO private; the compose ports bind to loopback.
2. Replace all sample passwords and internal secrets before handling real data.
   Configure `FR_POSTGRES_DSN`, `FR_CAMERA_ENCRYPTION_KEY`,
   `FR_WORKER_INTERNAL_TOKEN`, `FR_AUTH_RATE_LIMIT_KEY` (32+ characters),
   `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, and `MINIO_SECRET_KEY` in the API process.
   Use unique secrets and keep the rate-limit key stable through restarts.
3. From the repository root, with `FR_POSTGRES_DSN` in the process environment,
   run `python -m control_api.auth_cli`. Enter the first administrator email and
   a strong password interactively. The CLI applies migrations and does not print
   the password. No default admin is created.
4. Start the API and web app as shown in the repository README, then sign in.
5. Create each student Person in **Hồ sơ khuôn mặt**, create the STUDENT account
   linked to that Person ID, create a class, assign one or more TEACHER accounts,
   and add the student Person to the roster. Create an active camera before a
   teacher schedules its class session.
6. Have students submit face photos from their profile. Reviewers approve only
   images whose ownership and quality they have checked. Only approved samples
   are eligible for the recognition comparison gallery.

Admin manages cameras, polygon ROI, models, registry, accounts, classes, and
system health. A teacher sees assigned classes and reviews only current class
members. A student sees only their own profile, images, attendance, events, and
authorized evidence. Attendance rows without a verified observation are reported
as unknown system coverage; they must not be treated as proof of absence.

## Run and verify

From the repository root:

```bash
python -m pytest apps/control-api/tests workers/vision-worker/tests -q
python -m ruff check .
python -m mypy apps/control-api/src workers/vision-worker/src packages/python
```

From `apps/web`:

```bash
npm ci
npm run test -- --maxWorkers=1
npm run typecheck
npm run build
```

PostgreSQL and MinIO tests are opt-in. Set `FR_TEST_POSTGRES_DSN` to a disposable
database and set the `MINIO_*` connection values to disposable private buckets
before running `apps/control-api/tests/test_face_registry_postgres.py`,
`test_class_postgres.py`, or `tests/integration/`. These tests create isolated
schemas and remove their own objects. Do not point tests at school data.

## Privacy and retention

Enrollment images are private and default to `PENDING_REVIEW`. Pending and rejected
images are not comparison references. Student endpoints derive the Person ID from
the signed-in account; teachers are scoped to assignments and current class
membership. Image/evidence routes return private no-store content and do not expose
storage keys. Review audit events store the decision and actor but no image bytes or
biometric values. Face comparison currently uses the configured backend flow; model
accuracy must be accepted separately on consented data.

Each camera can set evidence retention days. The event store removes expired
evidence objects and their corresponding expired event records during its cleanup
cycle. Preserve enrollment-photo retention according to institutional policy; the
current app has no scheduled deletion policy for registry samples. Assign an
operator to review retention requirements and remove records through authorized
administrative procedures before production rollout.

## Backup and recovery

Back up PostgreSQL and all three private MinIO buckets (`models`, `face-images`,
`evidence`) at the same quiet point in time. The exact `pg_dump`, `mc mirror`, and
restore commands are in the local-development guide. Stop API and worker writes
during the matched backup and restore. Keep backups encrypted, access-controlled,
and labeled with a common timestamp. Test restore into an isolated environment;
do not run `docker compose down -v` as a restart command.

An admin can create a second admin from the Admin account screen while an admin is
available. The first-admin CLI intentionally refuses to act as a general password
reset tool. If all administrators are locked out, use the institution's controlled
database recovery process and record the operator action; do not weaken route
authorization or add a default credential.

## Acceptance limits

Automated API/UI tests verify role boundaries, session lifecycle, scoped class
records, enrollment review, and the optional persistence adapters. Synthetic ONNX
fixtures prove tensor-contract plumbing, not model accuracy. This checkout has not
been accepted against a real production camera, a school's enrolled population, or
GPU/TensorRT hardware unless the opt-in supplied-model acceptance test is run with
those exact artifacts. Measure detection quality, face false-match rate, FPS,
latency, attendance policy, and failure recovery on authorized representative
hardware and data before deployment. The live UI offers **Thiết lập vùng ROI** on
each camera card; use it to set the polygon corresponding to the monitored entry.
