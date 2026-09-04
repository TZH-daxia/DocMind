import re
from typing import Any, ClassVar

from pydantic import ValidationError

from app.schemas.analysis import FieldCandidate
from app.schemas.po_order import PartyInfo


class CandidateValidator:
    """校验候选值格式与业务语义。"""

    ADDRESS_MARKERS = (
        "STREET",
        "ROAD",
        "BUILDING",
        "STRASSE",
        "GERMANY",
        "CHINA",
        "LTD",
        "GMBH",
        "CO.",
        "COMPANY",
        "地址",
    )
    NUMERIC_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "ybpiece",
        "ybweight",
        "ybvolume",
        "inwageallinprice",
    })
    PARTY_FIELDS: ClassVar[frozenset[str]] = frozenset({"shipper", "consignee"})

    def validate(self, candidates: list[FieldCandidate]) -> list[FieldCandidate]:
        """校验候选并写入字段级错误。"""

        validated: list[FieldCandidate] = []
        for candidate in candidates:
            errors = list(candidate.validation_errors)
            status = candidate.status
            value = candidate.value
            if value is not None and not candidate.evidence:
                errors.append("非上下文字段缺少原文证据")
                status = "needs_review"
                value = None
            value_errors = self._validate_value(candidate.field_key, value)
            errors.extend(value_errors)
            if value_errors and status in {
                "normalized",
                "needs_review",
                "confirmed",
            }:
                status = "invalid"
            elif candidate.field_key in self.PARTY_FIELDS and isinstance(value, dict):
                value = PartyInfo.model_validate(value).model_dump()
            validated.append(
                candidate.model_copy(
                    update={
                        "value": value,
                        "status": status,
                        "validation_errors": self._unique(errors),
                    }
                )
            )
        return validated

    def _validate_value(self, field_key: str, value: Any) -> list[str]:
        """校验单个字段值。"""

        if value is None:
            return []
        if field_key in {"sfg", "mdg"}:
            return self._validate_port(value)
        if field_key in self.NUMERIC_FIELDS:
            return self._validate_numeric(field_key, value)
        if field_key == "hbrq" and re.search(r"[~～至到]", str(value)):
            return ["日期区间不能直接填入单日期字段"]
        if field_key in self.PARTY_FIELDS:
            return self._validate_party(field_key, value)
        if field_key in {"chinesepm", "englishpm"} and not isinstance(value, str):
            return [f"{field_key} 必须为字符串"]
        return []

    def _validate_port(self, value: object) -> list[str]:
        """港口候选只接受明确的港口名称/代码，拒绝发货人或收货人地址。"""

        text = str(value).strip()
        if not text:
            return ["港口值为空"]
        upper_text = text.upper()
        if len(text) > 40 or any(marker in upper_text for marker in self.ADDRESS_MARKERS):
            return ["港口候选疑似为发货人或收货人地址"]
        return []

    def _validate_numeric(self, field_key: str, value: object) -> list[str]:
        """数字字段必须是非负数值；运费为 COLLECT/PREPAID 等文本时视为无效。"""

        if field_key == "ybpiece":
            try:
                number = float(str(value))
            except (TypeError, ValueError):
                return ["包装件数不是数字"]
            if number <= 0 or not number.is_integer():
                return ["包装件数必须是正整数"]
            return []
        text = str(value).strip()
        if not text:
            return [f"{field_key} 数值为空"]
        if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
            return [f"{field_key} 不是数值，文本值（如 COLLECT/PREPAID）视为无效"]
        if float(text) < 0:
            return [f"{field_key} 不能为负数"]
        return []

    def _validate_party(self, field_key: str, value: object) -> list[str]:
        """发货人/收货人只能接受 PartyInfo 对象结构。"""

        if not isinstance(value, dict):
            return [f"{field_key} 必须是对象结构"]
        try:
            PartyInfo.model_validate(value)
        except ValidationError:
            return [f"{field_key} 对象结构不符合 PartyInfo 格式"]
        return []

    @staticmethod
    def _unique(messages: list[str]) -> list[str]:
        """去除重复错误信息。"""

        return list(dict.fromkeys(messages))
