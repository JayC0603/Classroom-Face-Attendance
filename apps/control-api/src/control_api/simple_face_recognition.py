"""Automatic, bounded YuNet/SFace comparison against registered reference images.

Model features stay private to this adapter. No caller-supplied vectors or FAISS
configuration are needed. Results are observations, not attendance events.
"""

import json
import os
from collections import deque
from dataclasses import dataclass, field
from hashlib import sha256
from importlib import import_module
from io import BytesIO
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Any

from PIL import Image

from control_api.face_registry import FaceRegistryService, PersonPublic, StoredFaceImage
from control_api.object_storage import ObjectStorageError

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    Vector = NDArray[np.float32]

CONFIG_PATH = Path(__file__).resolve().parents[4] / "configuration/models/opencv-face.json"


class ComparisonError(RuntimeError):
    pass


@dataclass
class FaceSample:
    box: list[float]  # normalized x/y/width/height in the source image
    feature: "Vector"


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    previous = len(polygon) - 1
    for current, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[previous]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
        previous = current
    return inside


class OpenCvFaceModels:
    def __init__(self, model_root: Path | None = None) -> None:
        self.root = model_root or Path(
            os.getenv("FR_SIMPLE_FACE_MODEL_ROOT", "data/models/opencv-face")
        )
        self.config = json.loads(CONFIG_PATH.read_text())
        self.detector: Any = None
        self.recognizer: Any = None
        self.cv: Any = None
        self.error: str | None = None

    def status(self) -> dict[str, object]:
        present = all(
            (self.root / self.config[role]["filename"]).is_file()
            for role in ("detector", "recognizer")
        )
        return {
            "state": self.error
            or (
                "READY"
                if self.detector is not None
                else "MODEL_LOADING"
                if present
                else "MODELS_MISSING"
            ),
            "model_names": ["YuNet", "SFace"],
            "profile": self.config["profile"],
        }

    def load(self) -> None:
        if self.detector is not None and self.recognizer is not None:
            return
        try:
            paths = {}
            for role in ("detector", "recognizer"):
                spec = self.config[role]
                path = self.root / spec["filename"]
                if not path.is_file():
                    raise ComparisonError("MODELS_MISSING")
                if sha256(path.read_bytes()).hexdigest() != spec["sha256"]:
                    raise ComparisonError("MODEL_CHECKSUM_MISMATCH")
                paths[role] = str(path)
            cv = import_module("cv2")
            np = import_module("numpy")
            detector = cv.FaceDetectorYN.create(
                paths["detector"],
                "",
                (320, 320),
                0.8,
                0.3,
                500,
                cv.dnn.DNN_BACKEND_OPENCV,
                cv.dnn.DNN_TARGET_CPU,
            )
            recognizer = cv.FaceRecognizerSF.create(
                paths["recognizer"], "", cv.dnn.DNN_BACKEND_OPENCV, cv.dnn.DNN_TARGET_CPU
            )
            detector.detect(np.zeros((320, 320, 3), dtype=np.uint8))
            feature = recognizer.feature(np.zeros((112, 112, 3), dtype=np.uint8))
            if feature.size != 128 or not np.isfinite(feature).all():
                raise ComparisonError("INVALID_SFACE_OUTPUT")
            self.cv, self.detector, self.recognizer = cv, detector, recognizer
            self.error = None
        except ComparisonError as error:
            self.error = str(error)
            raise
        except ImportError as error:
            self.error = "OPENCV_UNAVAILABLE"
            raise ComparisonError(self.error) from error
        except Exception as error:
            self.error = "FACE_MODEL_LOAD_FAILED"
            raise ComparisonError(self.error) from error

    def analyze(
        self, payload: bytes, roi_polygon: list[tuple[float, float]] | None = None
    ) -> list[FaceSample]:
        self.load()
        np = import_module("numpy")
        try:
            with Image.open(BytesIO(payload)) as source:
                if source.width * source.height > 20_000_000:
                    raise ComparisonError("IMAGE_TOO_LARGE")
                rgb = source.convert("RGB")
                rgb.thumbnail((640, 480))
                bgr = np.ascontiguousarray(np.asarray(rgb)[:, :, ::-1])
            height, width = bgr.shape[:2]
            self.detector.setInputSize((width, height))
            _, detections = self.detector.detect(bgr)
            if detections is None:
                return []
            samples = []
            for row in sorted(detections, key=lambda item: -float(item[-1])):
                if row.size != 15 or not np.isfinite(row).all():
                    raise ComparisonError("INVALID_FACE_DETECTION")
                x, y, w, h = map(float, row[:4])
                if min(w, h) < 30 or x < 0 or y < 0 or x + w > width or y + h > height:
                    continue
                if roi_polygon and not _point_in_polygon(
                    (x + w / 2) / width, (y + h / 2) / height, roi_polygon
                ):
                    continue
                landmarks = row[4:14].reshape(5, 2)
                if np.linalg.norm(landmarks[0] - landmarks[1]) < 2:
                    continue
                aligned = self.recognizer.alignCrop(bgr, row)
                feature = self.recognizer.feature(aligned).reshape(-1).astype(np.float32)
                norm = float(np.linalg.norm(feature))
                if feature.size != 128 or not np.isfinite(feature).all() or norm < 1e-8:
                    raise ComparisonError("INVALID_SFACE_OUTPUT")
                samples.append(
                    FaceSample([x / width, y / height, w / width, h / height], feature / norm)
                )
                if len(samples) >= 8:
                    break
            return samples
        except ComparisonError:
            raise
        except Exception as error:
            raise ComparisonError("FACE_INFERENCE_FAILED") from error


def best_identity(
    query: "Vector", gallery: list[tuple[str, str, "Vector"]]
) -> tuple[str | None, float, float]:
    """Max score per person, then compare distinct identities for the margin."""
    import numpy as np

    scores: dict[str, float] = {}
    for person_id, _, vector in gallery:
        score = float(np.dot(query, vector))
        scores[person_id] = max(scores.get(person_id, -1.0), min(1.0, max(-1.0, score)))
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if not ordered:
        return None, -1.0, 0.0
    person_id, score = ordered[0]
    return person_id, score, score - (ordered[1][1] if len(ordered) > 1 else -1.0)


def _iou(a: list[float], b: list[float]) -> float:
    width = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    intersection = width * height
    return intersection / max(1e-8, a[2] * a[3] + b[2] * b[3] - intersection)


@dataclass
class _Track:
    box: list[float]
    seen: float
    observations: deque[tuple[str, float, str | None]] = field(
        default_factory=lambda: deque(maxlen=5)
    )


class FaceVerifier:
    def __init__(self, minimum_observations: int = 3, window_seconds: float = 5) -> None:
        if not 2 <= minimum_observations <= 5 or not 0 < window_seconds <= 10:
            raise ValueError("INVALID_VERIFICATION_PROFILE")
        self._minimum = minimum_observations
        self._window_seconds = window_seconds
        self._tracks: dict[str, list[_Track]] = {}

    def clear(self, scope: str) -> None:
        self._tracks.pop(scope, None)

    def observe(
        self,
        scope: str,
        box: list[float],
        person_id: str | None,
        *,
        frame_id: str,
        now: float,
        immediate_unknown: bool = False,
    ) -> str:
        self._tracks = {
            key: [t for t in values if now - t.seen <= self._window_seconds]
            for key, values in self._tracks.items()
        }
        self._tracks = {key: value for key, value in self._tracks.items() if value}
        if scope not in self._tracks and len(self._tracks) >= 16:
            raise ComparisonError("RECOGNITION_CAPACITY_REACHED")
        tracks = self._tracks.setdefault(scope, [])
        candidates = [
            t
            for t in tracks
            if not t.observations or t.observations[-1][0] != frame_id or _iou(t.box, box) > 0.95
        ]
        track = max(candidates, key=lambda t: _iou(t.box, box), default=None)
        if track is None or _iou(track.box, box) < 0.3:
            if len(tracks) >= 8:
                tracks.pop(0)
            track = _Track(box, now)
            tracks.append(track)
        track.box, track.seen = box, now
        window = track.observations
        while window and now - window[0][1] > self._window_seconds:
            window.popleft()
        if not any(item[0] == frame_id for item in window):
            # Contradictory evidence immediately removes a previously verified identity.
            if window and person_id != window[-1][2]:
                window.clear()
            window.append((frame_id, now, person_id))
        if immediate_unknown and person_id is None:
            return "UNKNOWN"
        if len(window) < self._minimum or window[-1][1] - window[0][1] < 0.5:
            return "PENDING"
        identities = [item[2] for item in window]
        if person_id is not None and all(value == person_id for value in identities):
            return "VERIFIED"
        return "UNKNOWN" if all(value is None for value in identities) else "UNCERTAIN"


class RegisteredFaceComparison:
    def __init__(
        self, registry: FaceRegistryService, models: OpenCvFaceModels | None = None
    ) -> None:
        self.registry = registry
        self.models = models or OpenCvFaceModels()
        self._lock = Lock()
        self._gallery: list[tuple[str, str, Vector]] = []
        self._people: dict[str, PersonPublic] = {}
        self._signature = ""
        self._gallery_valid = False
        self._checked = float("-inf")
        self._image_results: list[dict[str, str]] = []
        self._cached: dict[str, tuple[str, float, dict[str, object]]] = {}
        self.error: str | None = None
        self.profile = json.loads(CONFIG_PATH.read_text())["comparison"]
        self._verifier = FaceVerifier(
            self.profile["minimum_observations"], self.profile["window_seconds"]
        )

    def _refresh(self, force: bool = False) -> None:
        if force:
            self._checked = float("-inf")
            self._gallery_valid = False
        self.models.load()
        if not force and self._gallery_valid and monotonic() - self._checked < 5:
            return
        self._checked = float("-inf")
        people, total = self.registry.repository.list_persons("", 200, 0)
        if total > 200:
            raise ComparisonError("REFERENCE_PERSON_LIMIT_EXCEEDED")
        people = [person for person in people if person.status == "ACTIVE"]
        references: list[tuple[PersonPublic, StoredFaceImage]] = []
        for person in people:
            approved_images = [
                image
                for image in self.registry.repository.list_images(person.id)
                if image.public.review_state == "APPROVED"
            ]
            references.extend((person, image) for image in approved_images[-5:])
        signature = sha256(
            repr(
                [
                    (p.id, p.updated_at.isoformat(), im.public.id, im.checksum)
                    for p, im in references
                ]
            ).encode()
        ).hexdigest()
        if signature == self._signature and self._gallery_valid and not force:
            self._checked = monotonic()
            return
        gallery = []
        self._gallery_valid = False
        image_results = []
        for person, image in references:
            try:
                payload = self.registry.storage.read(image.storage_key)
            except (OSError, ValueError, ObjectStorageError):
                image_results.append(
                    {
                        "person_id": person.id,
                        "image_id": image.public.id,
                        "state": "IMAGE_UNAVAILABLE",
                    }
                )
                continue
            faces = self.models.analyze(payload)
            state = (
                "READY" if len(faces) == 1 else "NO_USABLE_FACE" if not faces else "MULTIPLE_FACES"
            )
            image_results.append(
                {"person_id": person.id, "image_id": image.public.id, "state": state}
            )
            if len(faces) == 1:
                gallery.append((person.id, image.public.id, faces[0].feature))
        self._gallery, self._image_results = gallery, image_results
        self._people = {person.id: person for person in people}
        self._signature = signature
        self._gallery_valid = True
        self._cached.clear()
        self._verifier = FaceVerifier(
            self.profile["minimum_observations"], self.profile["window_seconds"]
        )
        self._checked = monotonic()
        self.error = None

    def status(self, force: bool = False) -> dict[str, object]:
        if not self._lock.acquire(blocking=False):
            return {"state": "PROCESSING", "model_names": ["YuNet", "SFace"]}
        try:
            try:
                self._refresh(force)
            except ComparisonError as error:
                self.error = str(error)
            return {
                **self.models.status(),
                "state": self.error or ("READY" if self._gallery else "NO_REFERENCE_IMAGES"),
                "reference_people": len({item[0] for item in self._gallery}),
                "reference_images": len(self._gallery),
                "images": self._image_results,
                "comparison": self.profile,
                "engine": "opencv-yunet-sface-v1",
                "reference_revision": self._signature,
            }
        finally:
            self._lock.release()

    def compare(
        self,
        payload: bytes,
        scope: str,
        frame_id: str,
        roi_polygon: list[tuple[float, float]] | None = None,
    ) -> dict[str, object]:
        if not self._lock.acquire(blocking=False):
            raise ComparisonError("RECOGNITION_BUSY")
        try:
            self._refresh()
            now = monotonic()
            self._cached = {k: v for k, v in self._cached.items() if now - v[1] < 3}
            cached = self._cached.get(scope)
            if cached and cached[0] == frame_id:
                return cached[2]
            if not self._gallery:
                faces = self.models.analyze(payload, roi_polygon=roi_polygon)
                if not faces:
                    self._verifier.clear(scope + "/" + self._signature)
                no_reference_result: dict[str, object] = {
                    "state": "NO_REFERENCE_IMAGES",
                    "faces": [
                        {
                            "box": face.box,
                            "state": "UNKNOWN",
                            "similarity": 0.0,
                            "person_id": None,
                            "full_name": None,
                            "student_id": None,
                            "person_number": None,
                        }
                        for face in faces
                    ],
                    "frame_id": frame_id,
                    "engine": "opencv-yunet-sface-v1",
                    "reference_revision": self._signature,
                }
                if len(self._cached) >= 16 and scope not in self._cached:
                    self._cached.pop(next(iter(self._cached)))
                self._cached[scope] = (frame_id, now, no_reference_result)
                self.error = None
                return no_reference_result
            faces = self.models.analyze(payload, roi_polygon=roi_polygon)
            if not faces:
                self._verifier.clear(scope + "/" + self._signature)
            results = []
            for face in faces:
                person_id, score, margin = best_identity(face.feature, self._gallery)
                qualified = (
                    score >= self.profile["cosine_threshold"]
                    and margin >= self.profile["minimum_identity_margin"]
                )
                state = self._verifier.observe(
                    scope + "/" + self._signature,
                    face.box,
                    person_id if qualified else None,
                    frame_id=frame_id,
                    now=now,
                    immediate_unknown=score < self.profile["cosine_threshold"],
                )
                if (
                    score >= self.profile["cosine_threshold"]
                    and margin < self.profile["minimum_identity_margin"]
                ):
                    state = "UNCERTAIN"
                person = self._people.get(person_id or "") if qualified else None
                results.append(
                    {
                        "box": face.box,
                        "state": state,
                        "similarity": round(score, 4),
                        "person_id": person.id if person else None,
                        "full_name": person.full_name if person else None,
                        "student_id": person.student_id if person else None,
                        "person_number": person.person_number if person else None,
                    }
                )
            result: dict[str, object] = {
                "state": "READY" if faces else "NO_FACE",
                "faces": results,
                "frame_id": frame_id,
                "engine": "opencv-yunet-sface-v1",
                "reference_revision": self._signature,
            }
            if len(self._cached) >= 16 and scope not in self._cached:
                self._cached.pop(next(iter(self._cached)))
            self._cached[scope] = (frame_id, now, result)
            self.error = None
            return result
        except ComparisonError as error:
            self.error = str(error)
            raise
        finally:
            self._lock.release()
