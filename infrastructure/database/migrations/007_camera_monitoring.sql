CREATE TABLE IF NOT EXISTS camera_monitoring_settings (
    camera_id text PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
    display_fps double precision NOT NULL DEFAULT 15 CHECK (display_fps > 0 AND display_fps <= 30),
    roi_polygon jsonb NOT NULL DEFAULT '[]'::jsonb,
    attendance_enabled boolean NOT NULL DEFAULT true,
    evidence_retention_days integer NOT NULL DEFAULT 30 CHECK (evidence_retention_days BETWEEN 1 AND 3650)
);
