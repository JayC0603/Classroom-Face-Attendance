from base64 import b64encode
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from threading import Event, Thread
from time import monotonic
from typing import Protocol, cast
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response

from control_api.browser_stream import BrowserStreamError, BrowserStreamStore
from control_api.camera_preview import PreviewStore
from control_api.cameras import CameraService
from control_api.face_presence_policy import FacePresencePolicy
from control_api.recognition_events import RecognitionEventStore
from control_api.simple_face_recognition import ComparisonError


class FaceComparison(Protocol):
    def status(self, force: bool = False) -> dict[str, object]: ...
    def compare(
        self,
        payload: bytes,
        scope: str,
        frame_id: str,
        roi_polygon: list[tuple[float, float]] | None = None,
    ) -> dict[str, object]: ...


class RecognitionMonitor:
    """Process the latest available frame without requiring an open monitoring tab."""

    def __init__(self, tick: Callable[[], None]) -> None:
        self.tick = tick
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        self._thread = Thread(target=self._run, name="face-presence-monitor", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(0.2):
            try:
                self.tick()
            except Exception:
                # No unknown decision is synthesized from camera/DB/model failure.
                continue

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _point_in_roi(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    previous = len(polygon) - 1
    for current, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[previous]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
        previous = current
    return inside


def _inside_roi(face: dict[str, object], polygon: list[tuple[float, float]]) -> bool:
    box = face.get("box")
    if not isinstance(box, list) or len(box) != 4:
        return False
    try:
        x, y, width, height = (float(value) for value in box)
    except (TypeError, ValueError):
        return False
    if not all(0 <= value <= 1 for value in (x, y, width, height)):
        return False
    return _point_in_roi(x + width / 2, y + height / 2, polygon)


def create_simple_recognition_router(
    comparison: FaceComparison,
    cameras: CameraService,
    previews: PreviewStore,
    browser: BrowserStreamStore,
    events: RecognitionEventStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["Automatic face comparison"])
    presence_policy = FacePresencePolicy()

    @router.get("/recognition/simple/status")
    def status(response: Response) -> dict[str, object]:
        response.headers["Cache-Control"] = "private, no-store"
        return comparison.status()

    @router.post("/recognition/simple/refresh")
    def refresh() -> dict[str, object]:
        return comparison.status(force=True)

    def require(camera_id: str, revision: str) -> None:
        try:
            camera = cameras.require(camera_id)
        except KeyError as error:
            raise HTTPException(404, "CAMERA_NOT_FOUND") from error
        if camera.revision != revision or not camera.enabled or camera.lifecycle != "ACTIVE":
            raise HTTPException(409, "CAMERA_NOT_ACTIVE")

    @router.get("/cameras/{camera_id}/recognition")
    def recognize(
        camera_id: str, revision: str, response: Response, include_preview: bool = False
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "private, no-store"
        require(camera_id, revision)
        camera = cameras.require(camera_id)
        snapshot = previews.snapshot(camera_id, revision)
        kind = "worker"
        if camera.source_type == "webcam":
            session = browser.current(camera_id, revision)
            if session is None:
                return {"state": "WAITING_FOR_FRAME", "faces": []}
            try:
                frame = browser.frame(camera_id, revision, session.id, 0)
            except BrowserStreamError:
                return {"state": "WAITING_FOR_FRAME", "faces": []}
            if frame is None or monotonic() * 1000 - frame.timestamp_ms >= 3000:
                return {"state": "WAITING_FOR_FRAME", "faces": []}
            payload, session_id, frame_id = frame.payload, frame.session_id, str(frame.sequence)
            frame_time = frame.timestamp_ms / 1000
            kind = "browser"
            if snapshot and snapshot.session == session.id and snapshot.person_deployment_id:
                payload, session_id, frame_id = (
                    snapshot.payload,
                    snapshot.session,
                    str(snapshot.sequence),
                )
                frame_time = snapshot.received
                kind = "worker"
            else:
                snapshot = None
        else:
            if snapshot is None:
                return {"state": "WAITING_FOR_FRAME", "faces": []}
            payload, session_id, frame_id = (
                snapshot.payload,
                snapshot.session,
                str(snapshot.sequence),
            )
            frame_time = snapshot.received
        deployment_id = snapshot.person_deployment_id if snapshot else ""
        scope = f"{camera_id}/{revision}/{kind}/{deployment_id}/{session_id}"
        try:
            result = comparison.compare(
                payload,
                scope,
                frame_id,
                roi_polygon=camera.roi_polygon if len(camera.roi_polygon) >= 3 else None,
            )
        except ComparisonError as error:
            result = {"state": str(error), "faces": []}
        require(camera_id, revision)  # discard results of a superseded/disabled camera
        if monotonic() - frame_time >= 3:
            return {"state": "WAITING_FOR_FRAME", "faces": []}
        if camera.source_type == "webcam":
            current_session = browser.current(camera_id, revision)
            if current_session is None or current_session.id != session_id:
                return {"state": "WAITING_FOR_FRAME", "faces": []}
        else:
            current_snapshot = previews.snapshot(camera_id, revision)
            if current_snapshot is None or current_snapshot.session != session_id:
                return {"state": "WAITING_FOR_FRAME", "faces": []}
        result = {
            **result,
            "persons": [person.model_dump(mode="json") for person in snapshot.persons]
            if snapshot
            else [],
            "person_deployment_id": deployment_id,
            "person_state": "READY" if deployment_id else "NOT_CONFIGURED",
        }
        faces = result.get("faces")
        if isinstance(faces, list):
            faces = [dict(face) for face in faces if isinstance(face, dict)]
            result["faces"] = faces
        if isinstance(faces, list) and len(camera.roi_polygon) >= 3:
            faces = [
                face
                for face in faces
                if isinstance(face, dict) and _inside_roi(face, camera.roi_polygon)
            ]
            result["faces"] = faces
        persons = cast(list[dict[str, object]], result["persons"])
        if isinstance(faces, list):
            for face in faces:
                if not isinstance(face, dict):
                    continue
                face_box = face.get("box")
                if not isinstance(face_box, list) or len(face_box) != 4:
                    continue
                fx, fy, fw, fh = (float(value) for value in face_box)
                center_x, center_y = fx + fw / 2, fy + fh / 2
                for person in persons:
                    person_box = person.get("box")
                    if not isinstance(person_box, (list, tuple)) or len(person_box) != 4:
                        continue
                    px, py, pw, ph = (float(value) for value in person_box)
                    if px <= center_x <= px + pw and py <= center_y <= py + ph:
                        if face.get("state") == "UNKNOWN":
                            person["identity_state"] = "UNKNOWN"
                        elif (
                            face.get("state") in {"PENDING", "VERIFIED"}
                            and face.get("person_number") is not None
                        ):
                            person["identity_state"] = str(face["state"])
                            person["person_number"] = face["person_number"]
                            person["full_name"] = face.get("full_name")
                            person["student_id"] = face.get("student_id")
                        break
        result["persons"] = persons
        candidates = (
            []
            if result.get("state") == "RECOGNITION_BUSY"
            else presence_policy.evaluate(
                scope=scope,
                frame_id=frame_id,
                now=frame_time,
                faces=cast(list[dict[str, object]], faces) if isinstance(faces, list) else [],
                persons=persons,
                search_ready=result.get("state") == "READY",
                reference_revision=str(result.get("reference_revision", "")),
                threshold=camera.unknown_alert_frames,
            )
        )
        result["alerts"] = []
        result["captures"] = []
        result["unknown_alert_frames"] = camera.unknown_alert_frames
        if events is not None:
            for candidate in candidates:
                event = events.face_events.record(
                    candidate,
                    scope=scope,
                    camera_id=camera_id,
                    camera_name=camera.name,
                    session_id=session_id,
                    payload=payload,
                    retention_days=camera.evidence_retention_days,
                )
                collection = "alerts" if candidate.kind == "UNKNOWN_ALERT" else "captures"
                cast(list[dict[str, object]], result[collection]).append(event)
        if events is not None and snapshot is not None:
            event_faces = result.get("faces", [])
            if not isinstance(event_faces, list):
                event_faces = []
            for face in event_faces:
                if (
                    not isinstance(face, dict)
                    or face.get("state") != "VERIFIED"
                    or not face.get("person_id")
                ):
                    continue
                face_box = face.get("box")
                track_id = None
                if isinstance(face_box, list) and len(face_box) == 4:
                    fx, fy, fw, fh = (float(value) for value in face_box)
                    cx, cy = fx + fw / 2, fy + fh / 2
                    for person in persons:
                        person_box = person.get("box")
                        if not isinstance(person_box, list) or len(person_box) != 4:
                            continue
                        px, py, pw, ph = (float(value) for value in person_box)
                        if px <= cx <= px + pw and py <= cy <= py + ph:
                            track_id = person.get("track_id")
                            break
                events.record_verified(
                    camera_id=camera_id,
                    camera_name=camera.name,
                    session_id=session_id,
                    track_id=track_id,
                    person_id=str(face["person_id"]),
                    full_name=str(face.get("full_name") or ""),
                    student_id=face.get("student_id"),
                    evidence=payload,
                    retention_days=camera.evidence_retention_days,
                    attendance_enabled=camera.attendance_enabled,
                )
        if include_preview:
            return {
                **result,
                "preview": "data:image/jpeg;base64," + b64encode(payload).decode("ascii"),
                "frame_id": frame_id,
                "stream_session_id": session_id,
                "preview_remaining_ms": max(1, int((3 - (monotonic() - frame_time)) * 1000)),
            }
        return result

    if events is not None:
        last_frames: dict[str, tuple[str, str, str]] = {}

        def monitor_once() -> None:
            active = [c for c in cameras.public_list() if c.enabled and c.lifecycle == "ACTIVE"][
                :16
            ]
            for old_id in set(last_frames) - {c.id for c in active}:
                last_frames.pop(old_id, None)
            for current in active:
                try:
                    latest = previews.snapshot(current.id, current.revision)
                    key = (
                        (current.revision, latest.session, str(latest.sequence)) if latest else None
                    )
                    if current.source_type == "webcam":
                        session = browser.current(current.id, current.revision)
                        if session is None:
                            continue
                        if (
                            latest is None
                            or latest.session != session.id
                            or not latest.person_deployment_id
                        ):
                            frame = browser.frame(current.id, current.revision, session.id, 0)
                            key = (
                                (current.revision, session.id, str(frame.sequence))
                                if frame
                                else None
                            )
                    if key is None or last_frames.get(current.id) == key:
                        continue
                    outcome = recognize(current.id, current.revision, Response())
                    if outcome.get("state") not in {"RECOGNITION_BUSY", "WAITING_FOR_FRAME"}:
                        last_frames[current.id] = key
                except Exception:
                    # Keep other cameras running; retry this camera on the next tick.
                    continue

        monitor = RecognitionMonitor(monitor_once)
        router.on_startup.append(monitor.start)
        router.on_shutdown.append(monitor.close)

        @router.get("/recognition/face-events")
        def face_events(
            response: Response,
            camera_id: str | None = None,
            kind: str | None = None,
            start: date | None = None,
            end: date | None = None,
            offset: int = Query(0, ge=0),
            limit: int = Query(200, ge=1, le=200),
        ) -> list[dict[str, object]]:
            response.headers["Cache-Control"] = "private, no-store"
            if kind not in {None, "FACE_CAPTURED", "UNKNOWN_ALERT"}:
                raise HTTPException(422, "INVALID_FACE_EVENT_KIND")
            if start and end and start > end:
                raise HTTPException(422, "INVALID_DATE_RANGE")
            timezone = ZoneInfo("Asia/Ho_Chi_Minh")
            return events.face_events.history(
                camera_id=camera_id,
                kind=kind,
                offset=offset,
                limit=limit,
                start=datetime.combine(start, time.min, timezone).astimezone(UTC).isoformat()
                if start
                else None,
                end=datetime.combine(end + timedelta(days=1), time.min, timezone)
                .astimezone(UTC)
                .isoformat()
                if end
                else None,
            )

        @router.get("/recognition/events")
        def recognition_events(
            start: str | None = None,
            end: str | None = None,
            camera_id: str | None = None,
            person_id: str | None = None,
        ) -> list[dict[str, object]]:
            return cast(
                list[dict[str, object]],
                events.history(start=start, end=end, camera_id=camera_id, person_id=person_id),
            )

        @router.get("/attendance")
        def attendance(
            start: str | None = None,
            end: str | None = None,
            camera_id: str | None = None,
            person_id: str | None = None,
        ) -> list[dict[str, object]]:
            return cast(
                list[dict[str, object]],
                events.attendance_rows(
                    start=start, end=end, camera_id=camera_id, person_id=person_id
                ),
            )

        @router.get("/attendance/export.xlsx")
        def export_attendance(
            start: str | None = None,
            end: str | None = None,
            camera_id: str | None = None,
            person_id: str | None = None,
        ) -> Response:
            rows = events.attendance_rows(
                start=start, end=end, camera_id=camera_id, person_id=person_id
            )
            data = _attendance_xlsx(rows)
            return Response(
                data,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": 'attachment; filename="attendance.xlsx"',
                    "Cache-Control": "no-store",
                },
            )

        @router.get("/recognition/evidence/{key}")
        def evidence(key: str) -> Response:
            content = (
                events.face_events.evidence_content(key)
                if key.startswith("face-")
                else events.evidence_content(key)
            )
            if content is None:
                raise HTTPException(404, "EVIDENCE_NOT_FOUND_OR_EXPIRED")
            return Response(
                content,
                media_type="image/jpeg",
                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
            )

    return router


def _attendance_xlsx(rows: list[dict[str, object]]) -> bytes:
    columns = [
        "attendance_date",
        "student_id",
        "full_name",
        "first_seen_at",
        "last_seen_at",
        "exited_at",
        "camera_name",
    ]

    def cell(value: object, ref: str) -> str:
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value or ""))}</t></is></c>'

    all_rows = [columns] + [[row.get(key, "") for key in columns] for row in rows]
    sheet_rows = []
    for index, values in enumerate(all_rows, 1):
        cells = "".join(cell(value, f"{chr(65 + col)}{index}") for col, value in enumerate(values))
        sheet_rows.append(f'<row r="{index}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(sheet_rows)
        + "</sheetData></worksheet>"
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Attendance" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()
