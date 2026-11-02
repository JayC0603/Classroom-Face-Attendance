CREATE TABLE IF NOT EXISTS recognition_events (
    id text PRIMARY KEY,
    camera_id text NOT NULL,
    camera_name text NOT NULL,
    session_id text NOT NULL,
    track_id bigint,
    person_id text NOT NULL,
    full_name text NOT NULL,
    student_id text,
    recognized_at timestamptz NOT NULL,
    attendance_date date NOT NULL,
    evidence_key text,
    evidence_checksum text,
    evidence_expires_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS recognition_events_dedupe_idx
    ON recognition_events(camera_id, session_id, coalesce(track_id, -1), person_id);
CREATE INDEX IF NOT EXISTS recognition_events_history_idx
    ON recognition_events(recognized_at DESC);
CREATE INDEX IF NOT EXISTS recognition_events_camera_day_idx
    ON recognition_events(camera_id, attendance_date DESC);
CREATE INDEX IF NOT EXISTS recognition_events_person_day_idx
    ON recognition_events(person_id, attendance_date DESC);
CREATE INDEX IF NOT EXISTS recognition_events_expiry_idx
    ON recognition_events(evidence_expires_at)
    WHERE evidence_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS attendance (
    id text PRIMARY KEY,
    attendance_date date NOT NULL,
    person_id text NOT NULL,
    full_name text NOT NULL,
    student_id text,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    exited_at timestamptz,
    camera_id text NOT NULL,
    camera_name text NOT NULL,
    event_id text NOT NULL DEFAULT '',
    UNIQUE(attendance_date, person_id)
);
CREATE INDEX IF NOT EXISTS attendance_history_idx
    ON attendance(attendance_date DESC, full_name);
CREATE INDEX IF NOT EXISTS attendance_camera_idx
    ON attendance(camera_id, attendance_date DESC);
