# Role-Based Face Attendance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a secured FPT-branded attendance workspace with Admin, Teacher, and Student experiences, class-aware attendance, and reviewed face enrollment.

**Architecture:** Preserve the FastAPI modular monolith, PostgreSQL/MinIO persistence, worker-token boundary, and React feature-slice UI. Add server-authoritative cookie sessions/RBAC, class/session projections over immutable verified events, an explicit enrollment review gate, then role-aware pages and semantic light/dark tokens.

**Tech Stack:** Python 3.11+, FastAPI, psycopg, PostgreSQL SQL migrations, MinIO, React 19, TypeScript, Vite, Vitest/Testing Library, CSS variables.

**Spec:** `docs/superpowers/specs/2026-10-19-role-based-face-attendance-design.md`

## Global Constraints

- Browser sensitive routes fail closed; `/health` stays public; worker-only routes use the separate internal token.
- Never store raw passwords, session cookies, CSRF tokens, biometric representations, or image bytes in logs or audit metadata.
- Student identity comes from the authenticated account link; self-service endpoints never accept a person ID selector.
- Only explicitly approved face images may enter comparison; frontend state is never authoritative for approval or identity.
- Existing daily attendance remains once per person per Vietnam-local day; per-class attendance is computed from verified events, roster, camera, and session time.
- Keep video stable and honor `prefers-reduced-motion`; never use a theme transition on camera media.

## Review Focus

- Auth bypass through direct route, media, export, websocket, or stale session: add exhaustive anonymous/role endpoint matrix tests in Task 2.
- Cross-class or cross-student IDOR through IDs/filters: assert 403/404 and unchanged data in Tasks 3 and 4.
- Pending student face sample entering comparison via old active-person cache: test before/after approval and cache refresh in Task 4.
- Recognition from a track outside class camera/time/roster: test session projection boundaries in Task 3.
- Theme persistence, contrast, mobile overflow, reduced motion, and camera-frame stability: test and inspect in Task 6.

---

### Task 1: Map current endpoint contracts and protect worker boundary

**Files:**
- Create: `docs/security/role-route-matrix.md`
- Modify: `apps/control-api/src/control_api/main.py`
- Modify: `workers/vision-worker/src/vision_worker/runtime/status_publisher.py`, `workers/vision-worker/src/vision_worker/main.py`
- Test: `apps/control-api/tests/test_auth_route_matrix.py`, `workers/vision-worker/tests/test_runtime_runner.py`

**Interfaces:**
- Consumes: Existing API router inclusion and `FR_WORKER_INTERNAL_TOKEN`.
- Produces: A route inventory with public, browser-user, admin, teacher, student, and worker classes; API and worker publisher enforce the existing internal-token contract for runtime status/WebSocket paths.

- [x] **Step 1: Write failing regression tests** for `PUT /api/v1/runtime/status` and `/api/v1/ws` requiring worker authentication and for the worker status publisher sending the token; enumerate sensitive API prefixes that must later require browser sessions.
- [x] **Step 2: Run `python -m pytest apps/control-api/tests/test_auth_route_matrix.py -q`** and confirm the unsafe worker routes fail the assertions.
- [x] **Step 3: Enforce the internal worker token** on runtime status writes and authenticate the runtime WebSocket; document every current browser endpoint in `docs/security/role-route-matrix.md`.
- [x] **Step 4: Run the focused test and existing runtime/WebSocket API tests**; all pass without changing worker polling contract. Task 2 replaces browser WebSocket token auth with cookie-session auth because native browser WebSocket cannot set custom headers.

### Task 2: Add PostgreSQL accounts, sessions, login lifecycle, and server RBAC

**Files:**
- Create: `infrastructure/database/migrations/009_auth_accounts.sql`
- Create: `apps/control-api/src/control_api/auth_domain.py`
- Create: `apps/control-api/src/control_api/auth_postgres.py`
- Create: `apps/control-api/src/control_api/auth_api.py`
- Create: `apps/control-api/src/control_api/auth_cli.py`
- Modify: `apps/control-api/src/control_api/main.py` and sensitive API routers under `apps/control-api/src/control_api/`
- Test: `apps/control-api/tests/test_auth_service.py`, `apps/control-api/tests/test_auth_api.py`, `apps/control-api/tests/test_auth_route_matrix.py`

**Interfaces:**
- Consumes: Route matrix from Task 1; repository uses DSN from `FR_POSTGRES_DSN`.
- Produces: `Principal(user_id, role, person_id)`, `AuthService.login/logout/current/change_password`, `require_roles(...)`, cookie session, CSRF dependency, admin account APIs, and first-admin CLI.

- [x] **Step 1: Write tests** for password verification, generic invalid-login response, session rotation/revocation/expiry, one-time admin creation, role denials, CSRF/origin enforcement, and rate-limits.
- [x] **Step 2: Run the auth tests and record expected failures.**
- [x] **Step 3: Implement versioned scrypt password hashes and random opaque session/CSRF secrets**; persist only secret hashes; add PostgreSQL users/sessions/audit tables and transactional repository methods.
- [x] **Step 4: Add login/logout/current/change-password endpoints and the explicit CLI**; bootstrap never creates a default credential. Cookie attributes are HttpOnly, SameSite=Strict, and Secure unless configured for local HTTP.
- [x] **Step 5: Apply role/owner dependencies** to every route/media/export/websocket entry in the route matrix; preserve the worker token as a distinct principal; keep health public.
- [x] **Step 6: Run focused auth, route, face, camera, model, history, and browser-stream tests.** Focused auth passed 24 tests; the complete Control API suite passed with integration tests skipped where infrastructure was not active; mypy passed on the four auth modules.

### Task 3: Add classes, assignments, sessions, and teacher-scoped attendance

**Files:**
- Create: `infrastructure/database/migrations/010_classrooms_sessions.sql`
- Create: `apps/control-api/src/control_api/class_domain.py`
- Create: `apps/control-api/src/control_api/class_postgres.py`
- Create: `apps/control-api/src/control_api/class_api.py`
- Modify: `apps/control-api/src/control_api/main.py`
- Test: `apps/control-api/tests/test_class_api.py`, `apps/control-api/tests/test_class_attendance.py`

**Interfaces:**
- Consumes: Principal and authorization helpers from Task 2; immutable recognition event store.
- Produces: Admin class/assignment/roster APIs, teacher-owned class/session APIs, and a bounded session attendance projection returning `PRESENT`, `ABSENT`, `UNKNOWN_SYSTEM`, source event/time/camera, and no raw storage key.

- [x] **Step 1: Write tests** for admin and teacher CRUD, cross-class denial, membership checks, camera access, session time-zone normalization, event projection boundaries, evidence authorization, and empty/system-unavailable distinction.
- [x] **Step 2: Run the class tests and confirm failures.**
- [x] **Step 3: Implement classes, teacher assignments, student memberships, and session migrations/repository/service** with database constraints and audit events.
- [x] **Step 4: Implement session reports/export** from verified events matching roster person, selected camera, and UTC interval; keep existing daily attendance semantics untouched.
- [x] **Step 5: Verify focused API tests and PostgreSQL migration/integration tests.** 26 focused tests passed; two isolated PostgreSQL class/event tests passed against the local database; full Control API suite passed. Broader MinIO restart integration remains opt-in.

### Task 4: Add student self-service and explicit face-enrollment review

**Files:**
- Create: `infrastructure/database/migrations/011_face_enrollment_review.sql`
- Create: `apps/control-api/src/control_api/student_api.py`
- Modify: `apps/control-api/src/control_api/face_registry.py`, `face_registry_api.py`, `face_registry_postgres.py`, `simple_face_recognition.py`, `main.py`
- Test: `apps/control-api/tests/test_student_api.py`, `test_enrollment_review.py`, `test_simple_face_recognition.py`

**Interfaces:**
- Consumes: Principal, class membership/assigned reviewer services, and existing image storage.
- Produces: `/api/v1/me/profile`, own attendance/events/image routes; teacher/admin pending-review decision endpoint; reviewed images only in comparison gallery.

- [x] **Step 1: Write tests** proving a student's arbitrary person IDs are ignored/rejected, other users' image/evidence bytes are denied, pending/rejected samples cannot match, approval enables match only after cache refresh, and review audit contains no biometric payload.
- [x] **Step 2: Run the focused tests and confirm failures.**
- [x] **Step 3: Persist review state and reviewer decisions** with owner/assigned-class/admin authorization; use the existing MinIO image store and never expose storage keys.
- [x] **Step 4: Update comparison reference selection** to require backend-approved samples and refresh on committed review transition; preserve existing admin registry behavior through explicit admin approval migration/backfill policy.
- [x] **Step 5: Add self-scoped profile, images, event, and attendance endpoints** deriving person ID exclusively from Principal.
- [x] **Step 6: Run comparison, registry, privacy, auth, and PostgreSQL integration tests.**

### Task 5: Build auth gate and role-specific React workspaces

**Files:**
- Create: `apps/web/src/api/auth.ts`, `apps/web/src/api/classes.ts`
- Create: `apps/web/src/features/auth/LoginPage.tsx`, `SessionProvider.tsx`
- Create: `apps/web/src/features/classes/TeacherWorkspace.tsx`, `ClassAttendance.tsx`
- Create: `apps/web/src/features/student/StudentWorkspace.tsx`, `MyFaceEnrollment.tsx`, `MyAttendance.tsx`
- Create: `apps/web/src/features/admin/AccountManagement.tsx`, `ClassManagement.tsx`
- Modify: `apps/web/src/app/App.tsx`, `Sidebar.tsx`, and `apps/web/src/api/client.ts`
- Test: `apps/web/src/features/auth/*.test.tsx`, `classes/*.test.tsx`, `student/*.test.tsx`, `admin/*.test.tsx`, `apps/web/src/app/App.test.tsx`

**Interfaces:**
- Consumes: `/auth/*`, account, class, session, `/me/*`, and review contracts from Tasks 2–4.
- Produces: Session-gated route rendering, role-derived navigation, class/task pages, access-denied and expired-session recovery.

- [x] **Step 1: Write UI tests** for login/logout, expiry, role-specific nav/deep-link denial, account/class CRUD, student own-only attendance/enrollment states, teacher class/session/review workflows, and API error recovery.
- [x] **Step 2: Run the frontend tests and confirm failures.**
- [x] **Step 3: Add typed auth client/provider and session gate**; keep session tokens HttpOnly and CSRF token in memory only.
- [x] **Step 4: Add admin, teacher, and student feature pages** that render backend states faithfully; keep forms/page composition separate from API clients.
- [x] **Step 5: Preserve existing admin camera/model/history experiences** behind authenticated admin navigation and route authorization.
- [x] **Step 6: Run all frontend tests, typecheck, and production build.**

### Task 6: Apply official FPT identity, dark mode, purposeful motion, and responsive QA

**Files:**
- Create: `apps/web/src/assets/fpt-university-logo.png`
- Create: `apps/web/src/ui/ThemeProvider.tsx`, `ThemeToggle.tsx`
- Modify: `apps/web/src/styles.css`, `apps/web/src/app/App.tsx`, `Sidebar.tsx`
- Test: theme and shell tests; visual artifacts under `.test-tmp/`

**Interfaces:**
- Consumes: Authenticated role shell and feature pages from Task 5.
- Produces: Official FPT logo, semantic light/dark color tokens, persisted theme choice, reduced-motion-safe page transitions, and final visual verification.

- [x] **Step 1: Verify and retrieve the official FPT Education/FPT University logo** from the official brand download page; preserve its proportions and record source/provenance in asset documentation.
- [x] **Step 2: Write theme tests** for OS preference before explicit choice, persisted toggle, keyboard labels, reduced-motion setting, and transition not applied to live video.
- [x] **Step 3: Implement semantic theme tokens and toggle** without introducing a component framework or CSS dependency.
- [x] **Step 4: Add short route/action transitions** with a `prefers-reduced-motion` fallback; use orange as a measured identity accent and preserve readable contrast.
- [x] **Step 5: Inspect sign-in, admin camera, teacher attendance, student enrollment on desktop and 390px mobile**; fix overflows and inaccessible focus states.
- [x] **Step 6: Run `npm run test -- --maxWorkers=1`, `npm run build`, full API/worker tests, Ruff, mypy, and PostgreSQL/MinIO integration checks; document unavailable GPU/camera acceptance separately.**

### Task 7: Run security and end-to-end acceptance; document operations

**Files:**
- Create: `docs/security/role-based-attendance-operations.md`
- Modify: `README.md`, `infrastructure/local/README.md`
- Test: `apps/control-api/tests/integration/`, frontend end-to-end smoke tests

**Interfaces:**
- Consumes: All prior tasks.
- Produces: Setup/backup/admin bootstrap procedure, complete route matrix, tested system acceptance and explicit hardware-dependent status.

- [x] **Step 1: Add isolated PostgreSQL/MinIO integration tests** for first-admin setup, account/class provisioning, student image pending/review, class attendance projection, and access denial.
- [x] **Step 2: Add an end-to-end API/UI smoke path** covering Admin → create Teacher/Student → assign class → Student enrolls → Teacher approves → verified event → Student/Teacher see scoped attendance.
- [x] **Step 3: Document migration, CLI bootstrap, session/cookie configuration, account recovery, local launch, backup/restore, privacy retention, and test commands.**
- [x] **Step 4: Run full verification**: `python -m pytest apps/control-api/tests workers/vision-worker/tests -q`; `python -m ruff check .`; `python -m mypy apps/control-api/src workers/vision-worker/src packages/python`; from `apps/web`, `npm run test -- --maxWorkers=1` and `npm run build`; run PostgreSQL/MinIO integration and browser desktop/mobile smoke.
- [x] **Step 5: Record the evidence and any unavailable hardware-dependent validation** without claiming GPU/camera accuracy from mocked checks.
