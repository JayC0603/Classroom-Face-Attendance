CREATE TABLE IF NOT EXISTS classrooms (
    id text PRIMARY KEY,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    course text NOT NULL CHECK (length(btrim(course)) > 0),
    section text NOT NULL CHECK (length(btrim(section)) > 0),
    term text NOT NULL CHECK (length(btrim(term)) > 0),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS classrooms_active_name_idx ON classrooms(active, name);

CREATE TABLE IF NOT EXISTS class_teachers (
    classroom_id text NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
    teacher_user_id text NOT NULL REFERENCES auth_users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (classroom_id, teacher_user_id)
);
CREATE INDEX IF NOT EXISTS class_teachers_user_idx ON class_teachers(teacher_user_id, classroom_id);

CREATE TABLE IF NOT EXISTS class_memberships (
    classroom_id text NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
    person_id text NOT NULL REFERENCES fr_persons(id) ON DELETE RESTRICT,
    enrollment_date date,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (classroom_id, person_id)
);
CREATE INDEX IF NOT EXISTS class_memberships_person_idx ON class_memberships(person_id, classroom_id);

CREATE TABLE IF NOT EXISTS class_sessions (
    id text PRIMARY KEY,
    classroom_id text NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
    camera_id text NOT NULL REFERENCES cameras(id) ON DELETE RESTRICT,
    label text NOT NULL CHECK (length(btrim(label)) > 0),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    timezone_id text NOT NULL,
    state text NOT NULL DEFAULT 'SCHEDULED'
      CHECK (state IN ('SCHEDULED','DONE','CANCELLED')),
    done boolean NOT NULL DEFAULT false,
    created_by text NOT NULL REFERENCES auth_users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at),
    CHECK (done = (state = 'DONE'))
);
CREATE INDEX IF NOT EXISTS class_sessions_class_time_idx
    ON class_sessions(classroom_id, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS class_sessions_camera_time_idx
    ON class_sessions(camera_id, starts_at, ends_at);

CREATE INDEX IF NOT EXISTS recognition_events_camera_verified_time_idx
    ON recognition_events(camera_id, recognized_at, person_id);
