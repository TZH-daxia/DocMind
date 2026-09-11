"""文档类型守卫：在调用抽取模型之前判断视觉转写内容是否像空运托书。

针对的是"传错文件"这个高频误操作：以往非托书文件要么以技术错误码失败
（EMPTY_EXTRACTION），要么因为转写内容偏短被当成"空白件"静默产出全空结果，
用户两种情况都看不出真正原因。这里用纯本地特征匹配提前拦下，既不消耗模型
调用，也能给出面向用户的明确提示。

判定只统计"命中了哪些托书特征组"，不做语义理解，并且刻意保守：
- 内容太短（空白件/扫描件/图多字少）时无从判断，一律不拦，交原流程处理；
- 只命中 1 个特征组时可能是其它单据（机票行程单也有 flight），同样放行；
- 只有"内容够长、却连一个托书特征都找不到"时才判定为不是托书。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.collector.evidence_locator import normalize_needle

# 内容短于该长度时无法判断类型：与 Service 判定"抽取空结果可豁免"的
# VLM_MIN_CONTENT_CHARS 取同一口径，避免两处阈值漂移。
MIN_CONTENT_CHARS = 200

# 视为托书所需的最少特征组数（1 组时不确定，放行以免误杀字段稀疏的托书）
MIN_HIT_GROUPS = 2

# 托书特征组：命中组内任意一个词即算命中该组。只收强特征词——name、address、
# date、packages 这类通用词任何文档都可能出现，不入表。
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "参与人": ("shipper", "consignee", "发货人", "收货人", "托运人"),
    "起讫港": (
        "airport of departure",
        "airport of destination",
        "始发站",
        "目的站",
        "始发港",
        "目的港",
        "port of loading",
        "port of discharge",
        "起运地",
        "目的地",
    ),
    "件重体": (
        "件数",
        "no. of packages",
        "no of packages",
        "gross weight",
        "毛重",
        "净重",
        "体积",
        "cbm",
        "chargeable weight",
        "计费重量",
    ),
    "单据航班": (
        "booking",
        "托书",
        "托单",
        "托运书",
        "mawb",
        "hawb",
        "air waybill",
        "flight",
        "航班",
        "提单",
    ),
    "品名": (
        "品名",
        "description of goods",
        "commodity",
        "货物名称",
        "goods description",
    ),
}

DocumentKind = Literal["booking", "uncertain", "undetermined", "mismatch"]

# 面向用户的提示文案（前端按错误码展示标题，正文直接用这条）
MISMATCH_MESSAGE = (
    "该文件不像空运托书：未识别到托运人、起讫港、件数等关键内容，"
    "请重新上传空运托书/托单（Booking）文件"
)

# 视觉转写是 markdown（标题、列表符号、表格竖线），摘要展示前先清掉
_MARKUP_PATTERN = re.compile(r"[#*`>|•\-—]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")


class DocumentTypeMismatchError(RuntimeError):
    """视觉转写内容不像空运托书：提前终止，不进入字段抽取。"""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class DocumentTypeVerdict:
    """文档类型判定结果。"""

    kind: DocumentKind
    hit_groups: tuple[str, ...] = ()

    @property
    def blocks_extraction(self) -> bool:
        """是否应当停止字段抽取。"""

        return self.kind == "mismatch"


def build_content_hint(text: str, limit: int = 80) -> str:
    """把视觉转写内容压成一行摘要，供前端展示"识别到的内容"。"""

    plain = _MARKUP_PATTERN.sub(" ", text or "")
    collapsed = _WHITESPACE_PATTERN.sub(" ", plain).strip()
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}…"


def detect_document_type(vlm_content: str) -> DocumentTypeVerdict:
    """按特征组命中情况判定文档类型（纯本地计算，不调用模型）。"""

    raw = (vlm_content or "").strip()
    if len(raw) < MIN_CONTENT_CHARS:
        # 内容太少：可能是空白件、扫描件或图多字少的非托书，无从判断，不拦
        return DocumentTypeVerdict(kind="undetermined")
    normalized = normalize_needle(raw)
    hit_groups = tuple(
        group
        for group, terms in FEATURE_GROUPS.items()
        if any(normalize_needle(term) in normalized for term in terms)
    )
    if len(hit_groups) >= MIN_HIT_GROUPS:
        return DocumentTypeVerdict(kind="booking", hit_groups=hit_groups)
    if hit_groups:
        return DocumentTypeVerdict(kind="uncertain", hit_groups=hit_groups)
    return DocumentTypeVerdict(kind="mismatch")
