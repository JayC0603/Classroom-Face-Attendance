from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

Codec = Literal["AUTO", "H264", "H265"]
Lifecycle = Literal["DRAFT", "ACTIVE"]
CameraSourceType = Literal["rtsp", "local_webcam", "webcam"]
RoiPoint = tuple[float, float]


def validate_roi(points: list[RoiPoint]) -> list[RoiPoint]:
    if points and (len(points) < 3 or len(points) > 32):
        raise ValueError("ROI_POLYGON_REQUIRES_3_TO_32_POINTS")
    if any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in points):
        raise ValueError("ROI_POINTS_MUST_BE_NORMALIZED")
    area = (
        abs(
            sum(
                points[i][0] * points[(i + 1) % len(points)][1]
                - points[(i + 1) % len(points)][0] * points[i][1]
                for i in range(len(points))
            )
        )
        if points
        else 0
    )
    if points and area < 1e-6:
        raise ValueError("ROI_POLYGON_AREA_MUST_BE_NONZERO")
    return points


class CameraCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    source_type: CameraSourceType = "rtsp"
    device_index: int | None = Field(default=None, strict=True, ge=0, le=63)
    rtsp_url: SecretStr | None = None
    codec: Codec = "AUTO"
    enabled: bool = True
    sampling_fps: float = Field(default=5.0, gt=0, le=60)
    display_fps: float = Field(default=15.0, gt=0, le=30)
    roi_polygon: list[RoiPoint] = Field(default_factory=list, max_length=32)
    attendance_enabled: bool = True
    evidence_retention_days: int = Field(default=30, ge=1, le=3650)
    unknown_alert_frames: int = Field(default=5, strict=True, ge=2, le=60)

    @model_validator(mode="after")
    def valid_url(self) -> "CameraCreate":
        if self.source_type == "local_webcam":
            if self.device_index is None:
                self.device_index = 0
        elif self.device_index is not None:
            raise ValueError("device index is only valid for a worker webcam")
        if self.source_type == "rtsp":
            if self.rtsp_url is None:
                raise ValueError("RTSP source requires a URL")
            validate_rtsp_url(self.rtsp_url.get_secret_value())
        else:
            if self.rtsp_url is not None:
                raise ValueError("webcam source cannot have an RTSP URL")
        return self

    @model_validator(mode="after")
    def valid_roi(self) -> "CameraCreate":
        validate_roi(self.roi_polygon)
        return self


class CameraUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str | None = None
    name: str | None = Field(default=None, min_length=1)
    rtsp_url: SecretStr | None = None
    source_type: CameraSourceType | None = None
    device_index: int | None = Field(default=None, strict=True, ge=0, le=63)
    codec: Codec | None = None
    enabled: bool | None = None
    sampling_fps: float | None = Field(default=None, gt=0, le=60)
    display_fps: float | None = Field(default=None, gt=0, le=30)
    roi_polygon: list[RoiPoint] | None = Field(default=None, max_length=32)
    attendance_enabled: bool | None = None
    evidence_retention_days: int | None = Field(default=None, ge=1, le=3650)
    unknown_alert_frames: int | None = Field(default=None, strict=True, ge=2, le=60)

    @model_validator(mode="after")
    def valid_url(self) -> "CameraUpdate":
        if self.rtsp_url is not None:
            validate_rtsp_url(self.rtsp_url.get_secret_value())
        return self

    @model_validator(mode="after")
    def valid_roi(self) -> "CameraUpdate":
        if self.roi_polygon is not None:
            validate_roi(self.roi_polygon)
        return self


class CameraPublic(BaseModel):
    id: str
    name: str
    source_type: CameraSourceType = "rtsp"
    device_index: int | None = None
    rtsp_url: str | None
    codec: Codec
    enabled: bool
    sampling_fps: float
    display_fps: float = 15.0
    roi_polygon: list[RoiPoint] = Field(default_factory=list)
    attendance_enabled: bool = True
    evidence_retention_days: int = 30
    unknown_alert_frames: int = 5
    lifecycle: Lifecycle
    revision: str


class ActiveCameraConfig(BaseModel):
    id: str
    name: str
    rtsp_url: str | None
    codec: Codec
    enabled: bool
    sampling_fps: float
    revision: str
    source_type: CameraSourceType = "rtsp"
    stream_session_id: str | None = None
    device_index: int | None = None
    display_fps: float = 15.0
    roi_polygon: list[RoiPoint] = Field(default_factory=list)
    attendance_enabled: bool = True
    evidence_retention_days: int = 30
    unknown_alert_frames: int = 5


class CameraConnectionResult(BaseModel):
    status: Literal[
        "CONNECTED",
        "AUTHENTICATION_FAILED",
        "UNREACHABLE",
        "TIMEOUT",
        "UNSUPPORTED_STREAM",
        "DECODE_FAILED",
        "BACKEND_UNAVAILABLE",
    ]
    error_category: str | None = None


class CameraConnectionTester(Protocol):
    def test(self, rtsp_url: str) -> CameraConnectionResult: ...


class UnavailableCameraConnectionTester:
    def test(self, rtsp_url: str) -> CameraConnectionResult:
        return CameraConnectionResult(
            status="BACKEND_UNAVAILABLE", error_category="GSTREAMER_UNAVAILABLE"
        )


class CameraSecretProtector(Protocol):
    def protect(self, value: str) -> bytes: ...
    def reveal(self, value: bytes) -> str: ...


class FernetCameraSecretProtector:
    @classmethod
    def generate(cls) -> "FernetCameraSecretProtector":
        return cls(Fernet.generate_key())

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def protect(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode())

    def reveal(self, value: bytes) -> str:
        return self._fernet.decrypt(value).decode()


@dataclass(frozen=True, slots=True)
class CameraRecord:
    id: str
    name: str
    protected_rtsp_url: bytes
    redacted_rtsp_url: str
    codec: Codec
    enabled: bool
    sampling_fps: float
    lifecycle: Lifecycle
    revision: str
    source_type: CameraSourceType = "rtsp"
    device_index: int | None = None
    display_fps: float = 15.0
    roi_polygon: list[RoiPoint] = field(default_factory=list)
    attendance_enabled: bool = True
    evidence_retention_days: int = 30
    unknown_alert_frames: int = 5


class CameraRevisionConflict(ValueError):
    pass


class CameraRepository(Protocol):
    def put(self, record: CameraRecord, expected_revision: str | None = None) -> None: ...
    def get(self, camera_id: str) -> CameraRecord | None: ...
    def list(self) -> list[CameraRecord]: ...
    def delete(self, camera_id: str, expected_revision: str) -> None: ...


class InMemoryCameraRepository:
    def __init__(self) -> None:
        self._records: dict[str, CameraRecord] = {}
        self._lock = RLock()

    def put(self, record: CameraRecord, expected_revision: str | None = None) -> None:
        with self._lock:
            current = self._records.get(record.id)
            if expected_revision is not None and (
                current is None or current.revision != expected_revision
            ):
                raise CameraRevisionConflict("CAMERA_REVISION_CONFLICT")
            self._records[record.id] = record

    def get(self, camera_id: str) -> CameraRecord | None:
        return self._records.get(camera_id)

    def list(self) -> list[CameraRecord]:
        return list(self._records.values())

    def delete(self, camera_id: str, expected_revision: str) -> None:
        with self._lock:
            current = self._records.get(camera_id)
            if current is None:
                raise KeyError(camera_id)
            if current.revision != expected_revision:
                raise CameraRevisionConflict("CAMERA_REVISION_CONFLICT")
            del self._records[camera_id]


class CameraService:
    def __init__(
        self,
        repository: CameraRepository,
        protector: CameraSecretProtector,
        connection_tester: CameraConnectionTester | None = None,
    ) -> None:
        self.repo = repository
        self.protector = protector
        self.tester = connection_tester or UnavailableCameraConnectionTester()

    def create(self, req: CameraCreate) -> CameraPublic:
        uri = req.rtsp_url.get_secret_value() if req.rtsp_url is not None else ""
        rec = CameraRecord(
            str(uuid4()),
            req.name,
            self.protector.protect(uri) if uri else b"",
            redact_rtsp_url(uri) if uri else "",
            req.codec,
            req.enabled,
            req.sampling_fps,
            "DRAFT",
            str(uuid4()),
            req.source_type,
            req.device_index,
            req.display_fps,
            req.roi_polygon,
            req.attendance_enabled,
            req.evidence_retention_days,
            req.unknown_alert_frames,
        )
        self.repo.put(rec)
        return self.public(rec)

    def require(self, camera_id: str) -> CameraRecord:
        rec = self.repo.get(camera_id)
        if rec is None:
            raise KeyError(camera_id)
        return rec

    def update(self, camera_id: str, req: CameraUpdate) -> CameraPublic:
        current = self.require(camera_id)
        if req.expected_revision is not None and current.revision != req.expected_revision:
            raise CameraRevisionConflict("CAMERA_REVISION_CONFLICT")
        source_type = req.source_type or current.source_type
        device_index = (
            req.device_index
            if req.device_index is not None
            else (current.device_index if source_type == "local_webcam" else None)
        )
        uri = (
            req.rtsp_url.get_secret_value()
            if req.rtsp_url is not None
            else (
                self.protector.reveal(current.protected_rtsp_url)
                if source_type == "rtsp" and current.source_type == "rtsp"
                else None
            )
        )
        validated = CameraCreate(
            name=req.name if req.name is not None else current.name,
            source_type=source_type,
            device_index=device_index,
            rtsp_url=SecretStr(uri) if uri is not None else None,
            codec=req.codec if req.codec is not None else current.codec,
            enabled=req.enabled if req.enabled is not None else current.enabled,
            sampling_fps=req.sampling_fps if req.sampling_fps is not None else current.sampling_fps,
            display_fps=req.display_fps if req.display_fps is not None else current.display_fps,
            roi_polygon=req.roi_polygon if req.roi_polygon is not None else current.roi_polygon,
            attendance_enabled=req.attendance_enabled
            if req.attendance_enabled is not None
            else current.attendance_enabled,
            evidence_retention_days=req.evidence_retention_days
            if req.evidence_retention_days is not None
            else current.evidence_retention_days,
            unknown_alert_frames=req.unknown_alert_frames
            if req.unknown_alert_frames is not None
            else current.unknown_alert_frames,
        )
        protected_url = self.protector.protect(uri) if uri is not None else b""
        redacted_url = redact_rtsp_url(uri) if uri is not None else ""
        updated = CameraRecord(
            id=current.id,
            name=req.name if req.name is not None else current.name,
            protected_rtsp_url=protected_url,
            redacted_rtsp_url=redacted_url,
            codec=req.codec if req.codec is not None else current.codec,
            enabled=req.enabled if req.enabled is not None else current.enabled,
            sampling_fps=(
                req.sampling_fps if req.sampling_fps is not None else current.sampling_fps
            ),
            lifecycle="DRAFT",
            revision=str(uuid4()),
            source_type=validated.source_type,
            device_index=validated.device_index,
            display_fps=validated.display_fps,
            roi_polygon=validated.roi_polygon,
            attendance_enabled=validated.attendance_enabled,
            evidence_retention_days=validated.evidence_retention_days,
            unknown_alert_frames=validated.unknown_alert_frames,
        )
        self.repo.put(updated, expected_revision=current.revision)
        return self.public(updated)

    def delete(self, camera_id: str, expected_revision: str) -> None:
        self.require(camera_id)
        self.repo.delete(camera_id, expected_revision)

    def activate(self, camera_id: str) -> CameraPublic:
        current = self.require(camera_id)
        rec = replace(current, lifecycle="ACTIVE", revision=str(uuid4()))
        self.repo.put(rec, expected_revision=current.revision)
        return self.public(rec)

    def test_connection(self, camera_id: str) -> CameraConnectionResult:
        rec = self.require(camera_id)
        if rec.source_type != "rtsp":
            return CameraConnectionResult(
                status="BACKEND_UNAVAILABLE",
                error_category=(
                    "WEBCAM_CONNECT_FROM_BROWSER"
                    if rec.source_type == "webcam"
                    else "LOCAL_WEBCAM_TEST_REQUIRES_WORKER"
                ),
            )
        return self.tester.test(self.protector.reveal(rec.protected_rtsp_url))

    def get_public(self, camera_id: str) -> CameraPublic:
        return self.public(self.require(camera_id))

    def public_list(self) -> list[CameraPublic]:
        return [self.public(r) for r in self.repo.list()]

    def active_list(self) -> list[ActiveCameraConfig]:
        return [
            ActiveCameraConfig(
                id=r.id,
                name=r.name,
                rtsp_url=(
                    self.protector.reveal(r.protected_rtsp_url) if r.source_type == "rtsp" else None
                ),
                codec=r.codec,
                enabled=r.enabled,
                sampling_fps=r.sampling_fps,
                revision=r.revision,
                source_type=r.source_type,
                device_index=r.device_index,
                display_fps=r.display_fps,
                roi_polygon=r.roi_polygon,
                attendance_enabled=r.attendance_enabled,
                evidence_retention_days=r.evidence_retention_days,
                unknown_alert_frames=r.unknown_alert_frames,
            )
            for r in self.repo.list()
            if r.lifecycle == "ACTIVE" and r.enabled
        ]

    @staticmethod
    def public(r: CameraRecord) -> CameraPublic:
        return CameraPublic(
            id=r.id,
            name=r.name,
            rtsp_url=r.redacted_rtsp_url if r.source_type == "rtsp" else None,
            source_type=r.source_type,
            device_index=r.device_index,
            codec=r.codec,
            enabled=r.enabled,
            sampling_fps=r.sampling_fps,
            lifecycle=r.lifecycle,
            revision=r.revision,
            display_fps=r.display_fps,
            roi_polygon=r.roi_polygon,
            attendance_enabled=r.attendance_enabled,
            evidence_retention_days=r.evidence_retention_days,
            unknown_alert_frames=r.unknown_alert_frames,
        )


def validate_rtsp_url(value: str) -> None:
    p = urlsplit(value)
    if p.scheme not in {"rtsp", "rtsps"} or not p.hostname:
        raise ValueError("camera URL must be an absolute rtsp or rtsps URL")


def redact_rtsp_url(value: str) -> str:
    p = urlsplit(value)
    validate_rtsp_url(value)
    host = p.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if p.port is not None:
        host = f"{host}:{p.port}"
    netloc = f"***:***@{host}" if p.username is not None else host
    return urlunsplit((p.scheme, netloc, p.path, "", ""))
