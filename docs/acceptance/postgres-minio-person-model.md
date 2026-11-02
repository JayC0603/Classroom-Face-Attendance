# PostgreSQL, MinIO, and YOLOX person-model acceptance

Run date: 2026-10-19, on the local Windows development host.

## Verified

- Local Compose PostgreSQL 17, Redis, and MinIO started with their named volumes;
  PostgreSQL and MinIO were reachable only on loopback. The `models`, `face-images`,
  and `evidence` buckets passed the API startup health checks.
- API health reported `status=ok`, `external_dependencies=configured`,
  `camera_persistence=postgresql`, and `object_storage=ready`.
- Migrations 001–008 were applied before repository startup. Existing model records
  and registered face images that pointed to local files were checksum-checked and
  copied to MinIO. Database rows now refer to opaque object keys. Local originals
  remain in place.
- The existing 35,997,222-byte YOLOX file was reused by SHA-256
  `10a9cacf27c3aa276e4a323fcf86bc6275542b45456aa11c1abd10ff18e6711c`. The selected
  runtime profile is `yolox_xyxy`, input `[1,3,608,1088]`, output `[1,13566,6]`,
  person class `0`, confidence threshold `0.5`. A second import does not create a
  duplicate artifact/version when its effective profile matches.
- Worker delivery used the worker-authenticated API content route. The worker
  checksum-verified the local cache, loaded the model with `CPUExecutionProvider`,
  completed warmup, and only then assigned deployment
  `a93235ab-ea98-461c-b3f2-12ba30c0c42c` as `PERSON_DETECTOR`.
- Camera `c2dda7ca-3831-4ff2-a40c-64db4455baf1` is configured as an enabled
  worker-local webcam on device index 0. Index 1 was unavailable on this host;
  OpenCV confirmed index 0 can provide frames. After the final PostgreSQL/MinIO
  container restart and API/worker recovery, the worker reported `CONNECTED`,
  `healthy`, one active track, 159 processed frames, and zero person-inference
  failures. The recognition endpoint returned one person box with Track ID 0 and
  `person_state=READY`; the private preview endpoint returned HTTP 200 with JPEG.
- In that live post-restart sample, the CPU camera pipeline processed about 1.98
  FPS and person inference took about 455 ms on the latest frame (476 ms mean).
  This is a local sample, not a sustained performance benchmark.
- `test_postgres_minio_restart.py` recreated model, assignment, camera, face-image,
  event, attendance, and evidence records in an isolated PostgreSQL schema and
  verified them through newly constructed repositories with real MinIO storage.
  The test removed only its generated schema and uniquely named test objects.

## Validation run

The following passed against the local PostgreSQL and MinIO services:

```text
92 backend tests passed across migrations, PostgreSQL repositories, MinIO object
storage, model import/delivery, camera preview, events, and YOLOX ONNX inference.
31 frontend tests passed across 12 files.
The TypeScript check, Vite production build, and Ruff checks passed.
```

## Limits

Person detection and Track IDs are active. Face detection/identity verification is a
separate pipeline and is not configured by this person-model deployment; the tested
camera result was `NO_FACE`. No verified identity, attendance record, or real-camera
evidence event was generated during this run. The synthetic PostgreSQL/MinIO event
test verifies persistence and evidence retrieval, not recognition accuracy.

The local MinIO service is pinned to the public ECR mirror image declared in
`infrastructure/local/compose.yaml`. For production, use an image source approved
and pinned by the deployment owner, secure non-placeholder secrets, and keep matched
PostgreSQL and MinIO backups as described in the local setup guide.
