-- Extend the existing identity/image domain; preserve legacy index records.
CREATE TABLE IF NOT EXISTS fr_persons (
 id text PRIMARY KEY, full_name text NOT NULL, student_id text UNIQUE, active boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS fr_face_images (
 id text PRIMARY KEY, person_id text NOT NULL REFERENCES fr_persons(id),
 evidence_ref text, active boolean NOT NULL
);
ALTER TABLE fr_persons ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE fr_persons ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
CREATE UNIQUE INDEX IF NOT EXISTS fr_person_student_id_normalized
 ON fr_persons (lower(btrim(student_id))) WHERE student_id IS NOT NULL;
ALTER TABLE fr_face_images ADD COLUMN IF NOT EXISTS storage_key text;
ALTER TABLE fr_face_images ADD COLUMN IF NOT EXISTS original_filename text;
ALTER TABLE fr_face_images ADD COLUMN IF NOT EXISTS mime_type text;
ALTER TABLE fr_face_images ADD COLUMN IF NOT EXISTS width integer;
ALTER TABLE fr_face_images ADD COLUMN IF NOT EXISTS height integer;
ALTER TABLE fr_face_images ADD COLUMN IF NOT EXISTS checksum text;
ALTER TABLE fr_face_images ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE fr_face_images ADD COLUMN IF NOT EXISTS processing_state text
 NOT NULL DEFAULT 'LEGACY_UNPROCESSED';
ALTER TABLE fr_face_images ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS fr_face_images_person ON fr_face_images(person_id);
