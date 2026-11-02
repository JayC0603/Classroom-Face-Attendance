ALTER TABLE camera_monitoring_settings ADD COLUMN IF NOT EXISTS unknown_alert_frames
    integer NOT NULL DEFAULT 5 CHECK(unknown_alert_frames BETWEEN 2 AND 60);

CREATE TABLE IF NOT EXISTS recognition_face_events (
    id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('FACE_CAPTURED','UNKNOWN_ALERT')),
    camera_id TEXT NOT NULL, camera_name TEXT NOT NULL, session_id TEXT NOT NULL,
    track_id BIGINT, frame_id TEXT NOT NULL, recognized_at TEXT NOT NULL,
    consecutive_unknown INTEGER NOT NULL,
    evidence_status TEXT NOT NULL CHECK(evidence_status IN ('PENDING','READY','FAILED','EXPIRED')),
    evidence_key TEXT, evidence_error TEXT, evidence_expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recognition_face_events_time_idx
    ON recognition_face_events(recognized_at DESC);
