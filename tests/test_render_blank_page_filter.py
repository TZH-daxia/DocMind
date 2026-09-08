"""近空白页过滤的行为测试。"""

import os
from pathlib import Path
from typing import Any

import pytest

from app.collector.document_renderer import LocalDocumentRenderer
from app.config import Settings
from app.storage.file_store import FileStore


@pytest.fixture(autouse=True)
def _data_root_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOCMIND_DATA_ROOT", str(tmp_path / "data"))


def build_renderer(tmp_path: Path, **settings_kwargs: Any) -> LocalDocumentRenderer:
    settings = Settings(**settings_kwargs)
    return LocalDocumentRenderer(FileStore(settings), settings)


def _make_pdf(path: Path, text_pages: list[str], blank_pages: int) -> None:
    import pymupdf

    doc = pymupdf.open()
    for text in text_pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    for _ in range(blank_pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def test_render_pages_filters_blank_page(tmp_path: Path) -> None:
    renderer = build_renderer(tmp_path)
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["Booking for GGK26"], blank_pages=1)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    images, page_count = renderer._render_pages(pdf, out_dir, "doc")
    assert page_count == 1
    assert [p.name for p in images] == ["doc_page_001.png"]


def test_render_pages_zero_threshold_disables_filter(tmp_path: Path) -> None:
    renderer = build_renderer(
        tmp_path, DOCMIND_RENDER_BLANK_PAGE_RATIO=0.0
    )
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["Booking for GGK26"], blank_pages=1)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    images, page_count = renderer._render_pages(pdf, out_dir, "doc")
    assert page_count == 2
    assert len(images) == 2
    assert os.path.exists(out_dir / "doc_page_002.png")


def test_render_pages_all_blank_keeps_first_page(tmp_path: Path) -> None:
    renderer = build_renderer(tmp_path)
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["", ""], blank_pages=0)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    images, page_count = renderer._render_pages(pdf, out_dir, "doc")
    assert page_count == 1
    assert [p.name for p in images] == ["doc_page_001.png"]
