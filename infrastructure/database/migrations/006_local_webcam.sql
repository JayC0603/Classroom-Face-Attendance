-- New source configuration avoids ALTER locks against existing API readers.
CREATE TABLE IF NOT EXISTS camera_source_configs (
    camera_id text PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
    source_type text NOT NULL CHECK (source_type IN ('rtsp','local_webcam','webcam')),
    device_index integer CHECK (device_index BETWEEN 0 AND 63),
    CHECK ((source_type='local_webcam' AND device_index IS NOT NULL)
        OR (source_type<>'local_webcam' AND device_index IS NULL))
);
INSERT INTO camera_source_configs (camera_id,source_type)
 SELECT camera_id,source_type FROM camera_source_settings ON CONFLICT DO NOTHING;
