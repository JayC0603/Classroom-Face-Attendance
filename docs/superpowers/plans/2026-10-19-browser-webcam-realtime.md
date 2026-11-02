# Browser webcam realtime implementation

User selected webcam of the browser rather than a device on the worker host.
Execute inline in the shared checkout, preserving existing unrelated changes.

## Design and interfaces

- Camera source is rtsp (default for existing rows) or webcam (browser).
  A webcam has no RTSP URL and no worker device index. Switching source creates
  a new draft revision and requires activation, like existing camera edits.
- Browser explicitly grants getUserMedia access, creates a short-lived stream
  session, sends bounded JPEG frames at at most configured FPS (max 10), and
  closes the session/tracks on stop/unmount/source or revision changes.
- One upload in flight per browser; no accumulated frame queue. One latest frame
  per server session, maximum 16 concurrent sessions, 10-second idle expiry.
  JPEG payload limit 1 MiB, maximum 1280 pixels on either axis and 921600 pixels.
- Session IDs/tokens stay in memory; frame tokens use headers, never URLs.
  Camera must be ACTIVE/enabled/webcam and session must match its revision.
  No images are persisted as enrollment/evidence by the realtime path.
- POST/DELETE `/api/v1/cameras/{id}/webcam/session` starts/stops a session;
  POST `.../webcam/frames` accepts raw bounded JPEG and a session-token header.
  GET `/api/v1/internal/cameras/{id}/webcam/frame` requires the worker token and
  returns the latest JPEG plus sequence/session/timestamp headers, or 204.
- Active-camera delivery includes webcam only while its browser session is live.
  Session ID participates in the worker runtime revision, clearing old tracks.
- HttpBrowserCameraBackend implements the existing frame-source interface.
  Source selection is shared with managed worker startup; RTSP still uses Gst.
- With models assigned, frames enter existing detection/tracking/face stages.
  Without Person Detector, capture-only runtime reports received frames/FPS,
  processed_frames=0 and PERSON_DETECTOR_NOT_CONFIGURED. No stubs or fake matches.
- UI exposes Source selection when creating/editing cameras and a webcam live
  control for activated webcam cameras. The preview is local browser video;
  processing state/FPS comes from backend/worker status, never inferred from preview.

## Tasks and checks

1. Extend camera models/persistence/UI source selection with backward compatibility;
   test create, edit, activation and safe RTSP credential handling.
2. Add bounded ephemeral browser-session/frame service and endpoints; test ownership,
   expiry/revision invalidation, limits, latest-only behavior and internal authorization.
3. Add HTTP frame backend/source routing and capture-only lifecycle; test RGB frames,
   session isolation, shutdown and real pipeline plumbing with controlled inputs.
4. Add browser realtime controller and client; test media cleanup, stop/unmount races,
   one in-flight request, source option and camera API integration.
5. Run Python/frontend suites, lint/build/type checks and isolated PostgreSQL roundtrip.
   Review, start a fresh preview API/worker, and verify synthetic streamed JPEGs.
   Real model accuracy/physical browser webcam remain operator-supplied acceptance.

## Ledger

- Initial optional-question default was worker-local webcam. User answered browser
  webcam; discarded provisional OpenCV source test and device-index contract before
  delivery. Keep the reusable rtsp/webcam configuration work, adapting its semantics.

## Completion evidence and decisions

- Implemented selectable source on create/edit; browser-session/frame endpoints,
  bounded JPEG validation and latest-only storage; authenticated worker delivery;
  RGB HTTP source and capture-only/model-backed runtime routing; browser start/stop,
  media cleanup and one-in-flight upload loop. RTSP configuration remains compatible.
- Independent review findings fixed with regression tests: stale revision requests
  cannot delete replacement sessions; telemetry network failures no longer stop
  capture/inference; concurrent metadata operations cannot mix revisions/source types.
- Live old services held read transactions. An ALTER TABLE startup statement blocked;
  cancelled only our exact pending statement. Changed migration 005 to a separate
  camera_source_settings table and left legacy camera rows/schema intact. An actual
  PostgreSQL test holds a reader and enforces 200 ms startup lock timeout: old ALTER
  failed, new schema passed. Repository transactions serialize reads/two-table writes
  and commit/roll back as a unit.
- Python suite: 218 passed, 5 skipped (four opt-in PostgreSQL checks and real model
  acceptance). All four opt-in PostgreSQL checks passed separately. Frontend: 16 tests
  passed; production build passed. Targeted Ruff and mypy checks passed.
- Live API/proxy/managed worker test used synthetic JPEGs: worker received frames,
  reported actual FPS/session ID and PERSON_DETECTOR_NOT_CONFIGURED with zero
  processed frames, and stopped when the browser session closed. No frame/enrollment
  artifacts were persisted. Physical camera/model inference was not claimed.
- Created an ACTIVE camera named Webcam trình duyệt for immediate user start. No
  webcam session is active. Actual webcam permission is requested only in the browser.
- Preview http://127.0.0.1:5174 now targets API http://127.0.0.1:8005 and the managed
  browser worker. API/worker run in tool-managed terminal sessions (38463, 67191);
  older services were preserved. Background start script was policy-blocked; the
  directly observable terminal execution succeeded.

## Approved extension: three selectable sources

- User explicitly requested RTSP, webcam on the worker host, and browser webcam.
  Added `local_webcam` with a strict integer device index (0..63, default 0).
  Create/edit/activation use the existing revision lifecycle. Browser webcam
  sessions remain required only for the browser source.
- Worker owns OpenCV capture on a dedicated reader, converts BGR to raw RGB,
  bounds delivery to the latest frame, and releases the device on stop/revision
  replacement. Open/shutdown waits are bounded; a stuck native driver produces
  a categorical timeout instead of claiming successful release.
- Added `camera_source_configs` rather than altering tables held by old readers.
  Startup backfills legacy browser settings idempotently; the new table stores
  local device indexes atomically with camera revisions. Migration 006 matches it.
- Eight concurrent shutdown calls reproduced an AttributeError. A lifecycle lock
  and stable reader reference fixed it; independent review confirmed the fix.
  Failed capture retains ERROR status until explicit stop/replacement.
- Isolated PostgreSQL checks: all four pass, including legacy backfill, restart,
  all three sources, and local device index persistence. Frontend: 20 tests pass
  and production build succeeds. Final Python suite: 225 pass, 5 skip (the four
  PostgreSQL checks above and supplied-model acceptance). Targeted Ruff and mypy
  pass. Physical webcam/model accuracy are not asserted.
- Current API is port 8006 (terminal 74118), managed worker terminal 59037.
  Both Vite proxies (5173/5174) now target 8006. A UTF-8 BOM in the local env file
  prevented Vite from reading the override; writing it without BOM fixed the
  observed 5173 stale-API response. Live draft-only smoke verifies three source
  transitions through both proxies and PostgreSQL, then deletes only its test row.

## Worker camera preview follow-up

- User requested that direct worker camera images appear on the UI as well as FPS.
  Added a backend preview adapter shared by direct-webcam and RTSP sources.
- Worker encodes a bounded latest RGB frame on a separate thread, at most five
  times per second; transient JPEGs are capped at 640x480 and 256 KiB. Telemetry
  network/encoding failures do not block capture/inference. Source shutdown closes
  the preview publisher; browser input retains its existing local video preview.
- Worker-token protected upload and revision-scoped snapshot endpoint hold at most
  16 latest previews, expire images after three seconds, and reject inactive/draft
  cameras. No image persistence. Single API process remains required.
- UI automatically shows active worker-camera images below the controls, with
  waiting/reconnecting states. Polling has one request in flight, cancellation,
  request timeout, hidden-tab suspension and object URL cleanup on replacement,
  revision/source changes, disable and unmount.
- Final Python suite: 228 passed, 5 skipped. Frontend: 22 passed; build passed.
  Targeted mypy and Ruff pass. Independent review found no material preview bug.
- Reloaded API 8006 in terminal 19804 and worker in terminal 18477; Vite HMR
  delivers the new preview component to 5173/5174.
- Live acceptance: existing camera was DRAFT on device 0; activated it for the
  requested preview. Worker reports CONNECTED with measured input about 7.9 FPS.
  GET through the 5173 proxy returned a real 6,831-byte JPEG (640x360). No image
  was saved or emitted. Processing remains unconfigured, independent of preview.
