from io import BytesIO

import pytest
from control_api.browser_stream import BrowserStreamError, BrowserStreamStore
from PIL import Image


def jpeg():
    out = BytesIO()
    Image.new("RGB", (64, 48), "blue").save(out, format="JPEG")
    return out.getvalue()


def test_session_is_exclusive_bounded_latest_only_and_expires():
    now = [1.0]
    store = BrowserStreamStore(clock=lambda: now[0], max_sessions=1)
    session = store.start("camera", "revision")
    with pytest.raises(BrowserStreamError, match="WEBCAM_IN_USE"):
        store.start("camera", "revision")
    with pytest.raises(BrowserStreamError, match="WEBCAM_SESSION_LIMIT"):
        store.start("other", "revision")
    with pytest.raises(BrowserStreamError, match="WEBCAM_SESSION_UNAUTHORIZED"):
        store.push("camera", "revision", "wrong", jpeg())
    first = store.push("camera", "revision", session.token, jpeg())
    second = store.push("camera", "revision", session.token, jpeg())
    assert second.sequence == first.sequence + 1
    assert store.frame("camera", "revision", session.id, after=0) == second
    assert store.frame("camera", "revision", session.id, after=second.sequence) is None
    now[0] += 11
    assert store.current("camera", "revision") is None
    assert store.start("other", "revision").id


def test_revision_change_and_stop_invalidate_frames():
    store = BrowserStreamStore()
    session = store.start("camera", "r1")
    store.push("camera", "r1", session.token, jpeg())
    assert store.current("camera", "r2") is None
    second = store.start("camera", "r2")
    assert second.id != session.id
    store.stop("camera", "r2", second.token)
    assert store.current("camera", "r2") is None


def test_frame_bytes_and_pixels_are_bounded():
    store = BrowserStreamStore()
    session = store.start("camera", "r1")
    for payload, error in (
        (b"x" * (1024 * 1024 + 1), "WEBCAM_FRAME_TOO_LARGE"),
        (b"invalid", "INVALID_WEBCAM_FRAME"),
    ):
        with pytest.raises(BrowserStreamError, match=error):
            store.push("camera", "r1", session.token, payload)


def test_stale_upload_cannot_delete_a_replacement_session():
    store = BrowserStreamStore()
    old = store.start("camera", "r1")
    new = store.start("camera", "r2")
    with pytest.raises(BrowserStreamError):
        store.push("camera", "r1", old.token, jpeg())
    assert store.current("camera", "r2").id == new.id


def test_authoritative_revision_rejects_stale_session_start():
    revision = ["r1"]
    store = BrowserStreamStore(revision_resolver=lambda _: revision[0])
    store.start("camera", "r1")
    revision[0] = "r2"
    replacement = store.start("camera", "r2")
    with pytest.raises(BrowserStreamError, match="WEBCAM_CAMERA_CHANGED"):
        store.start("camera", "r1")
    assert store.current("camera", "r2").id == replacement.id
