# Milestone 3 acceptance report — 2026-10-03

Code: IMPLEMENTED. Adapter/pipeline evidence: TESTED_WITH_CONTROLLED_TEST_MODEL.
Real SCRFD, real AdaFace, real-media and RTSP acceptance: BLOCKED.
An embedding is not an identity, recognition decision, UNKNOWN or event.

## 1. Repository

Starting commit: 4842797aca52de74e525ca3d68b262c856b7d491
Ending commit: same; no commit or push performed.
Branch: main.
Working tree status: dirty before work and remains dirty. Existing YOLO/BoT-SORT,
face adapters and unrelated vector-index changes were preserved. This report
describes completion/hardening of the actual uncommitted implementation.

## 2. Files Changed

Paths below are relative to the repository root; only changes from this work are listed.

| Path | Purpose |
|---|---|
| workers/vision-worker/src/vision_worker/inference/scrfd_onnx.py | Validate crop/tensor semantics, rounded scales, padding anchors; stage timing |
| workers/vision-worker/src/vision_worker/inference/adaface_onnx.py | Output-count validation and per-thread stage timing |
| workers/vision-worker/src/vision_worker/inference/interfaces.py | Optional typed timing provider; existing face protocols retained |
| workers/vision-worker/src/vision_worker/face_recognition/images.py | Reject invalid fractional ROI; transient pixel repr privacy; affine metadata |
| workers/vision-worker/src/vision_worker/face_recognition/quality.py | Actual pre-rounding size and confidence validation |
| workers/vision-worker/src/vision_worker/face_recognition/alignment.py | Validate image/landmark shape, distinct/noncollinear points; retain transform |
| workers/vision-worker/src/vision_worker/face_recognition/embedding_pipeline.py | Candidate filtering, provenance, metadata, technical retry/result state and metrics |
| workers/vision-worker/src/vision_worker/face_recognition/latency.py | At most 256 timing samples per stage; lifetime attempt/failure counts |
| workers/vision-worker/src/vision_worker/model_delivery.py | Timing forwarding, assignment-aware activation and last-valid fallback |
| workers/vision-worker/src/vision_worker/runtime/pipeline.py | Typed failure observations, continued track/frame processing and health |
| workers/vision-worker/src/vision_worker/runtime/runner.py | Publish categorical face-model error and recovery |
| workers/vision-worker/src/vision_worker/runtime/track_store.py | Latest lightweight quality status; no retained pixels/vectors |
| packages/python/face-recognition-contracts/src/fr_contracts/runtime_status.py | Stage summaries, technical error/count and adapter timings |
| workers/vision-worker/tests/test_face_adapters.py | Strengthen existing current-failure state assertions |
| workers/vision-worker/tests/test_face_milestone3.py | Synthetic/controlled contracts, full adapter chain, lifecycle/recovery and CPU baseline |
| workers/vision-worker/tests/test_real_face_acceptance.py | Opt-in assigned-artifact/local-media acceptance; BLOCKED when inputs absent |
| apps/control-api/src/control_api/vector_index.py | Mechanical imports/line wrapping/redundant-cast correction only |
| apps/control-api/src/control_api/vector_index_api.py | Mechanical import ordering only |
| README.md | Explicit profiles, coordinates, quality, recovery, timing and real acceptance procedure |
| docs/superpowers/plans/2026-10-03-milestone-3-face-embedding.md | Implementation and verification record |
| docs/acceptance/milestone-3-face-embedding.md | This report |

No enrollment, FAISS behavior, identity decisions, events or frontend features were added.

## 3. Existing Architecture Reused

- FaceDetector/FaceEmbedder: existing structural protocols; adapters implement them.
- FaceDetection/FaceLandmarks: named DTOs; ROI-local detector outputs.
- Model delivery: assignment -> deployment/version/artifact -> SHA-256/manifest -> factory.
- Runtime factory: existing ProductionAdapterFactory, prepared wrappers and CPU resolution.
- ONNX session: existing OnnxInferenceSession, provider/tensor validation and reusable sessions.
- TrackState: camera/session/track key, bounded current face timing/result/quality metadata.
- FaceRecognitionConfig: existing quality and recognition/retry timing fields.
- YOLO/BoT-SORT/frame pipeline: implemented prerequisite code, exercised with controlled graphs.
- Stubs: confined to labelled unit/integration tests; complete controlled graph chain uses production adapters.

## 4. SCRFD

Implementation status: IMPLEMENTED; TESTED_WITH_CONTROLLED_TEST_MODEL.
Supported contract: static FP32 NCHW [1,3,H,W], positive H/W multiples of 32;
manifest input/output names must exactly match the graph. RGB/BGR order is declared.
Input: uint8 RGB ROI, bilinear aspect-preserving resize, bottom/right zero padding,
(pixel-127.5)/128, batch one.
Outputs: nine ordered tensors: three score, three bbox and three landmark matrices,
at strides 8/16/32, two anchors per grid cell. N=(H/s)*(W/s)*2; columns 1/4/10;
optional leading batch-one dimension. Scores are probabilities [0,1], boxes
nonnegative stride-unit distances, landmarks signed stride-unit offsets.
Landmarks: image-left eye, image-right eye, nose, image-left/right mouth.
NMS: continuous-area IoU, manifest thresholds (defaults confidence .5, IoU .4),
bounded 300 selected candidates per level.
Coordinate restoration: actual rounded x/y scales inverted separately; padding
anchors discarded, boxes clipped ROI-local, original landmark geometry preserved
for alignment validation; observation coordinates translated to full frame.
CPU provider: CPUExecutionProvider, verified with controlled ONNX execution.
Known limitations: this export profile only; no dynamic input, embedded NMS or
other anchor/output formats. No real SCRFD weights executed.
Reference: https://github.com/deepinsight/insightface/blob/master/detection/scrfd/tools/scrfd.py

## 5. Face Quality

Checks: actual visible face width/height, box/confidence geometry, configurable
minimum confidence, grayscale mean exposure, four-neighbour Laplacian variance.
Threshold source: FaceRecognitionConfig.quality (FaceQualityConfig).
Statuses: ACCEPTED, FACE_TOO_SMALL, LOW_DETECTION_CONFIDENCE, INVALID_GEOMETRY,
TOO_DARK, TOO_BRIGHT, TOO_BLURRY.
Known limitations: baseline signals only; pose/occlusion explicitly not evaluated.
Threshold defaults are policy values, not calibrated accuracy evidence.

## 6. Face Alignment

Method: least-squares similarity transform; OpenCV bilinear warp, zero border.
Landmark count: five.
Target size: 112x112 canonical ArcFace/AdaFace template.
Validation: image rank/channels/uint8, finite exact five coordinate pairs, image
bounds, eye/mouth order, distinct/noncollinear points and nondegenerate transform.
Metadata: ROI-to-aligned affine matrix, PERSON_ROI coordinate space and crop origin.
Known limitations: one template/size; controlled mathematical tests, not pose or biometric accuracy.
Reference: https://github.com/deepinsight/insightface/blob/master/python-package/insightface/utils/face_align.py

## 7. AdaFace

Implementation status: IMPLEMENTED; TESTED_WITH_CONTROLLED_TEST_MODEL.
Input: FP32 [1,3,112,112]; uint8 aligned RGB -> BGR -> pixel/127.5-1 -> NCHW.
Output: exactly one FP32 [1,D] tensor with manifest-matched name/dimension.
Embedding dimension: positive manifest D; controlled graph D=4. No silently accepted dimension.
Normalization: adapter alone performs L2 normalization; downstream validates norm.
Finite-value validation: exact shape/dtype, finite entries, norm above 1e-12;
reject NaN, Inf and zero/near-zero vectors.
CPU provider: CPUExecutionProvider, verified with controlled ONNX execution.
Known limitations: declared aligned export profile only; no real AdaFace weights executed.
Reference: https://github.com/mk-minchul/AdaFace/blob/master/inference.py

## 8. Runtime Flow

Frame -> assigned YOLO -> BoT-SORT -> clipped Person ROI -> assigned SCRFD ->
valid candidate selection -> Quality -> five-point Alignment -> assigned AdaFace ->
validated normalized EmbeddingVector -> TrackFaceObservation.
The full adapter chain executed with synthetic frames and controlled YOLO/SCRFD/
AdaFace graphs on CPU, with real BoT-SORT implementation. This is controlled
plumbing evidence, not real detector/embedder or recognition acceptance.

## 9. Failure Semantics

NO_FACE: successful inference without acceptable candidates; no embedding.
QUALITY_REJECTED: typed quality reason; no alignment/embedder inference.
ALIGNMENT_FAILURE: invalid geometry/alignment; no embedding inference.
MODEL_FAILURE: processor records current technical failure and raises; frame
pipeline emits a current track-scoped failure observation with no embedding.
Other tracks/frames continue and retries respect recognition_retry_ms. Current
failure never returns a previous embedding. Status is unhealthy and categorical
until recovery. Tests assert identity state remains PENDING and no verified person
is assigned; none of these outcomes map to UNKNOWN.

## 10. Tests

| Exact command | Result |
|---|---|
| python -m pytest --basetemp=.test-tmp/m3-final-verified -p no:cacheprovider | 197 passed, 1 skipped; skip explicitly BLOCKED real acceptance |
| python -m ruff check . | All checks passed |
| python -m mypy apps/control-api/src workers/vision-worker/src packages/python | Success; 68 source files; strict config unchanged |
| npm test (apps/web) | 2 test files, 3 tests passed |
| npm run typecheck (apps/web) | Exit 0 |
| npm run build (apps/web) | Exit 0 |

Windows cache permission issue avoided using fresh ignored basetemp and disabling
only pytest's cache provider. No tests deleted and no typechecking weakened.
Independent review found two important lifecycle defects; failing regression tests
reproduced both and fixes passed through runner/managed-thread and HTTP-provider
paths. Historical revision-cache pruning is a deferred minor.

## 11. Controlled Model Evidence

TESTED_WITH_CONTROLLED_TEST_MODEL: small ONNX ReduceMean/Mul/Add graphs produce
controlled YOLO detections, SCRFD stride matrices and AdaFace D=4 outputs.
They validate SHA-256/session contracts, preprocessing/decoding, role delivery,
warmup errors, shape/finite/norm rejection, complete track association and session
reuse. Synthetic image/landmark tests cover quality/alignment independently.
These graphs contain no production detector/embedder weights and prove no biometric accuracy.

## 12. Real SCRFD Evidence

BLOCKED. No supplied deployment-backed real artifact established; no real
filename/SHA/provider/input/output/execution evidence claimed. No download performed.

## 13. Real AdaFace Evidence

BLOCKED. No supplied deployment-backed real artifact established; no real
filename/SHA/provider/input/output/dimension/norm/execution evidence claimed.

## 14. Real Frame / Video Evidence

BLOCKED. No authorized local media with assigned real YOLO/SCRFD/AdaFace artifacts
was supplied/executed. Opt-in acceptance test requires all documented settings.
The complete controlled test uses production adapter classes but synthetic graphs/media.

## 15. RTSP Evidence

BLOCKED. Camera lifecycle/retry tests use a controlled backend, not real RTSP/GStreamer media.

## 16. Performance

Real-model performance: BLOCKED. The table is a controlled CPU plumbing baseline
only, 50 successful post-warmup attempts with tiny graphs; no production speed claim.
Python 3.13.5, ONNX Runtime 1.23.2, NumPy 2.2.6; CPUExecutionProvider.

| Stage | Mean ms | Median ms | p95 ms | Failure count |
|---|---:|---:|---:|---:|
| ROI | .036 | .032 | .055 | 0 |
| SCRFD adapter with controlled graph | .852 | .793 | 1.572 | 0 |
| Quality | .211 | .185 | .313 | 0 |
| Alignment | .924 | .841 | 1.441 | 0 |
| AdaFace adapter with controlled graph | .447 | .374 | .761 | 0 |
| Total face pipeline | 2.697 | 2.472 | 4.351 | 0 |

Safe evidence JSON: .test-tmp/m3-final-verified/test_controlled_deployment_yol0/controlled-face-cpu-baseline.json.
Runtime also records preprocessing/inference/postprocessing summaries and lifetime
failure counts; the synthetic failure-injection tests are separate from this successful baseline.

## 17. Acceptance Matrix

| Capability | Status |
|---|---|
| Real YOLO | BLOCKED |
| Real BoT-SORT | IMPLEMENTED |
| Real SCRFD | BLOCKED |
| Face Quality | IMPLEMENTED |
| 5-point Alignment | IMPLEMENTED |
| Real AdaFace | BLOCKED |
| Real face embedding | BLOCKED |
| Track -> face embedding | TESTED_WITH_CONTROLLED_TEST_MODEL |
| RTSP -> face embedding | BLOCKED |
| Person Enrollment | NOT_IMPLEMENTED |
| Face Registry | NOT_IMPLEMENTED |
| FAISS | IMPLEMENTED |
| Identity matching | NOT_IMPLEMENTED |
| Multi-frame recognition | NOT_IMPLEMENTED |
| UNKNOWN_PENDING | NOT_IMPLEMENTED |
| Unknown Verification | NOT_IMPLEMENTED |
| Human correction | NOT_IMPLEMENTED |
| Events/evidence | NOT_IMPLEMENTED |

FAISS implementation predates this work and is not connected to the face path;
only its control-API lint/type issues were corrected. Other out-of-scope rows
refer to production capability in this milestone; pre-existing domain scaffolds
were preserved. Overall recognition remains NOT_READY.

## 18. Remaining Gaps

- Supplied deployment-backed real SCRFD/AdaFace/YOLO weights and authorized media.
- Real local-video/RTSP acceptance, real CPU benchmark and long-running soak.
- Policy calibration; pose/occlusion evaluation remains explicitly unsupported.
- Deferred minor from independent review: historical successful deployment revision
  metadata cache is not pruned. Timing windows and track face fields are bounded;
  no images/embedding history are retained in TrackState.
- Earlier .pytest-m3-audit-20261003 directory remains after policy-rejected cleanup;
  current test output lives under ignored .test-tmp.

## 19. Exact Next Milestone

After real Milestone 3 acceptance: PERSON ENROLLMENT -> MULTIPLE FACE IMAGES ->
MULTIPLE ADAFACE EMBEDDINGS -> SEARCH BY NAME / STUDENT ID -> FACE REGISTRY UI ->
PERSISTENT GALLERY. Do not begin FAISS integration or Unknown Verification until
the Enrollment foundation is explicitly started.
