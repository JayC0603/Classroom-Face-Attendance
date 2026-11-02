# Role-Based Face Attendance Workspace

## Intent and success

Build the existing face-recognition control plane into a usable campus attendance workspace for administrators, teachers, and students. Administrators manage accounts, classes, cameras, and models; teachers manage assigned classes and inspect class attendance; students register their own face images and see only their own attendance and recognition history. Preserve the existing PostgreSQL, MinIO, FastAPI, React, and worker boundaries.

Success means that every sensitive API is protected server-side; a teacher cannot reach another teacher's class; a student cannot read or alter another person's profile, images, events, evidence, or attendance; only explicitly approved enrollment images enter face comparison; class reports are derived from verified events in the configured class session window; users can sign in, sign out, and change their password; and the interface clearly communicates role, resource, loading/error state, and next action.

## Current system

- FastAPI composes APIs from `apps/control-api/src/control_api/main.py`. PostgreSQL migrations are applied from `infrastructure/database/migrations`; MinIO stores face images, models, and evidence.
- The React 19/Vite UI in `apps/web/src` uses hash navigation, page features, a shared stylesheet, and feature-local API clients. There is no authentication, account, role, classroom, or class-session model yet.
- Sensitive person images, camera controls/previews, model controls, recognition history, attendance/export, evidence, and runtime WebSocket routes are public. Internal worker APIs use a separate shared token. `PUT /api/v1/runtime/status` and `/api/v1/ws` also need explicit protection.
- Existing attendance is a once-per-person-per-Vietnamese-day ledger, not a class-session ledger. Teacher class reports must be computed from a class roster, assigned camera, session time range, and immutable verified-recognition events; the existing daily row must not be relabeled as per-class attendance.
- Face-image uploads currently remain pending in the UI but `RegisteredFaceComparison` can include every image for an active person. Student self-enrollment therefore needs a backend-controlled eligibility/review transition before those images become comparison references.

## Architecture

Keep a modular monolith. Add an authentication domain with an opaque random session cookie, password hashes using Python's memory-hard `hashlib.scrypt`, PostgreSQL-backed users/sessions, role and ownership dependencies, and a one-time CLI for the first administrator. Keep worker authentication separate. Require CSRF/Origin validation for cookie-authenticated mutations and authenticate the realtime WebSocket from the same browser session.

Add classrooms, teacher assignments, student memberships, and class sessions with a selected camera and explicit timezone-aware start/end instants. Compute class attendance from verified recognition events whose person is on the class roster and whose camera/time fall inside the session. This report is a projection; the existing attendance ledger retains its daily semantics. Enforce ownership in SQL/service methods as well as routes.

Student accounts link to exactly one existing Person record. Self-enrollment writes images in `PENDING_REVIEW`; only a teacher assigned to that student's class or an administrator can approve/reject. Comparison only includes explicitly approved image references. Responses and logs never expose image storage keys, raw biometric representations, or credentials.

The web UI uses a session gate, role-derived navigation, feature-based route/page components, and central typed auth/class/student API clients. Admin retains camera/model/system controls and gains account/class administration. Teachers get assigned classes, roster, session setup, pending-enrollment review, and class attendance. Students get own profile, enrollment status/upload, and own event/attendance history. The existing visual system becomes FPT Education/FPT University branded using an official asset, with tokenized light/dark themes, persisted theme preference, restrained page/action transitions, keyboard support, responsive layouts, and reduced-motion support.

## Security and privacy requirements

- Fail closed for unconfigured production auth. `/health` may remain public. All browser-accessible sensitive routes and media are session protected. `/internal/**` remains worker-token protected. A route classification table is tested.
- Bootstrap exactly one initial administrator through an explicit management command; no default account or hard-coded password. Creation and reset secrets are supplied interactively and never printed or logged.
- Store only password hashes and hashes of random session/CSRF secrets. Cookies are HttpOnly, SameSite=Strict, and Secure outside explicitly local HTTP development. Invalidate sessions on logout, password change, expiry, or account disable.
- Role and object ownership checks run in API services/queries, never only in React. Validate same-origin mutation requests and CSRF token.
- Student routes derive person ID from the authenticated account link; client-provided person IDs are ignored/rejected for self-service.
- Enrollment images are ineligible for comparison until explicit approval. Students can see their own enrollment lifecycle and request removal; image delivery is authorized for owner or assigned reviewer.
- Recognition event/evidence identity remains backend-authoritative. Reports never equate Track ID with identity or infer a verified event from a detection.
- Audit security-sensitive account, class assignment, enrollment review, and attendance export actions without recording passwords, session tokens, image data, or embeddings.

## Data model and API behavior

`users`: UUID, normalized unique email, display name, password hash/version, role (`ADMIN`, `TEACHER`, `STUDENT`), optional `person_id`, active/force-password-change flags, timestamps. A constraint limits `person_id` to one account and only for students.

`auth_sessions`: session token hash primary key, user ID, CSRF token hash, created/last-seen/expiry/revoked timestamps, user-agent digest, remote-address digest. `audit_log`: actor, action, target kind/id, timestamp, safe structured metadata.

`auth_login_attempts`: normalized-email digest, remote-address digest, attempt time, and result category only; no plaintext email/IP is stored for throttling. Keep only the short rolling window and remove expired entries during authentication operations.

`classrooms`, `class_teachers`, `class_memberships`, and `class_sessions`: class identity; teacher and student relations; session camera, UTC interval, source timezone, and course/section label. Membership and assignment changes are scoped and audited.

`face_enrollment_reviews`: image ID, person ID, status, reviewer, decision time, reason code, and audit metadata. The authoritative image lifecycle is pending, approved, or rejected; approval controls comparison eligibility.

Browser API contract includes `/auth/login`, `/auth/logout`, `/auth/me`, `/auth/password`, admin account/class management, teacher-owned class/session/attendance endpoints, and self-scoped `/me/profile`, `/me/face-images`, `/me/events`, `/me/attendance`. Keep filters bounded and paginate long history. No `/me` route accepts an arbitrary person ID.

## UI and interaction design

- The sign-in page explains the service and offers labeled email/password inputs, password visibility, loading/error recovery, and keyboard/focus support.
- The shared shell displays a user/role menu, theme control, sign-out, and only the navigation allowed for the current role. Deep links trigger server-backed authorization and show an accessible forbidden page.
- Admin navigation groups: Overview, Accounts & classes, Cameras, Models, Recognition history, System. Teacher navigation: My classes, Class attendance, Enrollment review. Student navigation: My face, My attendance, My activity.
- Teacher attendance is a class/session task: choose class, then session/date, see present/absent status with source event/time/camera and filtered evidence, then export. Loading, empty, and error states remain distinct.
- Student face enrollment displays pending/review/approved/rejected state and uploaded sample count; do not imply that upload immediately enrolls the model.
- FPT logo is served locally from an official FPT Education/FPT University brand download. Do not hotlink. Keep aspect ratio and legibility on both themes; do not invent or distort the mark.
- Light and dark themes use semantic CSS variables (surface, on-surface, primary, outline, error, success, warning), persisted in localStorage with OS preference only before a user choice. Animate route content with a brief opacity/translate transition; never animate the camera frame. Honor `prefers-reduced-motion`.

## Failure behavior

- Invalid login returns a generic credential error; rate limits return a retry time without confirming whether an email exists.
- Expired/revoked sessions clear browser auth state and route to sign-in. API errors do not reveal database details.
- Missing PostgreSQL configuration prevents production auth service startup. Local/test behavior is explicit and cannot silently disable auth in a deployed profile.
- A session with missing camera/roster or invalid time range cannot produce attendance. No recognized event means absent/unknown per session policy, with clear distinction between no event and system unavailable.
- Failed enrollment review changes nothing. Comparison cache reloads only after committed approval/rejection and never falls back to unreviewed images.
- Failed upload/export or network operations preserve form drafts and expose safe retry guidance.

## Validation

Backend tests prove route matrix 401/403 behavior, admin bootstrap uniqueness, password/session lifecycle, CSRF, worker-token separation, WebSocket authentication, teacher cross-class denial, student IDOR denial for JSON/images/evidence/export, rate limiting, review lifecycle, comparison exclusion/inclusion, session event projection, bounded pagination/export, and audit redaction. Integration tests run against isolated PostgreSQL and MinIO.

Frontend tests prove login/logout/password expiry, role-specific navigation and deep-link denial, account/class workflows, student face-review status, teacher class/session attendance, evidence denial, theme persistence/system fallback, reduced-motion class behavior, and responsive reachable actions. Build/typecheck and inspect real desktop/mobile screens. Camera/video remains stable across navigation and theme transitions.

## Reference research

- GOV.UK Design System uses documented reusable components and task patterns; its button guidance recommends clear action labels, a single main action, left-aligned primary form action, and sparing use of secondary actions. Adapt these interaction principles, not its visual identity: <https://design-system.service.gov.uk/components/button/>.
- Material Design 3 color roles provide semantic tokens for surfaces, text, outlines, and emphasis across light/dark modes. Use this as the theme architecture, retaining FPT brand orange as a measured accent: <https://m3.material.io/styles/color/the-color-system>.
- FPT Education's official brand page publishes logos for its education units, including FPT University. Use the downloadable official asset and preserve the mark: <https://fpt.edu.vn/brand>.

## Delivery stages

1. Auth contracts, account/session persistence, route protection, worker separation, and security tests.
2. Class/roster/session data and teacher-scoped attendance projection.
3. Student self-service and explicit enrollment review/recognition eligibility.
4. Role-based React shell and task screens.
5. Official brand asset, light/dark tokens, accessible motion, and responsive validation.

The app is considered complete only when all stages above work together and the security and regression checks pass. Hardware-dependent real-camera inference is validated separately from account/class UI.
