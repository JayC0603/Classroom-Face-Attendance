# API route and authorization matrix

This matrix describes the routes assembled by `control_api.main.create_app()` and their authorization boundary after Task 4. New routes under `/api/v1/` are protected by the default ADMIN session policy unless they are under `/teacher/`, `/me/`, `/auth/`, or an explicit worker-only path. The test suite introspects the registered HTTP routes and checks anonymous access.

| Route(s) | Current authorization | Planned scope | Purpose |
|---|---|---|---|
| `GET /health` | Public | Public | Service health only. |
| `POST /api/v1/auth/login` | Public, same-origin `Origin` required | Public | Start a browser session; generic login failure and rate limit. |
| `/api/v1/auth/me`, `/logout`, `/change-password` | Valid browser session; mutation also requires same-origin `Origin` and CSRF token | Browser user | Session lifecycle. |
| `/api/v1/admin/accounts` and account disable/reset routes | ADMIN session + CSRF on mutations | Admin | Create and manage accounts. |
| `/api/v1/admin/classrooms/**` | ADMIN session + CSRF on mutations | Admin | Manage classrooms, teacher assignments, and student rosters. |
| `/api/v1/teacher/classes`, `/api/v1/teacher/cameras`, and assigned class session routes | TEACHER session; class ownership is checked by user ID on every request; mutations require CSRF | Assigned teacher only | Schedule/complete sessions, choose from active enabled cameras (ID/name/source only), and view roster attendance. Other class IDs return 404. |
| `/api/v1/face-recognition/configs/validate`, `/api/v1/cameras/validate` | ADMIN session + CSRF | Admin | Configuration validation. |
| `/api/v1/cameras` and `/api/v1/cameras/{camera_id}` (`GET`, `POST`, `PATCH`) | ADMIN session; CSRF on mutations | Admin | Camera inventory and configuration. |
| `/api/v1/cameras/{camera_id}/test-connection`, `/activate`, `/runtime` | ADMIN session; CSRF on controls | Admin for controls; teacher for assigned camera reads | Camera controls and runtime status. |
| `/api/v1/cameras/{camera_id}/webcam/session`, `/webcam/frames`, `/preview` | ADMIN session + CSRF on mutations | Admin for controls; teacher for assigned camera preview | Browser webcam and preview. |
| `/api/v1/persons`, `/api/v1/persons/{person_id}`, face-image and content routes | ADMIN session; CSRF on mutations | Admin; student self-service and assigned teacher review | People and face images. |
| `/api/v1/me/profile`, `/me/face-images`, `/me/face-images/{image_id}/content`, `/me/attendance`, `/me/events` | STUDENT session; image upload also requires CSRF | Student's linked `Principal.person_id` only | Own profile, pending enrollment samples and own attendance/history; person selectors are ignored because these routes accept none. |
| `/api/v1/me/events/{event_id}/evidence` | STUDENT session | Event must belong to the student's linked Person | Private no-store evidence bytes; event ID alone never grants access. |
| `/api/v1/teacher/face-images/pending`, `/teacher/face-images/{image_id}/content`, `/teacher/face-images/{image_id}/review` | TEACHER session; review mutation also requires CSRF | Current teacher assignment and current class membership for the image Person | Assigned teachers review enrollment samples; image bytes are served only through the scoped content route. |
| `/api/v1/admin/face-images/pending`, `/admin/face-images/{image_id}/review` | ADMIN session; decision also requires CSRF | Global | Admin enrollment review queue and decision. |
| `/api/v1/person-models` | ADMIN session; CSRF on uploads | Admin | Person detector model uploads. |
| `/api/v1/models`, versions, deployments, assignments, validation, runtime and readiness | ADMIN session; CSRF on mutations | Admin | Model catalog and deployment controls. |
| `/api/v1/vector-index/*` | ADMIN session; CSRF on mutations | Admin | Vector index operations. |
| Recognition, daily attendance, export and evidence routes under `/api/v1/recognition/*` and `/api/v1/attendance*` | ADMIN session; CSRF on mutations | Admin; future teacher/student scoping is separate from class attendance | Existing recognition history and once-per-Vietnam-day attendance. |
| `GET /api/v1/runtime/status` | ADMIN session | Admin/teacher for scoped reads | Worker health snapshots. |
| `PUT /api/v1/runtime/status` | `X-Worker-Token`; missing or mismatched token is rejected | Worker | Worker status publication. |
| `WS /api/v1/ws` | Same-origin ADMIN session cookie, revalidated every 5 seconds (maximum revocation delay 5 seconds) | Admin; teacher access waits for assigned-camera scoping | Runtime status stream. Browser WebSocket authentication uses the cookie because native browser WebSocket cannot set custom headers. |
| `/api/v1/internal/cameras/*` | `X-Worker-Token`; missing or mismatched token is rejected | Worker | Camera configuration, webcam frame and preview operations. |
| `/api/v1/internal/model-deployments/*`, `/api/v1/internal/model-artifacts/*` | `X-Worker-Token`; missing or mismatched token is rejected | Worker | Model delivery and activation reports. |
| Class session attendance report | TEACHER session; report reads verified events filtered by session camera, UTC interval, and class roster | Assigned teacher only | Verified presence includes event time and camera; unobserved roster members are UNKNOWN_SYSTEM, never inferred absent. The response excludes evidence keys. |
| `/api/v1/teacher/*` | TEACHER session; mutations require CSRF | Teacher assigned to the requested class or currently assigned class membership for face review | Class/session endpoints and scoped face-enrollment review. |
| `/api/v1/me/*` | STUDENT session; mutations require CSRF | Authenticated student's linked Person only | Self-service profile, face enrollment, own attendance/history and owned event evidence. |

Face-image enrollment is `PENDING_REVIEW` by default. The comparison gallery includes only `APPROVED` images. Review uses a transactional compare-and-set from pending and writes an audit record containing reviewer, Person, image, decision and time; it excludes image bytes, face data and storage keys. Migration 011 places existing images into manual review because the legacy `active` flag has no proven approval semantics.

The browser session and worker token are independent credentials. `/internal/**` and worker status writes never accept a browser session as a worker identity. `GET /health` remains public and contains no user data. PostgreSQL authentication fails closed when the persistent auth service is not configured; production also requires a stable `FR_AUTH_RATE_LIMIT_KEY` of at least 32 characters.
