# Local development

PostgreSQL, Redis, and MinIO are available through the local Compose profile. Service
ports bind to loopback only, named volumes preserve data, and services include health
checks. The bucket initialization job creates private `models`, `face-images`, and
`evidence` buckets.

```bash
docker compose --env-file infrastructure/local/.env.example \
  -f infrastructure/local/compose.yaml up -d --wait
```

Generate backend-only local secrets instead of committing values:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

For a private local MinIO instance, set a unique `MINIO_ROOT_USER` and
`MINIO_ROOT_PASSWORD` in an ignored local env file, and use the same values as
`MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` for the API. Keep `MINIO_ENDPOINT` at
`127.0.0.1:59000`; the console is available at `http://127.0.0.1:59001`.
The checked-in `.env.example` values are development placeholders only.

Start or recreate services without deleting their volumes:

```bash
docker compose --env-file infrastructure/local/.env.example \
  -f infrastructure/local/compose.yaml up -d --wait
docker compose --env-file infrastructure/local/.env.example \
  -f infrastructure/local/compose.yaml ps
```

Do not run `down -v` when restarting; it deletes PostgreSQL, Redis, and MinIO data.

The checked-in `.env.example` is for disposable local development only. Before using
these services with real student data, replace every development credential with
unique generated secrets and configure the API with the matching private services.
Do not commit local env files or put credentials in browser storage, screenshots,
or logs.

Set `FR_POSTGRES_DSN`, `FR_CAMERA_ENCRYPTION_KEY`, `FR_WORKER_INTERNAL_TOKEN`,
`FR_AUTH_RATE_LIMIT_KEY`, and the `MINIO_*` connection values in the API process.
Generate `FR_AUTH_RATE_LIMIT_KEY` with `python -c "import secrets; print(secrets.token_urlsafe(48))"`
and keep the same value across API workers and restarts. Store it in an ignored local
API env file; never use the checked-in development placeholder in production. Database migrations under
`infrastructure/database/migrations` are applied before PostgreSQL repositories are
created. When `FR_POSTGRES_DSN` is configured but unavailable, API startup fails; it
does not fall back to volatile memory. The worker receives only the internal token
and control API base URL. RTSP credentials are created and edited in the web UI; they
are never environment variables or command-line arguments.

Authentication also requires PostgreSQL and a stable rate-limit key at least 32
characters long. With `FR_POSTGRES_DSN` configured, API startup applies ordered SQL
migrations and fails closed if the database is unavailable. Bootstrap the first
administrator with `python -m control_api.auth_cli` from the repository root; set
the process `FR_POSTGRES_DSN` first. The interactive command never creates a default
password. In the UI, create each student's Person record, link that person to the
student account, then create the class, assign its teacher, and add the student to
the roster. Enrollment samples remain private until approved.

Start the API and managed worker after Compose is healthy:

```bash
python -m uvicorn control_api.main:app --env-file .env --host 127.0.0.1 --port 8000
python -m vision_worker.main --control-api-url http://127.0.0.1:8000 \
  --feature-config configuration/examples/face-recognition.example.json
```

The model registry migrates verified legacy local model and face-image files to
MinIO at API startup. It checks each stored checksum before updating metadata and
keeps the local source file in place. If a source file is missing or has changed,
startup fails with a typed migration error instead of pointing records at missing
objects.

When PostgreSQL settings are absent, the API reports `camera_persistence=in_memory` in
health for dependency-free development. Production camera persistence requires
`camera_persistence=postgresql`.

GStreamer and its Python introspection bindings are system dependencies. Install the
CPU decode plugins for the host OS; NVIDIA plugins are not required. The worker loads
GStreamer lazily only when a real camera runtime or connection test starts.

Managed camera worker example:

```bash
python -m vision_worker.main --control-api-url http://127.0.0.1:8000 \
  --feature-config configuration/examples/face-recognition.example.json
```

## Matched backup and restore

Stop API and worker writes before backing up so database references and object
contents come from the same point in time. Keep all three private buckets with the
database backup:

```bash
mkdir -p backup/minio
pg_dump "$FR_POSTGRES_DSN" --format=custom --file=backup/postgres.dump
mc alias set local http://127.0.0.1:59000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"
mc mirror local/models backup/minio/models
mc mirror local/face-images backup/minio/face-images
mc mirror local/evidence backup/minio/evidence
```

Restore to the intended local database and buckets while the API and worker remain
stopped. The restore replaces database objects, so check the DSN before running it:

```bash
pg_restore --clean --if-exists --no-owner --dbname="$FR_POSTGRES_DSN" backup/postgres.dump
mc mirror --overwrite backup/minio/models local/models
mc mirror --overwrite backup/minio/face-images local/face-images
mc mirror --overwrite backup/minio/evidence local/evidence
```

Keep the database dump and three bucket copies together, label them with the same
backup timestamp, and test restoration before relying on them.
