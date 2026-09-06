"""本地文档渲染：把 .pdf/.doc/.xls 转为页面图片，替代 MinerU 解析。

- .pdf 直接用 PyMuPDF 光栅化；
- .doc/.xls 通过 Office COM 导出 PDF 后光栅化，XLS 导出前做合并单元格行高修正防止裁切；
- .xls 在 COM 不可用时回退为纯 Python 合成表格图（xlrd + Pillow，零 Office 依赖）。
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)

MAX_PAGES = 10
DPI = 200
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)
# Excel 垂直对齐常量：顶端（-4160）。底端对齐的合并单元格在高度不足时会从顶部裁切内容。
XL_VERTICAL_ALIGN_TOP = -4160


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

    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store

    def render(self, source_path: Path, source_name: str, task_id: str) -> RenderedDocument:
        """渲染单份文档，返回页面图片与文本层。"""

        stem = self.file_store.source_stem(source_name)
        out_dir = self.file_store.task_dir("parsed_documents", task_id)
        suffix = source_path.suffix.lower()
        if suffix not in {".pdf", ".doc", ".xls"}:
            raise ValueError(f"LOCAL_RENDER_UNSUPPORTED: {suffix}")

        converter = "pymupdf"
        pdf_path = source_path
        if suffix in {".doc", ".xls"}:
            try:
                pdf_path, converter = self._office_to_pdf(source_path, out_dir, stem, suffix)
            except (ImportError, OSError, RuntimeError) as exc:
                if suffix != ".xls":
                    raise RuntimeError(f"LOCAL_RENDER_OFFICE_REQUIRED: {exc}") from exc
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

    def _office_to_pdf(
        self,
        source_path: Path,
        out_dir: Path,
        stem: str,
        suffix: str,
    ) -> tuple[Path, str]:
        """通过 Office COM 把 doc/xls 导出为 PDF，XLS 先做行高修正。"""

        try:
            import pythoncom  # type: ignore[import-untyped]
            import win32com.client  # type: ignore[import-untyped,import-not-found]
        except ImportError as exc:
            raise RuntimeError("pywin32 不可用") from exc

        pdf_path = out_dir / f"{stem}_converted.pdf"
        program = "Word.Application" if suffix == ".doc" else "Excel.Application"
        # COM 必须在调用线程上初始化（asyncio.to_thread 的工作线程默认未初始化）
        pythoncom.CoInitialize()
        try:
            app: Any = win32com.client.DispatchEx(program)
            app.Visible = False
            app.DisplayAlerts = False
            try:
                if suffix == ".xls":
                    workbook = app.Workbooks.Open(str(source_path), ReadOnly=True)
                    for sheet in workbook.Worksheets:
                        self._fix_merged_row_heights(sheet)
                    workbook.ExportAsFixedFormat(0, str(pdf_path))
                    workbook.Close(False)
                    return pdf_path, "excel_com"
                document = app.Documents.Open(str(source_path), ReadOnly=True)
                document.ExportAsFixedFormat(OutputFileName=str(pdf_path), ExportFormat=17)
                document.Close(False)
                return pdf_path, "word_com"
            finally:
                try:
                    app.Quit()
                except Exception as quit_error:  # noqa: BLE001 - COM 退出失败不影响产物
                    logger.debug("Office COM 退出失败：%s", quit_error)
        finally:
            pythoncom.CoUninitialize()

    @staticmethod
    def _fix_merged_row_heights(sheet: Any) -> None:
        """AutoFit 对合并单元格无效，手动按文字量把高度差额补到区域末行。

        合并单元格垂直底端对齐时，区域高度不足会从顶部裁切内容（地址块首行的公司名
        就是这样丢的），因此统一改为顶端对齐；行高只增不减，避免 AutoFit 压缩原表
        已调好的行高，反而让内容溢出。
        """

        used = sheet.UsedRange
        # MergeCells 为 False 表示整表没有合并单元格；混合内容为 None，需继续扫描。
        if used.MergeCells is False:
            logger.debug("工作表没有合并单元格，跳过行高修正")
            return
        for area in LocalDocumentRenderer._collect_merge_areas(used):
            LocalDocumentRenderer._fix_area_row_height(sheet, area)

    @staticmethod
    def _collect_merge_areas(used: Any) -> list[Any]:
        """收集 UsedRange 中不重复的合并区域，已被区域覆盖的列直接跳过。"""

        seen: set[str] = set()
        areas: list[Any] = []
        for row in range(1, used.Rows.Count + 1):
            col = 1
            while col <= used.Columns.Count:
                cell = used.Cells(row, col)
                if not cell.MergeCells:
                    col += 1
                    continue
                area = cell.MergeArea
                address = str(area.Address)
                if address not in seen:
                    seen.add(address)
                    areas.append(area)
                col += area.Columns.Count
        return areas

    @staticmethod
    def _fix_area_row_height(sheet: Any, area: Any) -> None:
        """补足单个合并区域的高度，保持顶端对齐且不压缩原有行高。"""

        address = str(area.Address)
        top = area.Row
        bottom = top + area.Rows.Count - 1
        first_col = area.Column
        area.VerticalAlignment = XL_VERTICAL_ALIGN_TOP
        total_width = sum(
            sheet.Columns(i).ColumnWidth
            for i in range(first_col, first_col + area.Columns.Count)
        )
        original_height = sheet.Rows(top).RowHeight
        area.UnMerge()
        original_width = sheet.Columns(first_col).ColumnWidth
        sheet.Columns(first_col).ColumnWidth = max(total_width, 5)
        sheet.Rows(top).AutoFit()
        needed = sheet.Rows(top).RowHeight
        sheet.Columns(first_col).ColumnWidth = original_width
        sheet.Rows(top).RowHeight = original_height
        merged = sheet.Range(address)
        merged.Merge()
        merged.VerticalAlignment = XL_VERTICAL_ALIGN_TOP
        current = sum(sheet.Rows(i).RowHeight for i in range(top, bottom + 1))
        if needed > current:
            sheet.Rows(bottom).RowHeight += needed - current

    def _render_pages(self, pdf_path: Path, out_dir: Path, stem: str) -> tuple[list[Path], int]:
        """光栅化 PDF 页面（上限 MAX_PAGES），返回图片路径列表。"""

        import pymupdf

        image_paths: list[Path] = []
        with pymupdf.open(pdf_path) as document:
            for index, page in enumerate(document):
                if index >= MAX_PAGES:
                    break
                pixmap = page.get_pixmap(dpi=DPI)
                image_path = out_dir / f"{stem}_page_{index + 1:03d}.png"
                pixmap.save(image_path)
                image_paths.append(image_path)
            page_count = min(len(document), MAX_PAGES)
        return image_paths, page_count

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
        for index, sheet in enumerate(workbook.sheets(), start=1):
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
