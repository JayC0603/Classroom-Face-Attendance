# Milestone 3 Face Embedding Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task, with test-driven-development and verification-before-completion.

**Goal:** Produce a validated, normalized face embedding associated with a person track through deployment-backed SCRFD and AdaFace CPU adapters.

**Architecture:** Extend the existing adapters, delivery lifecycle, TrackFaceProcessor, TrackState and runtime status. Detection stays ROI-local; observations use full-frame coordinates. Images and vectors remain transient and bounded; identity and event processing are outside this path.

**Tech Stack:** Python, NumPy, Pillow, OpenCV, ONNX Runtime CPU, pytest, Pydantic; existing React gates.

**Spec:** User's supplied Milestone 3 requirements (sections 1–60), reflected below.

## Global Constraints

- FACE RECOGNITION ONLY; this milestone ends at a track-associated embedding.
- No enrollment, Person CRUD, face registry, FAISS, matching, verification, UNKNOWN_PENDING, alerts or feedback implementation.
- Never download model weights automatically or bypass deployment assignments.
- Validate role, SHA-256, manifest, tensors and warmup before swapping models.
- CPU is required; accelerated providers are unclaimed.
- NO_FACE, QUALITY_REJECTED, ALIGNMENT_FAILURE and MODEL_FAILURE never mean UNKNOWN.
- No raw images, vectors, credentials or internal artifact paths in public logs/status.
- Preserve the dirty main working tree; do not commit or remove unrelated work.

## Review Focus

- Fractional reversed boxes and rounded tiny faces must not become valid crops/quality.
- Malformed tensors and out-of-range scores must be technical failures.
- Non-square resize restoration must use actual horizontal/vertical scales.
- Collinear/duplicated landmarks and malformed images must stop before embedding.
- Failed hot replacement must preserve usable previous sessions until readers stop.

## Tasks

### Task 1: Harden image and detector contracts

Files: inference/scrfd_onnx.py, face_recognition/images.py, tests/test_face_milestone3.py (under workers/vision-worker).
Interfaces: keep preprocess(crop) -> (tensor, scale) and postprocess(outputs, width, height, scale); add optional vertical-scale restoration without breaking existing callers.
- [x] Write and run failing tests for malformed image shape/dtype, reversed fractional ROI, scores outside [0,1], negative distances, rounded resize and padded anchors.
- [x] Validate input/output semantics and restore actual resize axes; preserve typed detector failures.
- [x] Run focused tests; expected all pass.

### Task 2: Quality, selection and alignment metadata

Files: face_recognition/quality.py, alignment.py, images.py, embedding_pipeline.py; tests/test_face_milestone3.py.
Interfaces: retain quality statuses and FaceAligner.align; add lightweight affine metadata to AlignedFace and TrackFaceObservation; select only finite positive-area candidates contained in ROI, confidence then area then coordinates.
- [x] Write/run failing tests for fractional size, geometry filtering, collinear/duplicate landmarks, translated/scaled transform mapping, image validation and boundary padding.
- [x] Implement minimal validation and metadata; keep alignment dedicated and configurable quality independent.
- [x] Run focused tests; expected all pass.

### Task 3: Failure scheduling and bounded stage metrics

Files: inference/interfaces.py, scrfd_onnx.py, adaface_onnx.py, model_delivery.py, face_recognition/latency.py, embedding_pipeline.py, runtime_status.py.
Interfaces: optional model-stage timing provider returning a typed value; runtime stage summaries with count, failure count, mean/median/p95 over at most 256 timings; TrackState stores only latest attempt/result/quality metadata.
- [x] Write/run failing tests for current technical-result state, recognition/retry boundaries, per-stage timing, failure durations and bounded metrics.
- [x] Implement metrics and failure updates without logging vectors or retaining images.
- [x] Run focused tests; expected all pass.

### Task 4: Delivery/resource/readiness acceptance

Files: tests/test_face_milestone3.py; production lifecycle files only if tests expose defects.
- [x] Add controlled ONNX tests for both roles, warmup failure preservation, deferred retirement, repeat inference with reused sessions, complete provenance and normalization.
- [x] Verify factory cannot prepare a vector index and overall recognition remains NOT_READY.
- [x] Check only configured/assigned local artifacts; real acceptance is BLOCKED if unavailable. Do not manufacture production evidence.

### Task 5: Documentation and full gates

Files: README.md, docs/acceptance/milestone-3-face-embedding.md; existing vector-index files only for mechanical lint/type corrections needed by mandatory gates.
- [x] Document exact supported contracts, selection, coordinates, quality limitations, failure semantics, bounded timing and evidence vocabulary.
- [x] Run pytest with fresh ignored basetemp, Ruff, strict mypy, npm test, npm run typecheck, npm run build; expected all pass.
- [x] Perform final independent review and resolve material findings with regression tests.
- [x] Deliver the user's exact 19-section report, distinguishing inherited code, this turn's edits, controlled graphs and blocked real acceptance.

## Execution Notes

Pre-flight: Task 2 consumes Task 1 ROI coordinates. Task 3 consumes Task 2 observations/alignment metadata. Task 4 exercises Tasks 1–3 through existing delivery. Task 5 verifies all interfaces.
Ruling: work in the current dirty main checkout to validate the user's actual implementation; an isolated clean worktree would omit prerequisite uncommitted adapters. Keep changes reviewable without committing unrelated files.

Completion evidence: 197 passed, 1 BLOCKED real-acceptance skip; Ruff clean; strict mypy clean (68 files); frontend 3 tests, typecheck and build passed. No type checks weakened or tests deleted.
Independent review: two Important lifecycle findings reproduced RED then fixed GREEN. The managed camera loop now survives face failures and retries; HTTP assignment replacement retains the previous working runtime on load failure and honors withdrawal.
Final review: minor (deferred): historical successful deployment revision metadata cache is not pruned. It does not retain images or embedding vectors.
Ruling: preserve the existing out-of-scope vector-index work and correct only mechanical lint/type issues required by full gates. Cost if wrong: these files still require separate review for their pre-existing behavior.
Ruling: leave verified edits in the user's dirty main checkout without committing unrelated files. Cost if wrong: the user must select/stage changes when integrating them.
Real-model/media/RTSP acceptance remains BLOCKED; the 50-attempt CPU timing report is explicitly TESTED_WITH_CONTROLLED_TEST_MODEL.
