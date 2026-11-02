from io import BytesIO

import pytest
from control_api.browser_stream import BrowserStreamStore
from control_api.camera_preview import PersonDetection, PreviewStore
from control_api.cameras import (
    CameraCreate,
    CameraService,
    CameraUpdate,
    FernetCameraSecretProtector,
    InMemoryCameraRepository,
)
from control_api.face_presence_policy import FacePresencePolicy
from control_api.recognition_events import RecognitionEventStore
from control_api.simple_recognition_api import create_simple_recognition_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


def face(state="UNKNOWN", x=0.1):
    return {"state": state, "box": [x, 0.1, 0.2, 0.2], "person_id": None}


def observe(policy, number, faces=None, **kwargs):
    return policy.evaluate(
        scope=kwargs.pop("scope", "camera/revision/session"),
        frame_id=str(number),
        now=kwargs.pop("now", number * 0.1),
        faces=[face()] if faces is None else faces,
        persons=kwargs.pop("persons", []),
        search_ready=kwargs.pop("search_ready", True),
        reference_revision=kwargs.pop("reference_revision", "gallery-1"),
        threshold=kwargs.pop("threshold", 5),
        **kwargs,
    )


def alerts(candidates):
    return [item for item in candidates if item.kind == "UNKNOWN_ALERT"]


def test_five_distinct_frames_are_required_and_duplicates_do_not_count():
    policy = FacePresencePolicy()
    for i in range(1, 5):
        assert not alerts(observe(policy, i))
        for _ in range(8):
            assert not alerts(observe(policy, i))
    fifth = observe(policy, 5)
    assert alerts(fifth)[0].consecutive_unknown == 5
    assert observe(policy, 5) == fifth
    assert observe(policy, 6)[0].presence_id == fifth[0].presence_id


@pytest.mark.parametrize(
    "interruption", ["VERIFIED", "PENDING", "UNCERTAIN", "NO_FACE", "ERROR", "NO_REFERENCE"]
)
def test_non_unknown_evidence_breaks_the_streak(interruption):
    policy = FacePresencePolicy()
    for i in range(1, 5):
        observe(policy, i)
    missing = interruption in {"NO_FACE", "ERROR"}
    observe(
        policy,
        5,
        [] if missing else [face(interruption)],
        search_ready=interruption not in {"ERROR", "NO_REFERENCE"},
    )
    for i in range(6, 10):
        assert not alerts(observe(policy, i))
    assert len(alerts(observe(policy, 10))) == 1


def test_tracks_cameras_sessions_and_gallery_revisions_are_isolated():
    policy = FacePresencePolicy()
    persons = [
        {"box": [0, 0, 0.4, 1], "track_id": 10},
        {"box": [0.5, 0, 0.5, 1], "track_id": 20},
    ]
    for i in range(1, 5):
        # Reverse detection order to prove counters follow tracks, not list positions.
        faces = [face("UNKNOWN", 0.1), face("VERIFIED", 0.6)]
        observe(policy, i, faces if i % 2 else list(reversed(faces)), persons=persons)
    candidates = observe(policy, 5, [face("UNKNOWN", 0.1), face("UNKNOWN", 0.6)], persons=persons)
    assert [c.track_id for c in alerts(candidates)] == [10]
    assert not alerts(observe(policy, 6, scope="other-camera/revision/session"))
    assert not alerts(observe(policy, 6, scope="camera/revision/new-session"))
    assert not alerts(observe(policy, 6, reference_revision="gallery-2"))
    assert not alerts(observe(policy, 7, now=10))  # expired presence


def test_missing_track_does_not_borrow_the_other_tracks_counter():
    policy = FacePresencePolicy()
    for i in range(1, 5):
        observe(policy, i, [face(x=0.1), face(x=0.6)])
    observe(policy, 5, [face(x=0.6)])
    candidates = observe(policy, 6, [face(x=0.1), face(x=0.6)])
    assert len(alerts(candidates)) == 1
    assert alerts(candidates)[0].box[0] == 0.6


@pytest.mark.parametrize("threshold", [2, 5, 8])
def test_configured_threshold(threshold):
    policy = FacePresencePolicy()
    for i in range(1, threshold):
        assert not alerts(observe(policy, i, threshold=threshold))
    assert len(alerts(observe(policy, threshold, threshold=threshold))) == 1


def test_gallery_change_on_a_repeated_frame_cancels_previous_alert():
    policy = FacePresencePolicy()
    for i in range(1, 6):
        observe(policy, i)
    assert not alerts(observe(policy, 5, reference_revision="gallery-2"))
    assert not alerts(observe(policy, 6, reference_revision="gallery-2"))


def test_stale_result_does_not_rewind_or_extend_the_counter():
    policy = FacePresencePolicy()
    observe(policy, 2)
    assert observe(policy, 1) == []
    for i in range(3, 6):
        assert not alerts(observe(policy, i))
    assert alerts(observe(policy, 6))


def test_camera_threshold_validation_and_default():
    assert CameraCreate(name="Cam", source_type="webcam").unknown_alert_frames == 5
    for invalid in (0, 1, 61, 2.5, True):
        with pytest.raises(ValueError):
            CameraUpdate(unknown_alert_frames=invalid)


@pytest.fixture
def monitoring(tmp_path):
    cameras = CameraService(InMemoryCameraRepository(), FernetCameraSecretProtector.generate())
    previews, browser = PreviewStore(), BrowserStreamStore()
    events = RecognitionEventStore(tmp_path)

    class Comparison:
        state = "READY"
        faces = [face()]

        def compare(self, *args, **kwargs):
            return {"state": self.state, "faces": self.faces, "reference_revision": "v1"}

    comparison = Comparison()
    app = FastAPI()
    app.include_router(
        create_simple_recognition_router(comparison, cameras, previews, browser, events)
    )
    yield TestClient(app), cameras, previews, browser, events, comparison
    events.close()


def jpeg(color="blue"):
    output = BytesIO()
    Image.new("RGB", (100, 100), color).save(output, "JPEG")
    return output.getvalue()


def test_face_event_filters_page_and_vietnam_day_boundaries(monitoring):
    from control_api.face_presence_policy import FaceEventCandidate

    client, _, _, _, events, _ = monitoring
    store = events.face_events
    for index, instant in enumerate(
        (
            "2026-11-01T16:59:59+00:00",
            "2026-11-01T17:00:00+00:00",
            "2026-11-02T16:59:59+00:00",
            "2026-11-02T17:00:00+00:00",
        )
    ):
        row = store.record(
            FaceEventCandidate("UNKNOWN_ALERT", str(index), index, str(index), (0, 0, 1, 1), 5),
            scope="date-test",
            camera_id="door",
            camera_name="Door",
            session_id="test",
            payload=jpeg(),
            retention_days=30,
        )
        with events._store._db() as db:
            db.execute(
                "UPDATE recognition_face_events SET recognized_at=? WHERE id=?",
                (instant, row["id"]),
            )
    params = {
        "camera_id": "door",
        "kind": "UNKNOWN_ALERT",
        "start": "2026-11-02",
        "end": "2026-11-02",
        "limit": 1,
    }
    first = client.get("/api/v1/recognition/face-events", params=params).json()
    second = client.get("/api/v1/recognition/face-events", params={**params, "offset": 1}).json()
    assert first[0]["recognized_at"] == "2026-11-02T16:59:59+00:00"
    assert second[0]["recognized_at"] == "2026-11-01T17:00:00+00:00"
    assert (
        client.get("/api/v1/recognition/face-events", params={**params, "offset": 2}).json() == []
    )
    assert (
        client.get("/api/v1/recognition/face-events?start=2026-11-03&end=2026-11-02").status_code
        == 422
    )
    assert client.get("/api/v1/recognition/face-events?offset=-1").status_code == 422


@pytest.mark.parametrize("source", ["local_webcam", "webcam"])
def test_entry_capture_then_single_alert_with_face_snapshot_and_no_attendance(monitoring, source):
    client, cameras, previews, browser, events, _ = monitoring
    camera = cameras.activate(cameras.create(CameraCreate(name="Door", source_type=source)).id)
    session = browser.start(camera.id, camera.revision) if source == "webcam" else None
    url = f"/api/v1/cameras/{camera.id}/recognition?revision={camera.revision}"
    for number in range(1, 9):
        if session:
            browser.push(camera.id, camera.revision, session.token, jpeg())
        else:
            previews.put(
                camera.id,
                camera.revision,
                jpeg(),
                "session",
                (PersonDetection(box=(0, 0, 0.5, 1), confidence=0.9, track_id=7),),
                "model-1",
            )
        response = client.get(url)
        assert response.status_code == 200
        result = response.json()
        assert len(result["captures"]) == 1
        assert len(result["alerts"]) == (1 if number >= 5 else 0)
        # Reading a frame repeatedly must never manufacture independent observations.
        for _ in range(3):
            assert len(client.get(url).json()["alerts"]) == len(result["alerts"])
    events.face_events._queue.join()
    rows = client.get("/api/v1/recognition/face-events").json()
    assert len(rows) == 2
    assert {row["kind"] for row in rows} == {"FACE_CAPTURED", "UNKNOWN_ALERT"}
    assert all(row["evidence_status"] == "READY" for row in rows)
    for row in rows:
        evidence = client.get(f"/api/v1/recognition/evidence/{row['evidence_key']}")
        assert evidence.status_code == 200
        with Image.open(BytesIO(evidence.content)) as image:
            assert image.size == (21, 21) or image.size == (20, 20)
    assert events.attendance_rows() == []
    assert (
        client.get("/api/v1/recognition/face-events?kind=UNKNOWN_ALERT").json()[0][
            "consecutive_unknown"
        ]
        == 5
    )


def test_capture_failure_keeps_the_alert_and_retention_removes_only_evidence(
    monitoring, monkeypatch
):
    client, cameras, previews, _, events, _ = monitoring
    camera = cameras.activate(
        cameras.create(CameraCreate(name="Door", source_type="local_webcam")).id
    )
    url = f"/api/v1/cameras/{camera.id}/recognition?revision={camera.revision}"
    store = events.face_events
    original_save = store._save

    def fail(*args):
        raise OSError("storage unavailable")

    monkeypatch.setattr(store, "_save", fail)
    for _ in range(5):
        previews.put(camera.id, camera.revision, jpeg(), "session")
        client.get(url)
    store._queue.join()
    rows = store.history()
    assert len(rows) == 2
    assert all(row["evidence_status"] == "FAILED" for row in rows)
    monkeypatch.setattr(store, "_save", original_save)
    previews.put(camera.id, camera.revision, jpeg(), "new-session")
    result = client.get(url).json()
    store._queue.join()
    entry_id = result["captures"][0]["id"]
    ready = next(row for row in store.history() if row["id"] == entry_id)
    key = ready["evidence_key"]
    with events._store._db() as db:
        db.execute(
            "UPDATE recognition_face_events SET evidence_expires_at=? WHERE id=?",
            ("2000-01-01", entry_id),
        )
    store.expire()
    assert client.get(f"/api/v1/recognition/evidence/{key}").status_code == 404
    expired = next(row for row in store.history() if row["id"] == entry_id)
    assert expired["evidence_status"] == "EXPIRED"


def test_background_monitor_records_alert_without_any_recognition_get(monitoring):
    _, cameras, previews, browser, events, comparison = monitoring
    camera = cameras.activate(
        cameras.create(CameraCreate(name="Door", source_type="local_webcam")).id
    )
    router = create_simple_recognition_router(comparison, cameras, previews, browser, events)
    # Drive the production scheduler deterministically without sleeping or opening UI.
    monitor = router.on_startup[0].__self__
    for _ in range(5):
        previews.put(camera.id, camera.revision, jpeg(), "session")
        monitor.tick()
        monitor.tick()
    events.face_events._queue.join()
    assert len(events.face_events.history(kind="UNKNOWN_ALERT")) == 1


def test_evidence_queue_overflow_keeps_event_history(monitoring, monkeypatch):
    from threading import Event

    from control_api.face_presence_policy import FaceEventCandidate

    *_, events, _comparison = monitoring
    store = events.face_events
    entered, release = Event(), Event()
    original = store._save

    def slow_save(*args):
        entered.set()
        assert release.wait(5)
        original(*args)

    monkeypatch.setattr(store, "_save", slow_save)
    try:
        for number in range(34):
            candidate = FaceEventCandidate(
                "FACE_CAPTURED", str(number), number, str(number), (0.1, 0.1, 0.2, 0.2), 0
            )
            result = store.record(
                candidate,
                scope="scope",
                camera_id="camera",
                camera_name="Door",
                session_id="session",
                payload=jpeg(),
                retention_days=30,
            )
            if number == 0:
                assert entered.wait(2)
        assert result["evidence_status"] == "FAILED"
        assert result["evidence_error"] == "CAPTURE_QUEUE_FULL"
        assert len(store.history()) == 34
    finally:
        release.set()
        store._queue.join()
