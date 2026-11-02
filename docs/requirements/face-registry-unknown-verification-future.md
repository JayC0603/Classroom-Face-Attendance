# Approved future requirements: Face Registry and Unknown Verification

Status: approved product requirements; **not implemented in the YOLO ONNX CPU milestone**.
Scope: Face Recognition only. CPU execution remains mandatory; CUDA and TensorRT are optional.
The detailed future database, API and state contract is recorded in
[Database, API and state contract](database-api-state-contract-future.md).

## Boundaries to preserve

- `Person` is the durable business identity. Student ID is unique. A Person has a full name,
  status, creation and update times, many `FaceImage` records, and many `FaceEmbedding`
  records. A FaceImage and its generated embeddings are separate records; neither Person
  nor FaceImage is limited to one image or one vector.
- A FaceImage records provenance: `MANUAL_UPLOAD`, `CAMERA_CAPTURE`, or
  `UNKNOWN_CORRECTION`. Retain the active embedder model/version and full compatibility
  profile on every embedding. Store image artifacts outside PostgreSQL through the
  existing storage boundary; persist metadata and references in PostgreSQL. Do not
  permanently retain every observed face crop.
- PostgreSQL is authoritative for Person, FaceImage, FaceEmbedding, decisions and review.
  FAISS is a derived, rebuildable search index with an immutable revision and stable
  embedding-to-person mapping. Build, validate and activate replacements atomically;
  never mix incompatible embedding spaces or expose a vector without durable mapping.
  On embedder version change, regenerate embeddings from retained FaceImages, build and
  validate a compatible replacement index, then atomically activate it while retaining
  the previous valid index until replacement is ready.
- Keep `RecognitionResult`, immutable `RecognitionEvent`, `HumanVerification` and
  `RecognitionFeedback` separate. A review/correction appends records; it never silently
  rewrites the original recognition result or event. Persist reviewer, review time,
  original candidate/similarity, selected Person, created image/embedding IDs and model
  revision where applicable. Feedback is not automatic neural-network training data.
- Recognition outcomes must distinguish `RECOGNIZED`, `UNKNOWN_PENDING`, `NO_FACE`,
  `QUALITY_REJECTED`, `MODEL_FAILURE` and `VECTOR_INDEX_UNAVAILABLE`. Insufficient
  evidence remains pending/uncertain. Model, preprocessing, search and index errors
  never become an unknown-person decision.
- Human review states are separate: `PENDING`, `CONFIRMED_UNKNOWN`,
  `CORRECTED_AS_REGISTERED`, `DISMISSED`. Default behavior creates **no final unknown
  alert** before `CONFIRMED_UNKNOWN`. A future automatic policy requires an explicit
  configuration and separate approval.
- Multiple valid observations from one camera/session/track feed the configured
  multi-frame verifier. Use configured retry, dwell and cooldown to create at most one
  pending review task per continuing unknown track, not one task per frame.

## Future operator flows

Face Registry supports full-name and unique Student-ID search, including partial matches;
create/view/edit/disable Person; multiple image upload and gallery view. Results show full
name, Student ID and a representative image when available, and selection requires human
confirmation. An image enters the active index only after format/size, exactly-one-face,
face size, blur/exposure/quality, alignment and embedding validation succeed. UI reports
backend validation outcomes and never computes embeddings or declares index readiness.

Unknown Verification shows pending candidates with available face/frame evidence,
camera, time, track ID, similarity and Top-K context. `CONFIRM UNKNOWN` records the
review, then emits the confirmed alert with evidence. `ALREADY REGISTERED` requires
search and explicit Person selection, confirmation, quality/alignment/embedding checks,
durable FaceImage plus FaceEmbedding creation with source `UNKNOWN_CORRECTION`, safe
index update or recoverable reconciliation, then `CORRECTED_AS_REGISTERED`. Existing
gallery items remain intact. Subsequent compatible search must retrieve the new vector.
Any failed mandatory step leaves no partial *active* enrollment.
The confirmed event retains camera, occurrence time, face/snapshot evidence,
similarity context, reviewer and review time. A correction appends a validated
gallery image and embedding; it does not retrain AdaFace on each review.

## Current-contract compatibility notes

- `fr_domain.entities.Person` and `FaceEnrollment` are preliminary in-memory shapes.
  They do not yet encode Student ID, image provenance, or distinct persisted images and
  embeddings. Migrate them in the Face Registry milestone; do not treat them as the
  final database schema.
- `fr_domain.recognition.RecognitionState.UNKNOWN` is an internal verifier result after
  sufficient successful no-match observations. It is **not** human-confirmed unknown.
  Before adding review/events, map it to a separate `UNKNOWN_PENDING` recognition/review
  workflow without conflating it with `CONFIRMED_UNKNOWN`.
  Likewise, internal `VERIFIED` becomes a product-level `RECOGNIZED` result only after
  its configured multi-frame conditions hold.
- `fr_config.models.PolicyAction` currently permits `UNKNOWN_PERSON`. Before policy
  activation, validate that this action cannot emit a final unknown alert from a raw
  `UNKNOWN` result under the default policy. Update the config contract/version when
  review semantics are implemented; preserve compatibility for stored draft configs.
- The current `VectorStore` and verifier interfaces are scaffolds. Before real FAISS,
  add embedding compatibility, index revision, stable mapping and typed failure states.
- The YOLO CPU detector-only path stops before face detection, identity, review and
  alerts. Its `degraded` status cannot be interpreted as recognition readiness.

## Milestones and acceptance

1. Real frame/RTSP -> YOLO -> real BoT-SORT.
2. Track -> SCRFD -> quality -> alignment -> AdaFace.
3. Person -> many FaceImages/FaceEmbeddings -> Face Registry/search -> FAISS.
4. FAISS Top-K -> multi-frame recognition -> `RECOGNIZED` or `UNKNOWN_PENDING`.
5. Unknown Verification: confirm unknown -> alert, or correct as registered -> new
   image/embedding -> safe index update -> later retrieval.
6. Recognition history, events/evidence, configuration persistence and full RTSP
   acceptance. Keep navigation within the existing Face Recognition app.

Acceptance must separately prove code exists and real artifacts/runtime executed.
Test registration with several images and full/partial name or Student-ID search;
unknown pending without alert until human confirmation; correction followed by actual
search retrieval; and forced model failure producing `MODEL_FAILURE` with no unknown
review task. Mock or database-only evidence does not pass real recognition acceptance.
