"""LibreOffice 渲染路径的行为测试。"""

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


class _Completed:
    """subprocess.run 返回值的极简替身。"""

    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


def test_resolve_soffice_prefers_configured_path(tmp_path: Path) -> None:
    soffice = tmp_path / "soffice.exe"
    soffice.write_bytes(b"")
    renderer = build_renderer(tmp_path, DOCMIND_SOFFICE_PATH=str(soffice))
    assert renderer._resolve_soffice_path() == soffice


def test_resolve_soffice_raises_for_missing_configured_path(tmp_path: Path) -> None:
    renderer = build_renderer(
        tmp_path, DOCMIND_SOFFICE_PATH=str(tmp_path / "nope.exe")
    )
    with pytest.raises(RuntimeError, match="DOCMIND_SOFFICE_PATH"):
        renderer._resolve_soffice_path()


def test_libreoffice_convert_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    soffice = tmp_path / "soffice.exe"
    soffice.write_bytes(b"")
    renderer = build_renderer(tmp_path, DOCMIND_SOFFICE_PATH=str(soffice))
    out_dir = tmp_path / "task"
    out_dir.mkdir()
    source = tmp_path / "upload.doc"
    source.write_bytes(b"fake doc")

    def fake_run(command: list[str], **kwargs: Any) -> _Completed:
        outdir = Path(command[command.index("--outdir") + 1])
        (outdir / "upload.pdf").write_bytes(b"pdf")
        return _Completed(returncode=0)

    monkeypatch.setattr(
        "app.collector.document_renderer.subprocess.run", fake_run
    )
    pdf_path, converter = renderer._office_to_pdf_libreoffice(source, out_dir, "upload")
    assert converter == "libreoffice"
    assert pdf_path.name == "upload_converted.pdf"
    assert pdf_path.exists()


def test_libreoffice_convert_failure_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    soffice = tmp_path / "soffice.exe"
    soffice.write_bytes(b"")
    renderer = build_renderer(tmp_path, DOCMIND_SOFFICE_PATH=str(soffice))
    out_dir = tmp_path / "task"
    out_dir.mkdir()

    def fake_run(command: list[str], **kwargs: Any) -> _Completed:
        return _Completed(returncode=1, stderr="boom")

    monkeypatch.setattr(
        "app.collector.document_renderer.subprocess.run", fake_run
    )
    with pytest.raises(RuntimeError, match="LIBREOFFICE_CONVERT_FAILED"):
        renderer._office_to_pdf_libreoffice(tmp_path / "a.doc", out_dir, "a")


def test_render_doc_raises_without_libreoffice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LibreOffice 不可用时，doc 明确报错要求安装 LibreOffice。"""

    renderer = build_renderer(tmp_path)
    source = tmp_path / "a.doc"
    source.write_bytes(b"fake doc")

    def raise_runtime(*args: Any, **kwargs: Any) -> tuple[Path, str]:
        raise RuntimeError("LIBREOFFICE_NOT_FOUND")

    monkeypatch.setattr(LocalDocumentRenderer, "_office_to_pdf_libreoffice", raise_runtime)
    with pytest.raises(RuntimeError, match="LOCAL_RENDER_LIBREOFFICE_REQUIRED"):
        renderer.render(source, "a.doc", "task_render_test")


def test_render_xls_falls_back_to_plain_convert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """XLS 的 UNO 修正导出失败时回退到普通 LibreOffice 转换。"""

    renderer = build_renderer(tmp_path)
    source = tmp_path / "a.xls"
    source.write_bytes(b"fake xls")
    plain_pdf = tmp_path / "task_x" / "a_converted.pdf"
    plain_pdf.parent.mkdir()
    plain_pdf.write_bytes(b"%PDF-1.4 minimal")

    def raise_uno(*args: Any, **kwargs: Any) -> tuple[Path, str]:
        raise RuntimeError("LIBREOFFICE_UNO_UNAVAILABLE")

    def fake_plain(
        self: LocalDocumentRenderer,
        source_path: Path,
        out_dir: Path,
        stem: str,
    ) -> tuple[Path, str]:
        return out_dir / f"{stem}_converted.pdf", "libreoffice"

    def fake_pages(
        self: LocalDocumentRenderer,
        pdf_path: Path,
        out_dir: Path,
        stem: str,
    ) -> tuple[list[Path], int]:
        return [], 0

    monkeypatch.setattr(LocalDocumentRenderer, "_xls_to_pdf_libreoffice", raise_uno)
    monkeypatch.setattr(LocalDocumentRenderer, "_office_to_pdf_libreoffice", fake_plain)
    monkeypatch.setattr(LocalDocumentRenderer, "_render_pages", fake_pages)
    rendered = renderer.render(source, "a.xls", "task_x")
    assert rendered.converter == "libreoffice"
    assert rendered.page_count == 0
