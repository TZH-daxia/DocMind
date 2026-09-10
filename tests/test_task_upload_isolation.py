"""上传原件按任务目录隔离（同名文件不互相覆盖）的测试。"""

from pathlib import Path

from app.config import Settings
from app.schemas.analysis import AnalysisContext
from app.schemas.file import UploadedDocument
from app.service.analysis_service import AnalysisService

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
XLS_MIME = "application/vnd.ms-excel"


def build_service(tmp_path: Path) -> AnalysisService:
    settings = Settings(DOCMIND_DATA_ROOT=tmp_path, deepseek_api_key="test-key")
    return AnalysisService(settings)


async def create_task(
    service: AnalysisService, filename: str, content: bytes, request_id: str
) -> dict:
    return await service.create_task(
        UploadedDocument(filename=filename, content_type=XLS_MIME, content=content),
        request_id,
        "po_order.v1",
        AnalysisContext(),
        auto_start=False,
    )


async def test_same_filename_uploads_keep_separate_copies(tmp_path: Path) -> None:
    """两人上传同名托书时，各自的源文件互不覆盖。"""

    service = build_service(tmp_path)
    content_a = OLE_MAGIC + b"AAAA"
    content_b = OLE_MAGIC + b"BBBB"

    task_a = await create_task(service, "托书.xls", content_a, "req-a")
    task_b = await create_task(service, "托书.xls", content_b, "req-b")

    status_a = service.get_task_status(task_a["task_id"])
    status_b = service.get_task_status(task_b["task_id"])
    path_a = service.file_store.root / status_a["uploaded_path"]
    path_b = service.file_store.root / status_b["uploaded_path"]

    assert path_a != path_b
    assert path_a.read_bytes() == content_a
    assert path_b.read_bytes() == content_b


async def test_uploaded_file_lives_in_task_directory(tmp_path: Path) -> None:
    """原件放在任务目录内，随任务目录一起被保留期清理。"""

    service = build_service(tmp_path)
    task = await create_task(service, "订舱单.xls", OLE_MAGIC + b"X", "req-c")
    status = service.get_task_status(task["task_id"])

    assert status["uploaded_path"].startswith(
        f"parsed_documents/{task['task_id']}/"
    )
    assert (service.file_store.root / status["uploaded_path"]).exists()
