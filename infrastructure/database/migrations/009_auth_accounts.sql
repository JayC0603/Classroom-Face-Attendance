CREATE TABLE IF NOT EXISTS auth_users (
    id text PRIMARY KEY,
    email text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('ADMIN','TEACHER','STUDENT')),
    person_id text UNIQUE REFERENCES fr_persons(id),
    disabled boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((role = 'STUDENT' AND person_id IS NOT NULL) OR (role <> 'STUDENT' AND person_id IS NULL))
);
CREATE INDEX IF NOT EXISTS auth_users_role_idx ON auth_users(role, email);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash text PRIMARY KEY,
    user_id text NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    csrf_secret_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions(user_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS auth_sessions_expiry_idx ON auth_sessions(expires_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS auth_login_attempts (
    attempt_id text NOT NULL,
    digest text NOT NULL,
    attempted_at timestamptz NOT NULL DEFAULT now(),
    is_failure boolean NOT NULL DEFAULT false,
    PRIMARY KEY (attempt_id, digest)
);
CREATE INDEX IF NOT EXISTS auth_login_attempts_window_idx ON auth_login_attempts(digest, attempted_at DESC);

CREATE TABLE IF NOT EXISTS auth_security_audit (
    id text PRIMARY KEY,
    actor_user_id text REFERENCES auth_users(id) ON DELETE SET NULL,
    action text NOT NULL,
    target_user_id text REFERENCES auth_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
