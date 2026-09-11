"""委托客户主数据索引与确定性匹配（纯本地，不依赖模型）。

匹配顺序：客户 ID（数字）→ 客户编码 → 客户名称 → 英文名 → 名称/编码包含。
真实数据（PubFCustom）里 usr_name / usr_code 带前导空格，因此索引与查询
都要先归一化（去空白与标点、统一大写）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.schemas.customer import CustomerCandidate, CustomerRecord

_NON_ALNUM = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
# 包含匹配的最小长度：太短的名字（如"中"）会命中一大片客户
MIN_CONTAINS_LENGTH = 4


def normalize_customer_text(text: str) -> str:
    """归一化：去空白与标点、统一大写（保留中文）。"""

    return _NON_ALNUM.sub("", str(text or "")).upper()


def is_customer_available(record: CustomerRecord) -> bool:
    """可用客户判定：与 poOrder 新增订单一致（启用且不参与新业务的排除）。"""

    return record.usr_status == 1 and record.customxz != 2


@dataclass(frozen=True)
class CustomerLookupResult:
    """一次本地匹配的结果。"""

    kind: Literal["unique", "ambiguous", "not_found"]
    candidates: tuple[CustomerCandidate, ...] = ()
    matched_by: str | None = None


class CustomerReferenceIndex:
    """委托客户索引：构建一次，支持反复查询。"""

    def __init__(self, records: list[CustomerRecord]) -> None:
        self._by_id: dict[str, CustomerRecord] = {}
        self._by_code: dict[str, list[CustomerRecord]] = {}
        self._by_name: dict[str, list[CustomerRecord]] = {}
        self._by_ename: dict[str, list[CustomerRecord]] = {}
        for record in records:
            if not record.id:
                continue
            self._by_id[record.id] = record
            self._index_into(self._by_code, record.usr_code, record)
            self._index_into(self._by_name, record.usr_name, record)
            self._index_into(self._by_ename, record.ename, record)

    @property
    def size(self) -> int:
        return len(self._by_id)

    @staticmethod
    def _index_into(
        target: dict[str, list[CustomerRecord]], text: str, record: CustomerRecord
    ) -> None:
        key = normalize_customer_text(text)
        if key:
            target.setdefault(key, []).append(record)

    def lookup(self, raw_value: str) -> CustomerLookupResult:
        text = str(raw_value or "").strip()
        if not text:
            return CustomerLookupResult("not_found")
        # 1) 客户 ID（前端下拉选中后回填的就是数字 id）
        if text.isdigit():
            record = self._by_id.get(text)
            if record is not None:
                return CustomerLookupResult(
                    "unique", (self._to_candidate(record),), "id"
                )
        # 2) 编码 / 名称 / 英文名精确
        key = normalize_customer_text(text)
        for index, matched_by in (
            (self._by_code, "usr_code"),
            (self._by_name, "usr_name"),
            (self._by_ename, "ename"),
        ):
            hits = index.get(key)
            if hits:
                return self._finish(hits, matched_by)
        # 3) 名称/编码双向包含（`XXP` 这种简写、名称带后缀的场景）
        if len(key) >= MIN_CONTAINS_LENGTH:
            hits = []
            matched_by = ""
            for name_index, label in ((self._by_name, "usr_name"), (self._by_code, "usr_code")):
                for name_key, records in name_index.items():
                    if len(name_key) < MIN_CONTAINS_LENGTH:
                        continue
                    if name_key in key or key in name_key:
                        hits.extend(records)
                        matched_by = matched_by or label
            if hits:
                return self._finish(hits, f"{matched_by}_contains")
        return CustomerLookupResult("not_found")

    def _finish(
        self, records: list[CustomerRecord], matched_by: str
    ) -> CustomerLookupResult:
        unique: dict[str, CustomerCandidate] = {}
        for record in records:
            unique.setdefault(record.id, self._to_candidate(record))
        candidates = tuple(unique.values())
        if len(candidates) == 1:
            return CustomerLookupResult("unique", candidates, matched_by)
        return CustomerLookupResult("ambiguous", candidates, matched_by)

    @staticmethod
    def _to_candidate(record: CustomerRecord) -> CustomerCandidate:
        return CustomerCandidate(
            id=record.id,
            usr_name=record.usr_name,
            usr_code=record.usr_code,
            available=is_customer_available(record),
        )
