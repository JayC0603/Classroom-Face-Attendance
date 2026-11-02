import httpx
from vision_worker.camera_config import (
    ActiveCamera,
    CameraConfigSynchronizer,
    CameraRuntimeManager,
    HttpActiveCameraProvider,
)


class Source:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class Store:
    def __init__(self):
        self.cleared = []

    def clear_camera(self, camera_id):
        self.cleared.append(camera_id)


def test_revision_hot_reload_closes_source_and_clears_camera_state():
    made = []
    store = Store()

    def factory(config):
        source = Source()
        made.append((config, source))
        return source

    manager = CameraRuntimeManager(factory, store)
    first = ActiveCamera("cam-1", "Entrance", "rtsp://secret", "AUTO", True, 5.0, "rev-1")
    second = ActiveCamera("cam-1", "Entrance", "rtsp://replacement", "AUTO", True, 5.0, "rev-2")
    assert manager.apply([first]) == ["cam-1"]
    assert manager.apply([first]) == []
    assert manager.apply([second]) == ["cam-1"]
    assert made[0][1].closed is True
    assert store.cleared == ["cam-1"]


def test_http_provider_uses_worker_token_and_parses_active_revision() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Worker-Token"] == "worker-token"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "cam-1",
                        "name": "Entrance",
                        "rtsp_url": "rtsp://alice:secret@camera/live",
                        "codec": "AUTO",
                        "enabled": True,
                        "sampling_fps": 5.0,
                        "revision": "rev-2",
                    }
                ]
            },
        )

    provider = HttpActiveCameraProvider(
        "http://control-api:8000",
        "worker-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    cameras = provider.fetch()
    assert cameras[0].revision == "rev-2"
    assert cameras[0].rtsp_url == "rtsp://alice:secret@camera/live"


def test_synchronizer_does_not_restart_unchanged_revision() -> None:
    made: list[tuple[ActiveCamera, Source]] = []
    store = Store()

    def factory(config: ActiveCamera) -> Source:
        source = Source()
        made.append((config, source))
        return source

    config = ActiveCamera("cam-1", "Entrance", "rtsp://camera/live", "AUTO", True, 5.0, "r1")

    class Provider:
        def fetch(self) -> list[ActiveCamera]:
            return [config]

    manager = CameraRuntimeManager(factory, store)
    sync = CameraConfigSynchronizer(Provider(), manager)
    assert sync.sync_once() == ["cam-1"]
    assert sync.sync_once() == []
    assert len(made) == 1
    manager.close_all()
    assert made[0][1].closed is True
    assert store.cleared == ["cam-1"]


def test_model_runtime_revision_restarts_camera_reader_before_retirement() -> None:
    made: list[Source] = []
    store = Store()
    config = ActiveCamera("cam-1", "Entrance", "rtsp://camera/live", "AUTO", True, 5.0, "r1")

    def factory(_config: ActiveCamera) -> Source:
        source = Source()
        made.append(source)
        return source

    manager = CameraRuntimeManager(factory, store)
    assert manager.apply([config], runtime_revision="model-1") == ["cam-1"]
    assert manager.apply([config], runtime_revision="model-1") == []
    assert manager.apply([config], runtime_revision="model-2") == ["cam-1"]
    assert made[0].closed is True
    assert made[1].closed is False
    manager.close_all()
