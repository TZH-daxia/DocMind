"""证据坐标定位：把字段值/证据引文映射回源文档 PDF 的位置。

- 输入是渲染管线用过的 PDF（LibreOffice 转换件或用户上传的原件），其文本层自带坐标；
- 输出是归一化坐标（0~1，相对页面宽高），与页面图片分辨率无关，前端按百分比叠加高亮框；
- 本模块只负责"给定字符串找位置"，匹配优先级（值优先 / 引用兜底 / 区间收窄）由 Service 决定。
"""

from __future__ import annotations

import bisect
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

logger = logging.getLogger(__name__)

# 子串匹配的最小检索词长度：太短的词会满页误命中
MIN_NEEDLE_LENGTH = 2
# 词级精确匹配允许 1 个字符（要求整词相等，如件数 "1" 不会命中 "1PLT"）
MIN_WORD_NEEDLE_LENGTH = 1
# 单个检索词最多返回的命中数，防止短词在大文档上产生海量框
MAX_MATCHES_PER_NEEDLE = 12

# 归一化折叠表：去掉空白与表格符号（VLM 转写会用 | 画表格），统一各类分隔符与全角标点
_FOLD_MAP: dict[int, str | None] = {}
for _char in " \t\r\n\u3000|｜":
    _FOLD_MAP[ord(_char)] = None
for _char in "/\\.~～－−‐‑‒–—" :
    _FOLD_MAP[ord(_char)] = "-"
for _full, _half in (("（", "("), ("）", ")"), ("，", ","), ("：", ":"), ("；", ";"), ("、", ","), ("％", "%")):
    _FOLD_MAP[ord(_full)] = _half


def normalize_needle(text: str) -> str:
    """归一化检索词与页面文本：折叠分隔符、去空白与表格符号、统一大小写。"""

    return str(text).translate(_FOLD_MAP).casefold()


# 切分引用片段的分隔符：只认空白、表格符号与"不会出现在值内部"的标点。
# 刻意不含 . / - ~ _ , —— 它们是值本身的组成部分（0.1828、2026/09/28~2026/10/05、
# Ningbo,China），切开会把值从中间截断，导致上下文片段反而匹配不上。
_SEGMENT_SPLIT = re.compile(r"[\s\u3000|｜\\;；:：、()（）\[\]{}<>\"'`]+")


def _has_token_boundary(segment: str, value: str) -> bool:
    """值在片段中是否以"数字边界"出现。

    防止 `4` 命中 `74kg` 这类串号：要求值两侧都不是数字。
    """

    start = segment.find(value)
    while start >= 0:
        before_ok = start == 0 or not segment[start - 1].isdigit()
        end = start + len(value)
        after_ok = end >= len(segment) or not segment[end].isdigit()
        if before_ok and after_ok:
            return True
        start = segment.find(value, start + 1)
    return False


def quote_segments(quotes: list[str], limit: int = 12) -> list[str]:
    """把引用切成词元，作为定位时的"区域锚点"。

    整条引用常常匹配不上：标签与值可能落在不同文本块、阅读顺序被同行的其它栏
    打断。此时退而用词元锚点（如 `Final Destination`、`Item`）拿到该栏所在的
    区域，再用它把值的多个命中收窄到正确的那一处。
    """

    segments: list[str] = []
    for quote in quotes:
        if not quote:
            continue
        for segment in _SEGMENT_SPLIT.split(quote):
            normalized = normalize_needle(segment)
            if len(normalized) < MIN_NEEDLE_LENGTH or normalized in segments:
                continue
            segments.append(normalized)
            if len(segments) >= limit:
                return segments
    return segments


def context_needles(value: str, quotes: list[str], limit: int = 3) -> list[str]:
    """从证据引用里派生"带上下文的值片段"作为检索词（如 74 → `74kg`）。

    裸值在原文里常常带单位或后缀（`74kg`、`1PLT`、`0.1828CBM`），只搜裸值会
    命中别的同数字内容（如收货人地址里的 `Str. 74`）。引用本身就是原文逐字
    片段，从中截取包含该值的词元，既能命中正确单元格，又比裸值更具体。
    """

    normalized_value = normalize_needle(value)
    if not normalized_value:
        return []
    results: list[str] = []
    for quote in quotes:
        if not quote:
            continue
        for segment in _SEGMENT_SPLIT.split(quote):
            normalized_segment = normalize_needle(segment)
            if len(normalized_segment) <= len(normalized_value):
                continue
            if not _has_token_boundary(normalized_segment, normalized_value):
                continue
            if normalized_segment not in results:
                results.append(normalized_segment)
            if len(results) >= limit:
                return results
    return results


@dataclass(frozen=True)
class LocatedBox:
    """一处命中位置：页码（从 1 开始）与归一化矩形（x, y, w, h）。"""

    page: int
    bbox: tuple[float, float, float, float]

    def intersects(self, other: LocatedBox, tolerance: float = 0.01) -> bool:
        """两个框是否在视觉上重叠（用于用引用框收窄值的位置）。"""

        if self.page != other.page:
            return False
        left = max(self.bbox[0], other.bbox[0]) - tolerance
        top = max(self.bbox[1], other.bbox[1]) - tolerance
        right = min(self.bbox[0] + self.bbox[2], other.bbox[0] + other.bbox[2]) + tolerance
        bottom = min(self.bbox[1] + self.bbox[3], other.bbox[1] + other.bbox[3]) + tolerance
        return right > left and bottom > top

    def near(
        self,
        other: LocatedBox,
        tolerance: float = 0.02,
        max_gap: float = 0.4,
    ) -> bool:
        """是否与另一个框属于同一视觉区域。

        表格里锚点单元格（栏头/相邻栏）与值单元格常常是**并排且不相交**的两格，
        只判相交会漏掉正确位置。这里放宽为：同一页 + 纵向同一行 + 水平相距不远。
        """

        if self.page != other.page:
            return False
        if self.intersects(other):
            return True
        top = max(self.bbox[1], other.bbox[1])
        bottom = min(self.bbox[1] + self.bbox[3], other.bbox[1] + other.bbox[3])
        if bottom + tolerance < top:
            return False
        gap = max(
            other.bbox[0] - (self.bbox[0] + self.bbox[2]),
            self.bbox[0] - (other.bbox[0] + other.bbox[2]),
        )
        return gap <= max_gap


class DocumentTextIndex:
    """单份 PDF 的文本层索引：构建一次，支持多个检索词反复查询。"""

    def __init__(self, pdf_path: Path) -> None:
        self._pages: list[tuple[str, list[int], list[tuple], list[str], float, float]] = []
        with pymupdf.open(pdf_path) as document:
            for page in document:
                rect = page.rect
                words = page.get_text("words")
                parts: list[str] = []
                offsets: list[int] = []
                position = 0
                for word in words:
                    text = normalize_needle(word[4])
                    offsets.append(position)
                    parts.append(text)
                    position += len(text)
                offsets.append(position)
                self._pages.append(
                    (
                        "".join(parts),
                        offsets,
                        words,
                        [normalize_needle(word[4]) for word in words],
                        rect.width,
                        rect.height,
                    )
                )

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def has_text_layer(self) -> bool:
        return any(page[0] for page in self._pages)

    def search(self, needle: str) -> list[LocatedBox]:
        """子串匹配：把检索词归一化后在整页字符流里定位（跨词、跨行均可）。"""

        normalized = normalize_needle(needle)
        if len(normalized) < MIN_NEEDLE_LENGTH:
            return []
        results: list[LocatedBox] = []
        for page_no, page in enumerate(self._pages, start=1):
            text, offsets, words, _word_texts, width, height = page
            start = text.find(normalized)
            while start >= 0 and len(results) < MAX_MATCHES_PER_NEEDLE:
                box = self._box_for_range(page, offsets, words, start, start + len(normalized))
                if box is not None:
                    results.append(LocatedBox(page_no, self._normalize_box(box, width, height)))
                start = text.find(normalized, start + len(normalized))
        return results

    def search_word(self, needle: str) -> list[LocatedBox]:
        """词级精确匹配：要求检索词等于连续若干个完整词，避免短值命中更长的词。"""

        normalized = normalize_needle(needle)
        if len(normalized) < MIN_WORD_NEEDLE_LENGTH:
            return []
        results: list[LocatedBox] = []
        for page_no, page in enumerate(self._pages, start=1):
            _text, _offsets, words, word_texts, width, height = page
            for index in range(len(word_texts)):
                combined = ""
                for cursor in range(index, len(word_texts)):
                    combined += word_texts[cursor]
                    if combined == normalized:
                        box = self._union([words[index], words[cursor]])
                        results.append(
                            LocatedBox(page_no, self._normalize_box(box, width, height))
                        )
                        break
                    if not normalized.startswith(combined):
                        break
                if len(results) >= MAX_MATCHES_PER_NEEDLE:
                    break
        return results

    @staticmethod
    def _box_for_range(page, offsets, words, start: int, end: int) -> tuple | None:
        first = max(0, bisect.bisect_right(offsets, start) - 1)
        last = max(first, bisect.bisect_left(offsets, end) - 1)
        span = words[first : last + 1]
        if not span:
            return None
        return DocumentTextIndex._union(span)

    @staticmethod
    def _union(words: list[tuple]) -> tuple[float, float, float, float]:
        return (
            min(word[0] for word in words),
            min(word[1] for word in words),
            max(word[2] for word in words),
            max(word[3] for word in words),
        )

    @staticmethod
    def _normalize_box(
        box: tuple[float, float, float, float], width: float, height: float
    ) -> tuple[float, float, float, float]:
        if width <= 0 or height <= 0:
            return (0.0, 0.0, 0.0, 0.0)
        return (
            round(box[0] / width, 4),
            round(box[1] / height, 4),
            round((box[2] - box[0]) / width, 4),
            round((box[3] - box[1]) / height, 4),
        )


def resolve_source_pdf(
    data_root: Path,
    task_id: str,
    uploaded_path: str | None,
    source_stem: str,
) -> Path | None:
    """返回渲染页面图片时使用的源 PDF。

    - DOC/XLS 走 LibreOffice：中间件 `<文件名>_converted.pdf` 会留在任务目录；
    - 上传的 PDF 直接光栅化：没有中间件，直接用上传原件；
    - XLS 纯 Python 合成图兜底：没有 PDF，返回 None（该任务无法定位）。
    """

    converted = data_root / "parsed_documents" / task_id / f"{source_stem}_converted.pdf"
    if converted.exists():
        return converted
    if uploaded_path:
        upload = data_root / uploaded_path
        if upload.exists() and upload.suffix.lower() == ".pdf":
            return upload
    return None
