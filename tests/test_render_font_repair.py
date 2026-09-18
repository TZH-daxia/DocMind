"""无嵌入字体 PDF 的字体修复：判定、重绘与元数据。"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.collector import document_renderer
from app.collector.document_renderer import (
    LocalDocumentRenderer,
    _detect_halfwidth_unembedded_fonts,
    _load_halfwidth_redraw_font,
    _parse_cid_widths,
    _redraw_page_text,
)
from app.config import Settings
from app.storage.file_store import FileStore

# 真实案例的 /W：CID 1-95（ASCII）统一 500，另有若干 CJK 码位
REAL_W = "[1 95 500 814 939 500 7712[500]7716[500]]"


@pytest.fixture(autouse=True)
def _data_root_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOCMIND_DATA_ROOT", str(tmp_path / "data"))


def build_renderer(tmp_path: Path, **settings_kwargs: Any) -> LocalDocumentRenderer:
    settings = Settings(**settings_kwargs)
    return LocalDocumentRenderer(FileStore(settings), settings)


def make_pdf(
    path: Path, lines: list[str], with_rect: bool = False, fontname: str = "helv"
) -> None:
    """造一份正常（字体已嵌入）的 PDF；含中文时须传中国字体名，否则会写成败笔。"""

    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    if with_rect:
        page.draw_rect(pymupdf.Rect(50, 50, 320, 130))
    for index, line in enumerate(lines):
        page.insert_text((60, 80 + index * 18), line, fontname=fontname)
    doc.save(str(path))
    doc.close()


class _StubDocument:
    """只提供 xref_get_key 的文档桩，用于构造字体字典。"""

    def __init__(self, keys: dict[tuple[int, str], str]) -> None:
        self.keys = keys

    def xref_get_key(self, xref: int, key: str) -> tuple[str, str]:
        value = self.keys.get((xref, key))
        return ("null", "null") if value is None else ("array", value)


class _StubPage:
    """只提供 get_fonts / parent 的页面桩，绕开"造不出未嵌入字体 PDF"的限制。"""

    def __init__(
        self, fonts: list[tuple[Any, ...]], keys: dict[tuple[int, str], str]
    ) -> None:
        self._fonts = fonts
        self.parent = _StubDocument(keys)

    def get_fonts(self, full: bool = False) -> list[tuple[Any, ...]]:
        assert full
        return self._fonts


def stub_page(
    extension: str = "n/a",
    font_type: str = "Type0",
    basefont: str = "宋体",
    widths: str = REAL_W,
) -> _StubPage:
    return _StubPage(
        [(4, extension, font_type, basefont, "F0")],
        {(4, "DescendantFonts"): "[5 0 R]", (5, "W"): widths},
    )


# ---------------------------------------------------------------- /W 解析


def test_parse_cid_widths_range_form() -> None:
    widths = _parse_cid_widths(REAL_W, (1, 95))
    assert widths is not None
    assert set(widths) == set(range(1, 96))
    assert set(widths.values()) == {500}


def test_parse_cid_widths_array_form() -> None:
    assert _parse_cid_widths("[1 [278 500 833]]", (1, 95)) == {1: 278, 2: 500, 3: 833}


def test_parse_cid_widths_skips_out_of_range() -> None:
    widths = _parse_cid_widths("[1 95 500 814 939 700]", (1, 95))
    assert widths is not None
    assert set(widths.values()) == {500}


def test_parse_cid_widths_returns_none_on_unsupported_forms() -> None:
    assert _parse_cid_widths("null", (1, 95)) is None
    assert _parse_cid_widths("[1 95 500", (1, 95)) is None
    assert _parse_cid_widths("[1 [[500]]]", (1, 95)) is None
    assert _parse_cid_widths("[1 nope 500]", (1, 95)) is None
    assert _parse_cid_widths("[1 50 500 51]", (1, 95)) is None


# ---------------------------------------------------------------- 判定


def test_detect_hits_unembedded_halfwidth_font() -> None:
    assert _detect_halfwidth_unembedded_fonts(stub_page()) == ["宋体(F0)"]


def test_detect_ignores_embedded_font() -> None:
    assert _detect_halfwidth_unembedded_fonts(stub_page(extension="ttf")) == []


def test_detect_ignores_non_cid_font() -> None:
    assert _detect_halfwidth_unembedded_fonts(stub_page(font_type="TrueType")) == []


def test_detect_ignores_proportional_width() -> None:
    # 声明宽度超过半宽 → 该字体本就该用比例字宽，重绘会修坏版面
    assert _detect_halfwidth_unembedded_fonts(stub_page(widths="[1 95 700]")) == []


def test_detect_ignores_mixed_widths() -> None:
    assert (
        _detect_halfwidth_unembedded_fonts(stub_page(widths="[1 50 500 51 95 700]"))
        == []
    )


def test_detect_ignores_partial_ascii_coverage() -> None:
    assert _detect_halfwidth_unembedded_fonts(stub_page(widths="[1 50 500]")) == []


def test_detect_font_name_stays_json_safe() -> None:
    """字体名是 GBK 原始字节时，要还原成可读文本且能写进 JSON。"""

    # get_fonts 走 latin-1 解码，xref 走代理字符，两种形式都要能还原
    for raw in ("ËÎÌå", "\udccb\udcce\udccc\udce5"):
        hits = _detect_halfwidth_unembedded_fonts(stub_page(basefont=raw))
        assert hits == ["宋体(F0)"]
        assert json.dumps({"fonts": hits}, ensure_ascii=False)


# ---------------------------------------------------------------- 重绘


def test_redraw_keeps_text_and_line_art(tmp_path: Path) -> None:
    import pymupdf

    font = _load_halfwidth_redraw_font()
    if font is None:
        pytest.skip("系统缺少可用于重绘的中文字体")

    pdf = tmp_path / "doc.pdf"
    make_pdf(
        pdf,
        ["发货人Shipper:", "ALCUPA CORPORATION LIMITED"],
        with_rect=True,
        fontname="china-s",
    )
    doc = pymupdf.open(pdf)
    page = doc[0]
    assert _redraw_page_text(page, font) is True

    text = page.get_text("text")
    assert "发货人Shipper:" in text
    assert "ALCUPA CORPORATION LIMITED" in text
    # 矢量表格线必须保留（红action 只删文本）
    assert page.get_drawings()
    page.get_pixmap(dpi=72)
    doc.close()


def test_redraw_empty_page_is_noop(tmp_path: Path) -> None:
    import pymupdf

    font = _load_halfwidth_redraw_font()
    if font is None:
        pytest.skip("系统缺少可用于重绘的中文字体")

    pdf = tmp_path / "blank.pdf"
    make_pdf(pdf, [], with_rect=False)
    doc = pymupdf.open(pdf)
    assert _redraw_page_text(doc[0], font) is True
    doc.close()


# ---------------------------------------------------------------- 编排与元数据


def test_render_pages_repairs_flagged_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    renderer = build_renderer(tmp_path)
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["发货人Shipper:", "ALCUPA CORPORATION LIMITED"], with_rect=True)
    monkeypatch.setattr(
        document_renderer,
        "_detect_halfwidth_unembedded_fonts",
        lambda page: ["宋体(F0)"],
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    images, page_count, font_repair = renderer._render_pages(pdf, out_dir, "doc")

    assert page_count == 1
    assert [p.name for p in images] == ["doc_page_001.png"]
    assert font_repair == {
        "reason": "unembedded_font_uniform_halfwidth",
        "fonts": ["宋体(F0)"],
        "repaired_pages": 1,
        "leftover_text_pages": 0,
        "status": "repaired",
    }


def test_render_writes_font_repair_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    renderer = build_renderer(tmp_path)
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["发货人Shipper:", "ALCUPA CORPORATION LIMITED"])
    monkeypatch.setattr(
        document_renderer,
        "_detect_halfwidth_unembedded_fonts",
        lambda page: ["宋体(F0)"],
    )

    rendered = renderer.render(pdf, "doc.pdf", "task_font_repair")

    metadata = json.loads(rendered.metadata_path.read_text(encoding="utf-8"))
    assert metadata["converter"] == "pymupdf"
    assert metadata["images"] == [path.name for path in rendered.image_paths]
    assert metadata["font_repair"]["status"] == "repaired"
    assert metadata["font_repair"]["fonts"] == ["宋体(F0)"]


def test_render_omits_font_repair_metadata_for_clean_pdf(tmp_path: Path) -> None:
    renderer = build_renderer(tmp_path)
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["Booking for GGK26"])

    rendered = renderer.render(pdf, "doc.pdf", "task_clean")

    metadata = json.loads(rendered.metadata_path.read_text(encoding="utf-8"))
    assert "font_repair" not in metadata
