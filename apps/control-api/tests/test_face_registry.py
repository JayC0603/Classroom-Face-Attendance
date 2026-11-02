import asyncio
from hashlib import sha256
from io import BytesIO
from typing import Annotated

import pytest
from control_api.face_image_storage import FaceImageStorage
from control_api.face_registry import FaceRegistryService, InMemoryFaceRegistryRepository
from control_api.face_registry_api import create_face_registry_router
from control_api.face_upload_limits import FaceUploadBodyLimit, FaceUploadRoute
from fastapi import APIRouter, FastAPI, File, UploadFile
from fastapi.testclient import TestClient
from PIL import Image


class MemoryObjectStorage:
    def __init__(self):
        self.objects = {}

    def put(self, key, content, content_type):
        assert content_type == "image/jpeg"
        self.objects[key] = content

    def get(self, key):
        return self.objects[key]

    def iter_bytes(self, key, chunk_size=1024 * 1024):
        content = self.get(key)
        for offset in range(0, len(content), chunk_size):
            yield content[offset:offset + chunk_size]

    def delete(self, key):
        self.objects.pop(key, None)

    def health_check(self):
        return None


def picture(fmt="PNG", size=(48, 48)):
    out = BytesIO()
    Image.new("RGB", size, "red").save(out, format=fmt)
    return out.getvalue()


@pytest.fixture
def registry(tmp_path):
    repo = InMemoryFaceRegistryRepository()
    storage = FaceImageStorage(tmp_path / "images")
    service = FaceRegistryService(repo, storage)
    app = FastAPI()
    app.add_middleware(FaceUploadBodyLimit)
    app.include_router(create_face_registry_router(service))
    with TestClient(app) as client:
        yield client, repo, storage


def person(client, student_id="SV001", full_name="Nguyen An"):
    response = client.post(
        "/api/v1/persons",
        json={
            "full_name": f"  {full_name}  ",
            "student_id": student_id,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_register_search_and_duplicate_student_id(registry):
    client, _, _ = registry
    value = person(client)
    assert value["full_name"] == "Nguyen An"
    assert value["recognition_ready"] is False
    assert value["image_count"] == 0
    assert value["person_number"] == 1
    second = person(client, "OTHER002", "Other User")
    assert second["person_number"] == 2
    duplicate = client.post(
        "/api/v1/persons",
        json={
            "full_name": "Another Name",
            "student_id": " sv001 ",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "STUDENT_ID_ALREADY_EXISTS"
    for query in ("nguyen", "v00"):
        result = client.get("/api/v1/persons", params={"search": query}).json()
        assert result["total"] == 1
        assert result["items"][0]["id"] == value["id"]
    assert client.get("/api/v1/persons?offset=1").json()["items"][0]["id"] == second["id"]
    assert client.get("/api/v1/persons?limit=101").status_code == 422
    assert (
        client.post(
            "/api/v1/persons",
            json={"full_name": "  ", "student_id": "x"},
        ).status_code
        == 422
    )


def test_face_image_content_uses_private_object_storage(tmp_path):
    objects = MemoryObjectStorage()
    repo = InMemoryFaceRegistryRepository()
    storage = FaceImageStorage(tmp_path / "unused", object_storage=objects)
    service = FaceRegistryService(repo, storage)
    app = FastAPI()
    app.include_router(create_face_registry_router(service))
    with TestClient(app) as client:
        owner = person(client)
        uploaded = client.post(
            f"/api/v1/persons/{owner['id']}/face-images",
            files={"files": ("face.png", picture())},
        ).json()["results"][0]["image"]
        response = client.get(uploaded["content_url"])

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert Image.open(BytesIO(response.content)).size == (48, 48)
    assert all(
        "minio" not in str(value).lower() and "s3" not in str(value).lower()
        for value in uploaded.values()
    )
    assert list(objects.objects) and not (tmp_path / "unused").exists()


def test_legacy_face_image_moves_to_minio_and_keeps_local_source(registry):
    from control_api.face_registry import migrate_legacy_face_images

    client, repo, storage = registry
    owner = person(client, "LEGACY")
    uploaded = client.post(
        f"/api/v1/persons/{owner['id']}/face-images",
        files={"files": ("face.png", picture())},
    ).json()["results"][0]["image"]
    local_image = repo.list_images(owner["id"])[0]
    original = storage.path(local_image.storage_key)
    minio = MemoryObjectStorage()

    assert migrate_legacy_face_images(repo, minio, storage.root) == 1

    migrated = repo.list_images(owner["id"])[0]
    assert migrated.storage_key == f"{migrated.checksum[:2]}/{uploaded['id'].replace('-', '')}.jpg"
    assert minio.objects[migrated.storage_key] == original.read_bytes()
    assert original.is_file()
    assert migrate_legacy_face_images(repo, minio, storage.root) == 0


def test_upload_gallery_partial_success_and_ownership(registry):
    client, repo, storage = registry
    owner = person(client)
    url = f"/api/v1/persons/{owner['id']}/face-images"
    response = client.post(
        url,
        files=[
            ("files", ("../../face.png", picture(), "application/octet-stream")),
            ("files", ("bad.png", b"not an image", "image/png")),
            ("files", ("second.jpg", picture("JPEG"), "image/jpeg")),
        ],
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert [v["status"] for v in results] == ["STORED_PENDING", "REJECTED", "STORED_PENDING"]
    assert results[1]["error_category"] == "INVALID_IMAGE"
    gallery = client.get(url).json()["items"]
    assert len(gallery) == 2
    assert all(item["processing_state"] == "PENDING_PROCESSING" for item in gallery)
    assert gallery[0]["original_filename"] == "face.png"
    assert "storage_key" not in str(response.json())
    assert "embedding" not in str(response.json())
    content = client.get(gallery[0]["content_url"])
    assert content.headers["content-type"] == "image/jpeg"
    assert content.headers["cache-control"] == "private, no-store"
    assert Image.open(BytesIO(content.content)).getexif() == {}
    other = person(client, "SV002")
    assert (
        client.get(gallery[0]["content_url"].replace(owner["id"], other["id"])).status_code == 404
    )
    assert client.get(f"/api/v1/persons/{owner['id']}").json()["image_count"] == 2
    assert len(list(storage.root.glob("*.jpg"))) == 2
    for stored in repo.list_images(owner["id"]):
        assert stored.checksum == sha256(storage.path(stored.storage_key).read_bytes()).hexdigest()


def test_upload_limits_and_unknown_person(registry):
    client, _, _ = registry
    owner = person(client)
    url = f"/api/v1/persons/{owner['id']}/face-images"
    assert (
        client.post(
            "/api/v1/persons/missing/face-images",
            files={
                "files": ("x.png", picture()),
            },
        ).status_code
        == 404
    )
    assert client.post(url, files=[("files", ("x.png", picture()))] * 11).status_code == 422
    oversized = client.post(url, files={"files": ("big.png", b"x" * (10 * 1024 * 1024 + 1))})
    assert oversized.json()["results"][0]["error_category"] == "IMAGE_TOO_LARGE"
    unsupported = client.post(url, files={"files": ("x.png", picture("BMP"))})
    assert unsupported.json()["results"][0]["error_category"] == "UNSUPPORTED_IMAGE_FORMAT"
    too_large = client.post(url, files={"files": ("x.png", picture(size=(4500, 4500)))})
    assert too_large.json()["results"][0]["error_category"] == "IMAGE_DIMENSIONS_EXCEEDED"


def test_database_failure_cleans_file_and_reports_technical_error(registry, monkeypatch):
    client, repo, storage = registry
    owner = person(client)

    def fail(value):
        raise RuntimeError("database private details")

    monkeypatch.setattr(repo, "add_image", fail)
    result = client.post(
        f"/api/v1/persons/{owner['id']}/face-images",
        files={
            "files": ("face.png", picture()),
        },
    ).json()["results"][0]
    assert result["error_category"] == "IMAGE_SAVE_FAILED"
    assert result["status"] == "FAILED"
    assert list(storage.root.iterdir()) == []


def test_exif_orientation_and_metadata_removed(registry):
    client, _, _ = registry
    owner = person(client)
    out = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private author"
    Image.new("RGB", (32, 48), "green").save(out, format="JPEG", exif=exif)
    result = client.post(
        f"/api/v1/persons/{owner['id']}/face-images",
        files={
            "files": ("face.jpg", out.getvalue()),
        },
    ).json()["results"][0]
    assert result["status"] == "STORED_PENDING"
    assert (result["image"]["width"], result["image"]["height"]) == (48, 32)
    decoded = Image.open(BytesIO(client.get(result["image"]["content_url"]).content))
    assert len(decoded.getexif()) == 0


def test_multiframe_png_rejected(registry):
    client, _, _ = registry
    owner = person(client)
    out = BytesIO()
    Image.new("RGB", (32, 32), "red").save(
        out,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (32, 32), "blue")],
    )
    result = client.post(
        f"/api/v1/persons/{owner['id']}/face-images",
        files={
            "files": ("animated.png", out.getvalue()),
        },
    ).json()["results"][0]
    assert result["error_category"] == "MULTIFRAME_IMAGE_NOT_SUPPORTED"


def test_webcam_capture_source_is_preserved(registry):
    client, _, _ = registry
    owner = person(client)
    url = f"/api/v1/persons/{owner['id']}/face-images"
    result = client.post(
        url,
        data={"source": "CAMERA_CAPTURE"},
        files={
            "files": ("webcam.jpg", picture("JPEG"), "image/jpeg"),
        },
    )
    assert result.status_code == 200
    assert result.json()["results"][0]["image"]["source"] == "CAMERA_CAPTURE"
    assert client.get(url).json()["items"][0]["source"] == "CAMERA_CAPTURE"
    invalid = client.post(
        url,
        data={"source": "FAKE"},
        files={
            "files": ("webcam.jpg", picture("JPEG"), "image/jpeg"),
        },
    )
    assert invalid.status_code == 422


def test_streamed_request_is_bounded_before_multipart_spooling(tmp_path):
    service = FaceRegistryService(
        InMemoryFaceRegistryRepository(),
        FaceImageStorage(tmp_path / "images"),
    )
    app = FastAPI()
    app.add_middleware(FaceUploadBodyLimit, max_bytes=2048)
    app.include_router(create_face_registry_router(service))
    with TestClient(app) as client:
        owner = person(client)
        url = f"/api/v1/persons/{owner['id']}/face-images"

        def chunks():
            yield (
                b'--boundary\r\nContent-Disposition: form-data; name="files"; '
                b'filename="face.png"\r\n\r\n'
            )
            for _ in range(10):
                yield b"x" * 512
            yield b"\r\n--boundary--\r\n"

        streamed = client.post(
            url,
            content=chunks(),
            headers={
                "Content-Type": "multipart/form-data; boundary=boundary",
            },
        )
        assert streamed.status_code == 413
        assert streamed.json()["detail"] == "UPLOAD_REQUEST_TOO_LARGE"
        assert client.post(url, content=b"x" * 2049).status_code == 413
        assert service.repository.list_images(owner["id"]) == []


def test_student_upload_route_enforces_ingress_body_and_file_count_limits():
    app = FastAPI()
    app.add_middleware(FaceUploadBodyLimit, max_bytes=10000)
    router = APIRouter(route_class=FaceUploadRoute)

    @router.post("/api/v1/me/face-images")
    async def upload(files: Annotated[list[UploadFile], File()]) -> dict[str, int]:
        return {"count": len(files)}

    app.include_router(router)
    with TestClient(app) as client:
        def chunks():
            yield (
                b'--boundary\r\nContent-Disposition: form-data; name="files"; '
                b'filename="face.png"\r\n\r\n'
            )
            for _ in range(10):
                yield b"x" * 2048
            yield b"\r\n--boundary--\r\n"

        streamed = client.post(
            "/api/v1/me/face-images",
            content=chunks(),
            headers={"Content-Type": "multipart/form-data; boundary=boundary"},
        )
        assert streamed.status_code == 413
        too_many = client.post(
            "/api/v1/me/face-images",
            files=[("files", (f"{index}.jpg", b"x", "image/jpeg")) for index in range(21)],
        )
        assert too_many.status_code == 422


def test_ingress_limit_closes_temp_file_after_earlier_chunks(tmp_path, monkeypatch):
    import starlette.formparsers as parsers

    created_files = []
    original_file = parsers.SpooledTemporaryFile

    def track_file(*args, **kwargs):
        file = original_file(*args, **kwargs)
        created_files.append(file)
        return file

    monkeypatch.setattr(parsers, "SpooledTemporaryFile", track_file)
    service = FaceRegistryService(
        InMemoryFaceRegistryRepository(),
        FaceImageStorage(tmp_path / "images"),
    )
    app = FastAPI()
    app.add_middleware(FaceUploadBodyLimit, max_bytes=2048)
    app.include_router(create_face_registry_router(service))
    with TestClient(app) as client:
        owner = person(client)
    messages = iter(
        [
            {
                "type": "http.request",
                "body": (
                    b'--boundary\r\nContent-Disposition: form-data; name="files"; '
                    b'filename="face.png"\r\n\r\n'
                ),
                "more_body": True,
            },
            *[{"type": "http.request", "body": b"x" * 512, "more_body": True} for _ in range(6)],
            {"type": "http.request", "body": b"\r\n--boundary--\r\n", "more_body": False},
        ]
    )
    responses = []

    async def receive():
        return next(messages)

    async def send(message):
        responses.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "scheme": "http",
                "method": "POST",
                "root_path": "",
                "path": f"/api/v1/persons/{owner['id']}/face-images",
                "query_string": b"",
                "headers": [(b"content-type", b"multipart/form-data; boundary=boundary")],
                "server": ("testserver", 80),
                "client": ("testclient", 123),
            },
            receive,
            send,
        )
    )
    assert responses[0]["status"] == 413
    assert created_files and all(file.closed for file in created_files)
    assert service.repository.list_images(owner["id"]) == []
