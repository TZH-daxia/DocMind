import json
import re
from typing import Any

from app.schemas.analysis import FieldCandidate, FieldMetadata


class ConflictResolver:
    """对候选值去重并识别字段冲突。"""

    # 品名类字段的空格差异（如 PDF 字距导致的粘连）不视为值冲突。
    WHITESPACE_INSENSITIVE_FIELDS: frozenset[str] = frozenset({
        "englishpm",
        "chinesepm",
    })

    def resolve(self, candidates: list[FieldCandidate]) -> dict[str, FieldMetadata]:
        """生成每个字段的最终候选元数据。"""

        grouped: dict[str, list[FieldCandidate]] = {}
        invalid_grouped: dict[str, list[FieldCandidate]] = {}
        for candidate in candidates:
            if candidate.value is None or candidate.status in {"missing", "not_applicable"}:
                continue
            if candidate.status == "invalid":
                invalid_grouped.setdefault(candidate.field_key, []).append(candidate)
                continue
            grouped.setdefault(candidate.field_key, []).append(candidate)

        resolved: dict[str, FieldMetadata] = {}
        for field_key, options in grouped.items():
            unique = self._deduplicate(field_key, options)
            selected = max(unique, key=lambda item: item.confidence)
            evidence = self._merge_evidence(unique)
            if len(unique) > 1:
                resolved[field_key] = FieldMetadata(
                    value=None,
                    raw_value=selected.raw_value,
                    unit=selected.unit,
                    status="conflict",
                    confidence=selected.confidence,
                    evidence=evidence,
                    extraction_method=selected.extraction_method,
                    validation_errors=["同一字段存在多个不同候选值"],
                )
                continue
            selected = self._restore_conflicted_status(selected)
            usable = (
                selected.status in {"confirmed", "normalized"}
                and selected.confidence >= 0.85
                and bool(selected.evidence)
            )
            resolved[field_key] = FieldMetadata(
                value=selected.value if usable else None,
                raw_value=selected.raw_value,
                unit=selected.unit,
                status=selected.status if usable else "needs_review",
                confidence=selected.confidence,
                evidence=evidence,
                extraction_method=selected.extraction_method,
                validation_errors=selected.validation_errors,
            )
        for field_key, options in invalid_grouped.items():
            if field_key in resolved:
                existing = resolved[field_key]
                resolved[field_key] = existing.model_copy(
                    update={
                        "validation_errors": list(
                            dict.fromkeys(
                                existing.validation_errors
                                + [
                                    message
                                    for option in options
                                    for message in option.validation_errors
                                ]
                            )
                        )
                    }
                )
                continue
            selected = max(options, key=lambda item: item.confidence)
            resolved[field_key] = FieldMetadata(
                value=None,
                raw_value=selected.raw_value,
                unit=selected.unit,
                status="invalid",
                confidence=selected.confidence,
                evidence=self._merge_evidence(options),
                extraction_method=selected.extraction_method,
                validation_errors=selected.validation_errors or ["候选值未通过字段类型校验"],
            )
        return resolved

    def _deduplicate(
        self,
        field_key: str,
        candidates: list[FieldCandidate],
    ) -> list[FieldCandidate]:
        """合并规范化后相同的候选，保留完整证据。"""

        by_value: dict[str, FieldCandidate] = {}
        for candidate in candidates:
            key = self._canonical_value(field_key, candidate.value)
            existing = by_value.get(key)
            if existing is None:
                by_value[key] = candidate
            else:
                chosen = self._prefer_candidate(field_key, existing, candidate)
                by_value[key] = chosen.model_copy(
                    update={"evidence": self._merge_evidence([existing, candidate])}
                )
        return list(by_value.values())

    def _prefer_candidate(
        self,
        field_key: str,
        left: FieldCandidate,
        right: FieldCandidate,
    ) -> FieldCandidate:
        """同一规范化值的候选合并为一条；品名类字段优先保留分词更完整的表述。"""

        if field_key in self.WHITESPACE_INSENSITIVE_FIELDS and all(
            isinstance(item.value, str) for item in (left, right)
        ):
            chosen = max((left, right), key=lambda item: self._spacing_score(item.value))
        elif right.confidence > left.confidence:
            chosen = right
        else:
            chosen = left
        return chosen.model_copy(
            update={"confidence": max(left.confidence, right.confidence)}
        )

    @staticmethod
    def _spacing_score(value: Any) -> int:
        """衡量字符串分词完整度：粘连值（空格少）视为被 PDF 字距破坏的表述。"""

        return value.count(" ") if isinstance(value, str) else 0

    @staticmethod
    def _restore_conflicted_status(selected: FieldCandidate) -> FieldCandidate:
        """候选合并后值唯一时，模型自报的 conflict 不再成立，降回 normalized。"""

        if selected.status == "conflict" and not selected.validation_errors:
            return selected.model_copy(update={"status": "normalized"})
        return selected

    @staticmethod
    def _canonical_value(field_key: str, value: Any) -> str:
        """生成用于比较候选值的标准键。"""

        if field_key in {"ybpiece", "ybweight", "ybvolume", "jfweight"}:
            try:
                return str(float(value))
            except (TypeError, ValueError):
                pass
        if isinstance(value, str):
            if field_key in ConflictResolver.WHITESPACE_INSENSITIVE_FIELDS:
                return re.sub(r"\s+", "", value).upper()
            return value.strip().upper()
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _merge_evidence(candidates: list[FieldCandidate]):
        """合并候选证据并去除完全重复项。"""

        evidence = []
        seen: set[str] = set()
        for candidate in candidates:
            for item in candidate.evidence:
                key = item.model_dump_json()
                if key not in seen:
                    seen.add(key)
                    evidence.append(item)
        return evidence
