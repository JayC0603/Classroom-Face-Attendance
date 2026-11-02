-- Existing rows are deliberately sent to manual review: `active` historically
-- describes registry activity and has no proven reviewer-approval semantics.
ALTER TABLE fr_face_images
 ADD COLUMN IF NOT EXISTS review_state text NOT NULL DEFAULT 'PENDING_REVIEW';
ALTER TABLE fr_face_images
 ADD COLUMN IF NOT EXISTS reviewed_by text REFERENCES auth_users(id);
ALTER TABLE fr_face_images ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
ALTER TABLE fr_face_images DROP CONSTRAINT IF EXISTS fr_face_images_review_state_check;
ALTER TABLE fr_face_images ADD CONSTRAINT fr_face_images_review_state_check
 CHECK (review_state IN ('PENDING_REVIEW','APPROVED','REJECTED'));
CREATE INDEX IF NOT EXISTS fr_face_images_review_pending
 ON fr_face_images(created_at,id) WHERE review_state='PENDING_REVIEW';

CREATE TABLE IF NOT EXISTS fr_face_image_review_audit (
 id bigserial PRIMARY KEY,
 reviewer_id text NOT NULL REFERENCES auth_users(id),
 person_id text NOT NULL REFERENCES fr_persons(id),
 image_id text NOT NULL REFERENCES fr_face_images(id),
 decision text NOT NULL CHECK (decision IN ('APPROVED','REJECTED')),
 reviewed_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS fr_face_review_audit_image
 ON fr_face_image_review_audit(image_id,reviewed_at);
