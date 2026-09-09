"""本地文档渲染：把 .pdf/.doc/.xls 转为页面图片，替代 MinerU 解析。

- .pdf 直接用 PyMuPDF 光栅化；
- .doc/.docx 用 LibreOffice headless 导出 PDF（跨平台，不依赖 MS Office，可迁 Linux）；
- .xls/.xlsx 优先经 LibreOffice UNO 展开隐藏行列、修正合并单元格行高后导出 PDF，
  失败时回退普通 LibreOffice CLI 转换；
- .xls 在 LibreOffice 完全不可用时回退为纯 Python 合成表格图（xlrd + Pillow，
  仅支持旧版 .xls，.xlsx 必须依赖 LibreOffice）。
- 多 sheet 工作簿一律只取第一张表：其余表（尤其只有边框的空表）会变成多余
  PDF 页，拖慢光栅化并挤占送 VLM 的图片名额。
"""

import logging
import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)

MAX_PAGES = 10
DPI = 200
LIBREOFFICE_TIMEOUT_SECONDS = 180
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
)


@dataclass(frozen=True)
class RenderedDocument:
    """本地渲染产物：页面图片与元数据。"""

    parsed_directory: Path
    metadata_path: Path
    image_paths: list[Path]
    converter: str
    page_count: int


class LocalDocumentRenderer:
    """把上传文档渲染成页面图片，供 VLM 直接读取。"""

    def __init__(self, file_store: FileStore, settings: Settings) -> None:
        self.file_store = file_store
        self.settings = settings

    def render(self, source_path: Path, source_name: str, task_id: str) -> RenderedDocument:
        """渲染单份文档，返回页面图片与文本层。"""

        stem = self.file_store.source_stem(source_name)
        out_dir = self.file_store.task_dir("parsed_documents", task_id)
        suffix = source_path.suffix.lower()
        if suffix not in {".pdf", ".doc", ".docx", ".xls", ".xlsx"}:
            raise ValueError(f"LOCAL_RENDER_UNSUPPORTED: {suffix}")

        converter = "pymupdf"
        pdf_path = source_path
        if suffix in {".doc", ".docx", ".xls", ".xlsx"}:
            try:
                if suffix in {".xls", ".xlsx"}:
                    try:
                        pdf_path, converter = self._xls_to_pdf_libreoffice(
                            source_path, out_dir, stem
                        )
                    except (OSError, RuntimeError, subprocess.SubprocessError) as uno_error:
                        logger.warning(
                            "LibreOffice UNO 行高修正导出失败，回退普通转换：%s", uno_error
                        )
                        pdf_path, converter = self._office_to_pdf_libreoffice(
                            source_path, out_dir, stem
                        )
                else:
                    pdf_path, converter = self._office_to_pdf_libreoffice(
                        source_path, out_dir, stem
                    )
            except (OSError, RuntimeError, subprocess.SubprocessError) as libreoffice_error:
                if suffix != ".xls":
                    raise RuntimeError(
                        f"LOCAL_RENDER_LIBREOFFICE_REQUIRED: {libreoffice_error}"
                    ) from libreoffice_error
                logger.warning(
                    "LibreOffice 转 PDF 失败，XLS 回退纯 Python 合成图：%s",
                    libreoffice_error,
                )
                converter = "synthetic"
        image_paths, page_count = self._render_pages(pdf_path, out_dir, stem)
        if converter == "synthetic":
            image_paths, page_count = self._xls_synthetic_images(source_path, out_dir, stem)
        metadata_path = out_dir / f"{stem}_render_meta.json"
        self.file_store.write_json_atomic(
            metadata_path,
            {
                "source": source_path.name,
                "converter": converter,
                "page_count": page_count,
                "images": [path.name for path in image_paths],
            },
        )
        return RenderedDocument(
            parsed_directory=out_dir,
            metadata_path=metadata_path,
            image_paths=image_paths,
            converter=converter,
            page_count=page_count,
        )

    def _resolve_soffice_path(self) -> Path:
        """定位 soffice 可执行文件：配置优先，其次 PATH 与常见安装位置。"""

        if self.settings.libreoffice_path:
            configured = Path(self.settings.libreoffice_path)
            if configured.is_file():
                return configured
            raise RuntimeError(
                f"LIBREOFFICE_NOT_FOUND: DOCMIND_SOFFICE_PATH 指向的文件不存在：{configured}"
            )
        found = shutil.which("soffice")
        if found:
            return Path(found)
        candidates = (
            *[Path(drive) / "program" / "soffice.exe" for drive in (
                r"C:\Program Files\LibreOffice",
                r"C:\Program Files (x86)\LibreOffice",
            )],
            Path("/usr/bin/soffice"),
            Path("/usr/local/bin/soffice"),
            Path("/opt/libreoffice/program/soffice"),
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise RuntimeError(
            "LIBREOFFICE_NOT_FOUND: 未找到 soffice，请安装 LibreOffice "
            "或通过 DOCMIND_SOFFICE_PATH 指定可执行文件路径"
        )

    def _xls_to_pdf_libreoffice(
        self,
        source_path: Path,
        out_dir: Path,
        stem: str,
    ) -> tuple[Path, str]:
        """XLS 专用：先经 UNO 修正合并单元格行高，再导出 PDF（保真主路径）。

        直接 --convert-to 导出时，高度不足的合并单元格内容会被裁切
        （与当年 COM 行高修正针对的问题相同），因此 XLS 走 UNO 流程。
        """

        soffice = self._resolve_soffice_path()
        lo_python = soffice.parent / ("python.exe" if os.name == "nt" else "python")
        if not lo_python.is_file():
            raise RuntimeError(
                f"LIBREOFFICE_UNO_UNAVAILABLE: 未找到 LibreOffice 自带 Python：{lo_python}"
            )
        script_path = Path(__file__).with_name("lo_xls_height_fix.py")
        pdf_path = out_dir / f"{stem}_converted.pdf"
        self._remove_stale_lock(source_path)
        port = self._free_port()
        profile_dir = Path(tempfile.mkdtemp(prefix="docmind_lo_"))
        process = subprocess.Popen(
            [
                str(soffice),
                "--headless",
                "--norestore",
                "--nologo",
                f"-env:UserInstallation={profile_dir.as_uri()}",
                f"--accept=socket,host=127.0.0.1,port={port};urp;",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self._wait_for_port(port, timeout_seconds=60.0)
            completed = subprocess.run(
                [
                    str(lo_python),
                    str(script_path),
                    str(port),
                    str(source_path),
                    str(pdf_path),
                ],
                capture_output=True,
                text=True,
                timeout=LIBREOFFICE_TIMEOUT_SECONDS,
                check=False,
            )
            if completed.returncode != 0 or not pdf_path.exists():
                detail = (completed.stderr or completed.stdout or "").strip()
                raise RuntimeError(f"LIBREOFFICE_UNO_FAILED: {detail}")
        finally:
            self._stop_soffice(process)
            shutil.rmtree(profile_dir, ignore_errors=True)
        return pdf_path, "libreoffice_uno"

    @staticmethod
    def _remove_stale_lock(source_path: Path) -> None:
        """清理上次 soffice 异常退出遗留的文档锁，否则加载会静默失败。"""

        lock_path = source_path.parent / f".~lock.{source_path.name}#"
        try:
            lock_path.unlink()
            logger.warning("已清理残留的 LibreOffice 锁文件：%s", lock_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("清理 LibreOffice 锁文件失败：%s", exc)

    @staticmethod
    def _free_port() -> int:
        """申请一个临时的本机空闲端口供 UNO socket 使用。"""

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _wait_for_port(port: int, timeout_seconds: float) -> None:
        """等待 soffice 的 UNO socket 开始监听。"""

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                    return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError(
            f"LIBREOFFICE_UNO_TIMEOUT: soffice 未在 {timeout_seconds}s 内监听端口"
        )

    @staticmethod
    def _stop_soffice(process: subprocess.Popen[Any]) -> None:
        """结束本次转换拉起的 soffice 进程（含子进程）。"""

        if process.poll() is not None:
            return
        if os.name == "nt":
            # soffice.exe 会派生 soffice.bin，需整树结束避免残留进程
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def _office_to_pdf_libreoffice(
        self,
        source_path: Path,
        out_dir: Path,
        stem: str,
    ) -> tuple[Path, str]:
        """用 LibreOffice headless 把 doc/xls 统一转换为 PDF（跨平台主路径）。

        使用独立 UserInstallation 临时配置目录，避免与本机正在运行的
        LibreOffice 实例冲突导致转换静默失败。
        """

        soffice = self._resolve_soffice_path()
        profile_dir = Path(tempfile.mkdtemp(prefix="docmind_lo_"))
        command = [
            str(soffice),
            "--headless",
            "--norestore",
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(source_path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=LIBREOFFICE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"LIBREOFFICE_TIMEOUT: 转换超过 {LIBREOFFICE_TIMEOUT_SECONDS}s"
            ) from exc
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)
        produced = out_dir / f"{source_path.stem}.pdf"
        if completed.returncode != 0 or not produced.exists():
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"LIBREOFFICE_CONVERT_FAILED: {detail}")
        pdf_path = out_dir / f"{stem}_converted.pdf"
        produced.replace(pdf_path)
        return pdf_path, "libreoffice"

    def _render_pages(self, pdf_path: Path, out_dir: Path, stem: str) -> tuple[list[Path], int]:
        """光栅化 PDF 页面（上限 MAX_PAGES），过滤近空白页后返回图片路径列表。

        LibreOffice 导出的是整个工作簿：宽表横向溢出、附带 sheet 都会产生
        几乎空白的页面，送 VLM 只会增加噪音，因此按非白像素占比过滤。
        """

        import pymupdf

        threshold = self.settings.render_blank_page_ratio
        image_paths: list[Path] = []
        first_page: Path | None = None
        with pymupdf.open(pdf_path) as document:
            for index, page in enumerate(document):
                if index >= MAX_PAGES:
                    break
                pixmap = page.get_pixmap(dpi=DPI)
                image_path = out_dir / f"{stem}_page_{index + 1:03d}.png"
                pixmap.save(image_path)
                if first_page is None:
                    first_page = image_path
                if self._is_near_blank(image_path, threshold):
                    logger.debug("近空白页已过滤：%s", image_path.name)
                    image_path.unlink()
                    continue
                image_paths.append(image_path)
        if not image_paths and first_page is not None:
            logger.warning("所有页面均为近空白，保留首页：%s", first_page.name)
            image_paths.append(first_page)
        return image_paths, len(image_paths)

    @staticmethod
    def _is_near_blank(image_path: Path, threshold: float) -> bool:
        """按非白像素占比（灰度 < 250）判断页面是否近空白。"""

        from PIL import Image

        with Image.open(image_path) as image:
            histogram = image.convert("L").histogram()
        total = sum(histogram)
        if total == 0:
            return False
        non_white = sum(histogram[:250])
        return non_white / total < threshold

    def _xls_synthetic_images(self, xls_path: Path, out_dir: Path, stem: str) -> tuple[list[Path], int]:
        """无 Office 兜底：用 Pillow 按单元格数据重建表格图（内容无损）。"""

        import xlrd  # type: ignore[import-untyped]
        from PIL import Image, ImageDraw, ImageFont  # type: ignore[import-not-found]

        font_path = next((p for p in FONT_CANDIDATES if Path(p).exists()), None)
        if font_path is None:
            raise RuntimeError("LOCAL_RENDER_FONT_MISSING")
        scale, font_size, line_h, pad = 2, 14 * 2, 20 * 2, 5 * 2
        min_row_h, min_col_w, default_col_w = 26 * 2, 24 * 2, 110 * 2
        font = ImageFont.truetype(font_path, font_size)

        def wrap(text: str, max_px: int) -> list[str]:
            lines, current = [], ""
            for ch in text:
                if current and font.getlength(current + ch) > max_px:
                    lines.append(current)
                    current = ch
                else:
                    current += ch
            lines.append(current)
            return lines

        workbook = xlrd.open_workbook(str(xls_path), formatting_info=True)
        image_paths: list[Path] = []
        # 业务只认第一张表，与 UNO 导出路径保持一致
        for index, sheet in enumerate(workbook.sheets()[:1], start=1):
            if sheet.nrows == 0:
                continue
            span = {
                (rlo, clo): (rhi - rlo, chi - clo)
                for rlo, rhi, clo, chi in sheet.merged_cells
            }
            covered = {
                (r, c)
                for rlo, rhi, clo, chi in sheet.merged_cells
                for r in range(rlo, rhi)
                for c in range(clo, chi)
                if (r, c) != (rlo, clo)
            }
            col_ws = [
                max(min_col_w, int((sheet.colinfo_map.get(c).width / 256 * 7) * scale))
                if sheet.colinfo_map.get(c) is not None
                else default_col_w
                for c in range(sheet.ncols)
            ]
            filled = {
                (r, c)
                for r in range(sheet.nrows)
                for c in range(sheet.ncols)
                if (r, c) not in covered and sheet.cell_value(r, c) not in ("", None)
            }
            row_hs = [min_row_h] * sheet.nrows
            cell_texts: dict[tuple[int, int], tuple[list[str], int, int]] = {}
            for r in range(sheet.nrows):
                for c in range(sheet.ncols):
                    if (r, c) in covered:
                        continue
                    value = sheet.cell_value(r, c)
                    if value in ("", None):
                        continue
                    rowspan, colspan = span.get((r, c), (1, 1))
                    segs = self._format_cell(value).splitlines()
                    width = sum(col_ws[c : c + colspan]) - 2 * pad
                    needed_px = max((font.getlength(s) for s in segs), default=0)
                    if needed_px > width:
                        k = c + colspan
                        extended = width
                        while k < sheet.ncols and (r, k) not in filled and (r, k) not in span:
                            extended += col_ws[k]
                            k += 1
                            if needed_px <= extended - 2 * pad:
                                break
                        width = extended
                        colspan = max(colspan, min(k, sheet.ncols) - c)
                    lines = []
                    for seg in segs:
                        lines.extend(wrap(seg, width) or [""])
                    cell_texts[(r, c)] = (lines, rowspan, colspan)
                    needed_h = len(lines) * line_h + 2 * pad
                    if rowspan == 1:
                        row_hs[r] = max(row_hs[r], needed_h)
                    else:
                        total = sum(row_hs[r : r + rowspan])
                        if needed_h > total:
                            row_hs[r + rowspan - 1] += needed_h - total
            xs = [0]
            for w in col_ws:
                xs.append(xs[-1] + w)
            ys = [0]
            for h in row_hs:
                ys.append(ys[-1] + h)
            image = Image.new("RGB", (xs[-1] + scale, ys[-1] + scale), "white")
            draw = ImageDraw.Draw(image)
            for r in range(sheet.nrows):
                for c in range(sheet.ncols):
                    if (r, c) in covered:
                        continue
                    rowspan, colspan = span.get((r, c), (1, 1))
                    x, y = xs[c], ys[r]
                    w = xs[min(c + colspan, sheet.ncols)] - x
                    h = ys[min(r + rowspan, sheet.nrows)] - y
                    draw.rectangle([x, y, x + w, y + h], outline="#555555", width=scale)
            for (r, c), (lines, rowspan, colspan) in cell_texts.items():
                x, y = xs[c], ys[r]
                h = ys[min(r + rowspan, sheet.nrows)] - y
                ty = y + max(pad, (h - len(lines) * line_h) // 2)
                for line in lines:
                    draw.text((x + pad, ty), line, font=font, fill="black")
                    ty += line_h
            image_path = out_dir / f"{stem}_sheet_{index:03d}.png"
            image.save(image_path)
            image_paths.append(image_path)
        return image_paths, len(image_paths)

    @staticmethod
    def _format_cell(value: Any) -> str:
        """单元格值转显示文本，数字去掉多余的 .0。"""

        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value)
