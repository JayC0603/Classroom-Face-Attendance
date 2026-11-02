-- Legacy cameras remain RTSP; webcam frames come from a live browser session.
CREATE TABLE IF NOT EXISTS camera_source_settings (
    camera_id text PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
    source_type text NOT NULL CHECK (source_type IN ('rtsp','webcam'))
);
