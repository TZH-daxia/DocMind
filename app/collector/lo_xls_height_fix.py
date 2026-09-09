"""LibreOffice UNO 脚本：只把 XLS 第一张表导出为 PDF（展开隐藏行列、修正合并行高）。

由 LibreOffice 自带的 Python 解释器执行（系统 venv 里没有 uno 模块）：

    <LibreOffice>/program/python.exe lo_xls_height_fix.py <port> <xls_path> <pdf_path>

业务上只认第一张表，其余 sheet 一律不导出：它们（尤其是只有边框没有内容的
空表）会变成多余 PDF 页，既拖慢光栅化，又挤占送 VLM 的图片名额。

两步处理，目标都是"内容完整显示"：
1. 取消使用范围内所有隐藏行/列——隐藏行会让跨行合并单元格的内容被裁掉；
2. 合并区域统一改为顶端对齐，临时展开（unmerge + 加宽首列）测出真实所需
   高度后还原，高度差额补到区域末行（只增不减）。
"""

import os
import sys
import time
from typing import Any

import uno  # type: ignore[import-not-found]
from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]

CONNECT_TIMEOUT_SECONDS = 60.0
MIN_AREA_WIDTH = 1270  # 1/100 mm，约 0.35cm，与 COM 版下限对齐
# OptimalHeight 用Latin 字体度量测行高，实际渲染（含 CJK/字体替换）行距更大，
# 实测同一托书样例测量 11.2pt/行 vs 渲染 13pt/行，导致合并区域尾部内容仍被截断，
# 因此对测量结果放大后参与高度比较（只增不减，多余部分表现为留白）
MERGE_HEIGHT_SAFETY_FACTOR = 1.25


def _prop(name: str, value: Any) -> PropertyValue:
    prop = PropertyValue()
    prop.Name = name
    prop.Value = value
    return prop


def _connect(port: str) -> Any:
    """连接到 soffice 的 UNO socket，soffice 启动需要时间所以循环重试。"""

    local_context = uno.getComponentContext()
    resolver = local_context.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local_context
    )
    url = (
        f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
    )
    deadline = time.time() + CONNECT_TIMEOUT_SECONDS
    while True:
        try:
            return resolver.resolve(url)
        except Exception:
            if time.time() > deadline:
                raise
            time.sleep(0.5)


def _fix_area(sheet: Any, area: Any) -> None:
    """补足单个合并区域的高度，保持顶端对齐且不压缩原有行高。"""

    start_row = area.StartRow
    end_row = area.EndRow
    start_col = area.StartColumn
    if start_row == end_row and start_col == area.EndColumn:
        return
    top_row = sheet.Rows.getByIndex(start_row)
    bottom_row = sheet.Rows.getByIndex(end_row)
    merged = sheet.getCellRangeByPosition(
        start_col, start_row, area.EndColumn, end_row
    )
    top_align = uno.Enum("com.sun.star.table.CellVertJustify", "TOP")
    merged.VertJustify = top_align
    total_width = sum(
        sheet.Columns.getByIndex(col).Width
        for col in range(start_col, area.EndColumn + 1)
    )
    original_height = top_row.Height
    merged.merge(False)
    first_col = sheet.Columns.getByIndex(start_col)
    original_width = first_col.Width
    first_col.Width = max(total_width, MIN_AREA_WIDTH)
    top_row.OptimalHeight = True
    needed = int(top_row.Height * MERGE_HEIGHT_SAFETY_FACTOR)
    first_col.Width = original_width
    top_row.Height = original_height
    merged.merge(True)
    merged.VertJustify = top_align
    current = sum(
        sheet.Rows.getByIndex(row).Height for row in range(start_row, end_row + 1)
    )
    if needed > current:
        bottom_row.Height = bottom_row.Height + (needed - current)


def _unhide_used_rows_and_columns(sheet: Any) -> int:
    """取消使用范围内隐藏的行与列，返回处理数量。

    隐藏行是合并单元格内容"显示不全"的常见根因：跨隐藏行的合并区域
    可用高度比可见行高之和小，尾部内容直接被裁掉。
    """

    cursor = sheet.createCursor()
    cursor.gotoEndOfUsedArea(False)
    used = cursor.RangeAddress
    shown = 0
    for row in range(used.EndRow + 1):
        row_obj = sheet.Rows.getByIndex(row)
        if not row_obj.IsVisible:
            row_obj.IsVisible = True
            shown += 1
    for col in range(used.EndColumn + 1):
        col_obj = sheet.Columns.getByIndex(col)
        if not col_obj.IsVisible:
            col_obj.IsVisible = True
            shown += 1
    return shown


def _fix_sheet(sheet: Any) -> int:
    """修正一张工作表的全部合并区域，返回修正数量。"""

    cursor = sheet.createCursor()
    cursor.gotoEndOfUsedArea(False)
    used = cursor.RangeAddress
    seen: set[tuple[int, int, int, int]] = set()
    fixed = 0
    # 行主序扫描时每个合并区域的左上角最先被遇到，配合 seen 去重
    for row in range(used.EndRow + 1):
        for col in range(used.EndColumn + 1):
            cell = sheet.getCellByPosition(col, row)
            if not cell.IsMerged:
                continue
            area_cursor = sheet.createCursorByRange(cell)
            area_cursor.collapseToMergedArea()
            area = area_cursor.RangeAddress
            key = (
                area.StartColumn,
                area.StartRow,
                area.EndColumn,
                area.EndRow,
            )
            if key in seen:
                continue
            seen.add(key)
            _fix_area(sheet, area)
            fixed += 1
    return fixed


def _keep_primary_sheet(document: Any) -> Any:
    """只保留第一张表参与 PDF 导出，返回该表对象。

    优先关闭其余表的可打印标记（不影响首表中跨表引用的公式取值）；该属性
    不可用时退化为移除其余表——storeToURL 是另存为且 close(False) 不回写
    源文件，移除不会影响上传的原始 xls。
    """

    sheets = document.Sheets
    names = list(sheets.getElementNames())
    if not names:
        raise RuntimeError("NO_SHEET")
    primary = sheets.getByName(names[0])
    try:
        if not primary.IsVisible:
            primary.IsVisible = True  # 首表隐藏会导出空白 PDF
        for name in names[1:]:
            sheets.getByName(name).IsPrintable = False
        print("kept sheet by: IsPrintable")
    except Exception:  # noqa: BLE001 - UNO 属性不可用时退化为移除其余表
        for name in names[1:]:
            sheets.removeByName(name)
        print("kept sheet by: removeByName")
    return primary


def main() -> int:
    port, xls_path, pdf_path = sys.argv[1:4]
    context = _connect(port)
    desktop = context.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", context
    )
    document = None
    for _ in range(3):
        document = desktop.loadComponentFromURL(
            uno.systemPathToFileUrl(os.path.abspath(xls_path)),
            "_blank",
            0,
            (_prop("Hidden", True),),
        )
        if document is not None:
            break
        time.sleep(1.0)
    if document is None:
        print("LOAD_FAILED", file=sys.stderr)
        return 2
    try:
        primary = _keep_primary_sheet(document)
        unhidden = _unhide_used_rows_and_columns(primary)
        if unhidden:
            print(f"unhidden rows/cols in {primary.Name}: {unhidden}")
        fixed = _fix_sheet(primary)
        print(f"kept sheet: {primary.Name}")
        print(f"fixed merged areas: {fixed}")
        document.storeToURL(
            uno.systemPathToFileUrl(os.path.abspath(pdf_path)),
            (_prop("FilterName", "calc_pdf_Export"),),
        )
    finally:
        document.close(False)
    try:
        desktop.terminate()
    except Exception as terminate_error:  # noqa: BLE001 - terminate 竞态异常不影响产物
        print(f"desktop.terminate failed: {terminate_error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - 顶层兜底：完整堆栈进 stderr 供渲染器记录
        import traceback

        traceback.print_exc()
        sys.exit(1)
