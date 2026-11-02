-- Enrollment vectors are authoritative; FAISS files are derived revisions.
CREATE TABLE IF NOT EXISTS fr_persons (
 id text PRIMARY KEY, full_name text NOT NULL, student_id text UNIQUE, active boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS fr_face_images (
 id text PRIMARY KEY, person_id text NOT NULL REFERENCES fr_persons(id),
 evidence_ref text, active boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS fr_face_embeddings (
 id text PRIMARY KEY, face_image_id text NOT NULL REFERENCES fr_face_images(id),
 vector double precision[] NOT NULL, compatibility jsonb NOT NULL, active boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS fr_vector_revisions (
 id text PRIMARY KEY, status text NOT NULL, data jsonb NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS fr_one_active_vector_revision
 ON fr_vector_revisions ((status)) WHERE status = 'ACTIVE';
