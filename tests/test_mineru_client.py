from pathlib import Path

import httpx
import pytest

from app.collector.mineru_client import MinerUApiError, MinerUClient
from app.config import Settings
from app.storage.file_store import FileStore


@pytest.mark.asyncio
async def test_mineru_upload_request_contains_one_file(tmp_path: Path) -> None:
    settings = Settings(DOCMIND_DATA_ROOT=tmp_path, MINERU_API_KEY="test-key")
    client_impl = MinerUClient(settings, FileStore(settings))
    received: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        received["body"] = request.content
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "batch_id": "batch_001",
                    "file_urls": ["https://upload.example/one"],
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=settings.mineru_base_url) as http_client:
        source = tmp_path / "booking.pdf"
        source.write_bytes(b"pdf")
        result = await client_impl._request_upload_url(http_client, source.name, "doc_001")

    assert result["batch_id"] == "batch_001"
    assert result["upload_url"] == "https://upload.example/one"
    payload = received["body"]
    assert isinstance(payload, bytes)
    parsed_payload = __import__("json").loads(payload)
    assert parsed_payload["files"] == [
        {"name": "booking.pdf", "data_id": "doc_001", "is_ocr": True}
    ]
    assert parsed_payload["model_version"] == "vlm"


def test_mineru_archive_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "result.zip"
    import zipfile

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.txt", "unsafe")

    with pytest.raises(MinerUApiError):
        MinerUClient._extract_zip_safely(archive_path, tmp_path / "parsed")
