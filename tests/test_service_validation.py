from pathlib import Path

import pytest

from app.config import Settings
from app.schemas.file import UploadedDocument
from app.service.analysis_service import AnalysisService


def test_pdf_header_is_required(tmp_path: Path) -> None:
    service = object.__new__(AnalysisService)
    service.settings = Settings(DOCMIND_DATA_ROOT=tmp_path)

    with pytest.raises(ValueError, match="FILE_CONTENT_INVALID"):
        service._validate_content(Path("booking.pdf"), b"not a pdf")


def test_legacy_office_header_is_required(tmp_path: Path) -> None:
    service = object.__new__(AnalysisService)
    service.settings = Settings(DOCMIND_DATA_ROOT=tmp_path)

    with pytest.raises(ValueError, match="FILE_CONTENT_INVALID"):
        service._validate_content(Path("booking.xls"), b"not an office file")


def test_xls_is_allowed_when_the_office_header_is_valid(tmp_path: Path) -> None:
    service = object.__new__(AnalysisService)
    service.settings = Settings(DOCMIND_DATA_ROOT=tmp_path)
    office_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

    service._validate_upload_metadata(
        UploadedDocument(
            filename="booking.xls",
            content_type="application/vnd.ms-excel",
            content=office_header,
        )
    )
    service._validate_content(Path("booking.xls"), office_header)
