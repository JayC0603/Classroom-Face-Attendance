# Face entry snapshots and consecutive unknown alerts

The automatic YuNet/SFace camera comparison now feeds a separate presence policy.
Recognition observations remain independent from attendance and alert persistence.

- `FACE_CAPTURED`: accept one event on the first usable face detected in the ROI
  for a presence, then asynchronously store a crop from that frame. “Entry” here
  means a new face appearing in the configured ROI, not a directional door crossing.
- `UNKNOWN_ALERT`: accept one alert for that presence after
  `camera.unknown_alert_frames` consecutive distinct analyzed frames report
  `UNKNOWN` and the comparison service reports `READY`. The default is **5**;
  camera create/update accepts integers from 2 to 60. Save and activate the camera
  draft to apply a changed threshold.
- Pending, verified, ambiguous, missing-face, reference revision changes and
  technical comparison failures break the streak. Missing/unavailable reference
  images do not qualify as successful no-match searches. Busy inference is retried
  without treating it as a new observation. Polling the same frame cannot add votes.
- Scope includes camera, configuration revision, source kind, person model
  deployment and stream session. Worker person track IDs are matched by containing
  box; without them, face boxes use one-to-one IoU matching. This fallback can lose
  continuity during occlusion or crossing faces; it does not establish identity.
- Face absence breaks the streak immediately on an analyzed frame. Presence state
  expires after 1.5 seconds without a matched face. Each scope holds at most 32
  presences, with at most 16 scopes. A scope/session change or API restart starts
  new temporal state. A continuously present person gets at most one event of each
  kind; after an expired presence, a new appearance can produce another event.

A single API background monitor examines the newest available frames on a 200 ms
tick, processing cameras serially. It works independently from the monitoring
screen. Under load it processes the newest frames rather than buffering every
video frame: the threshold concerns **analyzed** frames, not display FPS. Browser
webcams still require their browser capture session to keep sending frames.
Run one API process, as required by the existing in-memory browser stream adapter.

Events commit before evidence work or response delivery. Each event has an immutable
ID and durable deduplication key. Evidence has its own `PENDING`, `READY`, `FAILED`
or `EXPIRED` status. One background evidence thread owns a queue of at most 32
bounded preview payloads; a full queue records `CAPTURE_QUEUE_FULL` without erasing
the event. Capture/storage errors are terminal `CAPTURE_FAILED` for that artifact.
Interrupted/overdue pending work becomes failed after a 30-second deadline during
startup or the minute cleanup pass. Late writes for failed jobs are discarded.
Shutdown allows five seconds to drain, and defers repository closure if necessary.

PostgreSQL stores `recognition_face_events`, with private JPEG crops in the existing
MinIO evidence bucket. Unconfigured local development uses SQLite and its existing
private evidence directory. Retention follows the camera's evidence retention days;
cleanup runs every minute and when history is read. Evidence bytes are available
only through the existing authenticated admin evidence API, with no-store headers.
Unknown alerts and entry captures never create attendance records.

The UI shows alerts and snapshots returned by the backend. Persisted events can
be queried with `GET /api/v1/recognition/face-events`, optionally filtering by
`camera_id` and `kind`, and appear under **Ảnh vào & cảnh báo** in history. This is
in-app, REST-polled delivery: reconnecting clients can read persisted history and
deduplicate by event ID. There is no external email, Telegram or push integration.

Tests cover threshold boundaries, repeated frames, interrupted streaks, multiple
tracks, session/gallery changes, stale results, background processing without a UI,
worker/browser inputs, crop bytes, storage failure, queue saturation, retention,
database migration, PostgreSQL restart persistence, permissions and UI behavior.
