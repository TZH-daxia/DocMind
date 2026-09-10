"""上传文件类型与魔数校验的行为测试。"""

from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.schemas.file import UploadedDocument
from app.service.analysis_service import AnalysisService

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 8
ZIP_MAGIC = b"PK\x03\x04" + b"\x14\x00\x00\x00" + b"\x00" * 8

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def _data_root_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOCMIND_DATA_ROOT", str(tmp_path / "data"))


def build_service(**settings_kwargs: Any) -> AnalysisService:
    return AnalysisService(Settings(deepseek_api_key="test-key", **settings_kwargs))


@pytest.mark.parametrize(
    ("filename", "magic", "content_type"),
    [
        ("a.pdf", b"%PDF-1.7\n", "application/pdf"),
        ("a.doc", OLE_MAGIC, "application/msword"),
        ("a.docx", ZIP_MAGIC, DOCX_MIME),
        ("a.xls", OLE_MAGIC, "application/vnd.ms-excel"),
        ("a.xlsx", ZIP_MAGIC, XLSX_MIME),
    ],
)
def test_supported_types_pass_validation(
    filename: str, magic: bytes, content_type: str
) -> None:
    service = build_service()
    upload = UploadedDocument(filename=filename, content_type=content_type, content=magic)
    service._validate_upload_metadata(upload)
    service._validate_content(Path(filename), magic)


def test_executable_renamed_to_docx_is_rejected() -> None:
    """改名的可执行文件必须被魔数校验拦下。"""

    service = build_service()
    with pytest.raises(ValueError, match="FILE_CONTENT_INVALID"):
        service._validate_content(Path("a.docx"), b"MZ\x90\x00" + b"\x00" * 16)


def test_docx_with_ole_magic_is_rejected() -> None:
    service = build_service()
    with pytest.raises(ValueError, match="FILE_CONTENT_INVALID"):
        service._validate_content(Path("a.docx"), OLE_MAGIC)


def test_xlsx_with_pdf_magic_is_rejected() -> None:
    service = build_service()
    with pytest.raises(ValueError, match="FILE_CONTENT_INVALID"):
        service._validate_content(Path("a.xlsx"), b"%PDF-1.7\n")


def test_unsupported_extension_is_rejected() -> None:
    service = build_service()
    upload = UploadedDocument(filename="a.jpg", content_type="image/jpeg", content=b"\xff\xd8")
    with pytest.raises(ValueError, match="FILE_TYPE_NOT_SUPPORTED"):
        service._validate_upload_metadata(upload)


def test_oversized_content_is_rejected() -> None:
    """超过大小上限的文件被拒绝（用 1KB 上限免于构造大文件）。"""

    service = build_service(DOCMIND_MAX_FILE_SIZE_BYTES=1024)
    with pytest.raises(ValueError, match="FILE_TOO_LARGE"):
        service._validate_content(Path("a.pdf"), b"%PDF-1.7" + b"\x00" * 1024)


def test_empty_content_is_rejected() -> None:
    service = build_service()
    with pytest.raises(ValueError, match="FILE_EMPTY"):
        service._validate_content(Path("a.xlsx"), b"")
