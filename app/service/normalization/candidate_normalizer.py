import re

from app.schemas.analysis import FieldCandidate


class CandidateNormalizer:
    """统一候选值的格式、单位和大小写。"""

    NUMERIC_FIELDS: frozenset[str] = frozenset({
        "ybpiece",
        "ybweight",
        "ybvolume",
        "inwageallinprice",
    })
    TEXT_FIELDS: frozenset[str] = frozenset({
        "sfg",
        "mdg",
        "hbrq",
        "chinesepm",
        "englishpm",
    })

    def normalize(self, candidates: list[FieldCandidate]) -> list[FieldCandidate]:
        """标准化候选值，不改变原始证据。"""

        normalized: list[FieldCandidate] = []
        for candidate in candidates:
            updates: dict[str, object] = {}
            if candidate.field_key in self.NUMERIC_FIELDS:
                updates.update(self._normalize_numeric(candidate))
            elif candidate.field_key in self.TEXT_FIELDS and isinstance(candidate.value, str):
                value = re.sub(r"\s+", " ", candidate.value).strip()
                if candidate.field_key in {"englishpm", "chinesepm"}:
                    value = self._clean_code_suffix(value)
                updates["value"] = value
            normalized.append(candidate.model_copy(update=updates))
        return normalized

    def _normalize_numeric(self, candidate: FieldCandidate) -> dict[str, object]:
        """标准化数字和对应单位。"""

        value = candidate.value
        raw_value = candidate.raw_value or ""
        if isinstance(value, str):
            match = re.search(r"[-+]?[0-9][0-9,]*(?:\.[0-9]+)?", value)
            if match:
                value = match.group(0)
        if isinstance(value, str):
            try:
                value = float(value.replace(",", ""))
            except ValueError:
                return {"value": value}
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        unit = candidate.unit or self._find_unit(raw_value, candidate.field_key)
        return {"value": value, "unit": unit}

    def _clean_code_suffix(self, value: str) -> str:
        """去掉模型候选品名末尾误带的海关代码片段。"""

        return re.split(
            r"(?:HS\s*CODE|海关代码)",
            value,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" ：:;,，")

    @staticmethod
    def _find_unit(raw_value: str, field_key: str) -> str | None:
        """从原文中识别字段单位。"""

        upper_value = raw_value.upper()
        if field_key == "ybpiece":
            unit_map = {
                "PALLET": "PLT",
                "PALLETS": "PLT",
                "PLT": "PLT",
                "CARTON": "CTN",
                "CARTONS": "CTN",
                "CTN": "CTN",
                "PKG": "PKG",
                "PKGS": "PKG",
                "PCS": "PCS",
            }
            for source_unit, target_unit in unit_map.items():
                if source_unit in upper_value:
                    return target_unit
        if field_key == "ybweight" and any(unit in upper_value for unit in ("KG", "KGS", "公斤")):
            return "KG"
        if field_key == "ybvolume" and any(unit in upper_value for unit in ("CBM", "CMB", "立方米")):
            return "CBM"
        return None
