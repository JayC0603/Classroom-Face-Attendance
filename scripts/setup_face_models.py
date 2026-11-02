"""Download official OpenCV Zoo weights, verifying the pinned Git LFS SHA-256."""

import json
from hashlib import sha256
from pathlib import Path

import httpx


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "configuration/models/opencv-face.json").read_text())
    target = root / "data/models/opencv-face"
    target.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for role in ("detector", "recognizer"):
            model = config[role]
            path = target / model["filename"]
            if path.exists() and sha256(path.read_bytes()).hexdigest() == model["sha256"]:
                print(role, "already verified")
                continue
            temporary = path.with_suffix(".download")
            digest = sha256()
            size = 0
            try:
                with client.stream("GET", model["url"]) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as out:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 50 * 1024 * 1024:
                                raise ValueError("MODEL_DOWNLOAD_TOO_LARGE")
                            digest.update(chunk)
                            out.write(chunk)
                if digest.hexdigest() != model["sha256"]:
                    raise ValueError("MODEL_CHECKSUM_MISMATCH")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            license_response = client.get(model["license_url"])
            license_response.raise_for_status()
            (target / f"{role}.LICENSE").write_text(license_response.text, encoding="utf-8")
            print(role, size, "bytes; SHA-256 verified")


if __name__ == "__main__":
    main()
