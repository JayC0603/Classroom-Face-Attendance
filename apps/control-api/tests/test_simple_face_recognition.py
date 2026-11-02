import numpy as np


def test_identity_aggregation_does_not_treat_two_photos_as_two_people():
    from control_api.simple_face_recognition import best_identity

    gallery = [
        ("alice", "a1", np.array([1.0, 0.0])),
        ("alice", "a2", np.array([0.99, 0.1])),
        ("bob", "b1", np.array([0.0, 1.0])),
    ]
    person, score, margin = best_identity(np.array([1.0, 0.0]), gallery)
    assert person == "alice" and score == 1.0 and margin == 1.0


def test_verification_requires_distinct_frames_and_resets_for_revision_or_conflict():
    from control_api.simple_face_recognition import FaceVerifier

    verifier = FaceVerifier()
    box = [0.1, 0.1, 0.2, 0.3]
    args = ("camera/revision/gallery/session", box, "alice")
    assert verifier.observe(*args, frame_id="1", now=1.0) == "PENDING"
    assert verifier.observe(*args, frame_id="1", now=1.1) == "PENDING"
    assert verifier.observe(*args, frame_id="2", now=2.0) == "PENDING"
    assert verifier.observe(*args, frame_id="3", now=3.0) == "VERIFIED"
    assert verifier.observe(args[0], box, "bob", frame_id="4", now=4.0) == "PENDING"
    assert (
        verifier.observe("camera/new-revision/gallery/session", box, "alice", frame_id="5", now=4.0)
        == "PENDING"
    )
    assert verifier.observe(*args, frame_id="6", now=20.0) == "PENDING"


def test_confident_no_match_is_unknown_on_first_valid_observation():
    from control_api.simple_face_recognition import FaceVerifier

    verifier = FaceVerifier()
    assert verifier.observe(
        "camera/revision/gallery/session",
        [0.1, 0.1, 0.2, 0.3],
        None,
        frame_id="frame-1",
        now=1.0,
        immediate_unknown=True,
    ) == "UNKNOWN"


def test_face_embeddings_are_only_created_for_faces_inside_the_camera_roi():
    from io import BytesIO

    from control_api.simple_face_recognition import OpenCvFaceModels
    from PIL import Image

    class Detector:
        def setInputSize(self, size):
            assert size == (200, 200)

        def detect(self, _image):
            return 1, np.array(
                [
                    [40, 40, 50, 50, 50, 55, 75, 55, 62, 65, 52, 80, 74, 80, 0.99],
                    [140, 40, 50, 50, 150, 55, 175, 55, 162, 65, 152, 80, 174, 80, 0.98],
                ],
                dtype=np.float32,
            )

    class Recognizer:
        aligned = 0

        def alignCrop(self, _image, _row):
            self.aligned += 1
            return np.zeros((112, 112, 3), dtype=np.uint8)

        def feature(self, _aligned):
            return np.ones((1, 128), dtype=np.float32)

    models = OpenCvFaceModels()
    models.detector = Detector()
    models.recognizer = Recognizer()
    payload = BytesIO()
    Image.new("RGB", (200, 200), "white").save(payload, format="JPEG")

    samples = models.analyze(
        payload.getvalue(), roi_polygon=[(0.1, 0.1), (0.6, 0.1), (0.6, 0.6), (0.1, 0.6)]
    )

    assert len(samples) == 1
    assert models.recognizer.aligned == 1


def test_ambiguous_and_unknown_observations_do_not_verify_a_face():
    from control_api.simple_face_recognition import FaceVerifier

    verifier = FaceVerifier()
    for index in range(5):
        assert (
            verifier.observe("scope", [0, 0, 0.2, 0.2], None, frame_id=str(index), now=index)
            != "VERIFIED"
        )


def test_face_detector_still_runs_without_registered_reference_images(tmp_path):
    from control_api.face_image_storage import FaceImageStorage
    from control_api.face_registry import FaceRegistryService, InMemoryFaceRegistryRepository
    from control_api.simple_face_recognition import FaceSample, RegisteredFaceComparison

    class Models:
        analyzed = []

        def load(self):
            pass

        def status(self):
            return {"state": "READY"}

        def analyze(self, payload, roi_polygon=None):
            self.analyzed.append((payload, roi_polygon))
            return [FaceSample([0.1, 0.2, 0.3, 0.4], np.array([1.0, 0.0]))]

    models = Models()
    registry = FaceRegistryService(InMemoryFaceRegistryRepository(), FaceImageStorage(tmp_path))
    service = RegisteredFaceComparison(registry, models)

    result = service.compare(
        b"live-camera-frame",
        "camera-1",
        "frame-1",
        roi_polygon=[(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)],
    )

    assert models.analyzed == [
        (b"live-camera-frame", [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)])
    ]
    assert result["state"] == "NO_REFERENCE_IMAGES"
    assert result["faces"] == [
        {
            "box": [0.1, 0.2, 0.3, 0.4],
            "state": "UNKNOWN",
            "similarity": 0.0,
            "person_id": None,
            "full_name": None,
            "student_id": None,
            "person_number": None,
        }
    ]


def test_comparison_profile_does_not_need_manual_embeddings_or_faiss(tmp_path):
    from control_api.simple_face_recognition import OpenCvFaceModels

    models = OpenCvFaceModels(model_root=tmp_path)
    status = models.status()
    assert status["state"] == "MODELS_MISSING"
    assert status["model_names"] == ["YuNet", "SFace"]


def test_corrupt_weights_are_rejected_before_loading_opencv(tmp_path):
    import pytest
    from control_api.simple_face_recognition import ComparisonError, OpenCvFaceModels

    models = OpenCvFaceModels(tmp_path)
    for role in ("detector", "recognizer"):
        (tmp_path / models.config[role]["filename"]).write_bytes(b"not-a-real-model")
    with pytest.raises(ComparisonError, match="MODEL_CHECKSUM_MISMATCH"):
        models.load()


def test_reference_failures_do_not_verify_against_an_old_gallery(tmp_path):
    from io import BytesIO

    import pytest
    from control_api.face_image_storage import FaceImageStorage
    from control_api.face_registry import (
        FaceRegistryService,
        InMemoryFaceRegistryRepository,
        PersonCreate,
    )
    from control_api.simple_face_recognition import (
        ComparisonError,
        FaceSample,
        RegisteredFaceComparison,
    )
    from PIL import Image

    class Models:
        fail = False

        def load(self):
            pass

        def status(self):
            return {"state": "READY"}

        def analyze(self, payload, roi_polygon=None):
            if self.fail:
                raise ComparisonError("FACE_INFERENCE_FAILED")
            return [FaceSample([0, 0, 0.2, 0.2], np.array([1.0, 0.0]))]

    registry = FaceRegistryService(InMemoryFaceRegistryRepository(), FaceImageStorage(tmp_path))
    person = registry.create_person(PersonCreate(full_name="Synthetic", student_id="TEST"))
    out = BytesIO()
    Image.new("RGB", (40, 40), "blue").save(out, format="JPEG")
    enrolled = registry.save_image(person.id, "face.jpg", out.getvalue())
    registry.review_image(enrolled.id, "reviewer-1", "APPROVE")
    models = Models()
    service = RegisteredFaceComparison(registry, models)
    assert service.status()["state"] == "READY"
    service.compare(out.getvalue(), "scope", "one")
    models.fail = True
    assert service.status(force=True)["state"] == "FACE_INFERENCE_FAILED"
    with pytest.raises(ComparisonError, match="FACE_INFERENCE_FAILED"):
        service.compare(out.getvalue(), "scope", "one")
    service._checked = float("-inf")
    registry.save_image(person.id, "new.jpg", out.getvalue())
    for _ in range(2):
        with pytest.raises(ComparisonError, match="FACE_INFERENCE_FAILED"):
            service.compare(out.getvalue(), "scope", "one")


def test_missing_minio_reference_image_keeps_person_detection_endpoint_available():
    from datetime import UTC, datetime

    from control_api.face_image_storage import FaceImageStorage
    from control_api.face_registry import (
        FaceImagePublic,
        FaceRegistryService,
        InMemoryFaceRegistryRepository,
        PersonCreate,
        StoredFaceImage,
    )
    from control_api.object_storage import ObjectNotFound
    from control_api.simple_face_recognition import RegisteredFaceComparison

    class MissingStorage:
        def get(self, _key):
            raise ObjectNotFound("object not found")

    class Models:
        def load(self):
            return None

        def status(self):
            return {"state": "READY"}

    repository = InMemoryFaceRegistryRepository()
    registry = FaceRegistryService(
        repository, FaceImageStorage(None, object_storage=MissingStorage())
    )
    person = registry.create_person(PersonCreate(full_name="Synthetic", student_id="TEST"))
    now = datetime.now(UTC)
    repository.add_image(
        StoredFaceImage(
            public=FaceImagePublic(
                id="image-1",
                person_id=person.id,
                original_filename="legacy.jpg",
                width=40,
                height=40,
                    review_state="APPROVED",
                created_at=now,
                content_url="/private/image-1",
            ),
            storage_key="legacy/path.jpg",
            checksum="0" * 64,
        )
    )

    state = RegisteredFaceComparison(registry, Models()).status(force=True)

    assert state["state"] == "NO_REFERENCE_IMAGES"
    assert state["images"] == [
        {"person_id": person.id, "image_id": "image-1", "state": "IMAGE_UNAVAILABLE"}
    ]
