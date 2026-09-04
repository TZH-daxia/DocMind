from typing import Any, Literal

from app.schemas.analysis import (
    AnalysisResult,
    Evidence,
    FieldMetadata,
    ValidationResult,
)
from app.schemas.po_order import CONTEXT_ONLY_KEYS, PO_ORDER_KEYS
from app.service.requirements.po_order_requirements import PoOrderRequirementService


class ResultBuilder:
    """将字段元数据和订单上下文构建为最终 JSON。"""

    def build(
        self,
        task_id: str,
        schema_version: str,
        field_meta: dict[str, FieldMetadata],
        context: dict[str, Any],
        overall_confidence: float,
        requirement_service: PoOrderRequirementService | None = None,
    ) -> AnalysisResult:
        """构建结果 JSON 并补充缺失字段和校验错误。"""

        requirement_service = requirement_service or PoOrderRequirementService()
        required_keys = set(requirement_service.required_field_keys(context))
        result: dict[str, Any] = {key: None for key in PO_ORDER_KEYS}
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        self._add_client_from_context(result, field_meta, context, requirement_service)

        for field_key, metadata in field_meta.items():
            if field_key not in result or field_key in CONTEXT_ONLY_KEYS:
                continue
            result[field_key] = self._result_value(field_key, metadata.value)
            self._append_field_issues(field_key, metadata, required_keys, errors, warnings)

        for field_key in required_keys:
            if field_meta.get(field_key) is not None:
                continue
            field_meta[field_key] = FieldMetadata(
                value=None,
                status="missing",
                confidence=0.0,
                evidence=[],
                extraction_method="none",
                validation_errors=[],
            )

        errors = self._unique(errors)
        validation = ValidationResult(is_valid=not errors, errors=errors, warnings=warnings)
        overall_status: Literal["ready", "needs_review", "failed"] = (
            "ready" if validation.is_valid and not warnings else "needs_review"
        )
        return AnalysisResult(
            task_id=task_id,
            schema_version=schema_version,
            result=result,
            overall_status=overall_status,
            overall_confidence=overall_confidence,
            field_meta=field_meta,
            validation=validation,
        )

    @staticmethod
    def _add_client_from_context(
        result: dict[str, Any],
        field_meta: dict[str, FieldMetadata],
        context: dict[str, Any],
        requirement_service: PoOrderRequirementService,
    ) -> None:
        """委托客户只在托书中不存在，需由调用方 context 提供。"""

        value = requirement_service.context_client_value(context)
        if value in (None, ""):
            return
        result["fid"] = value
        field_meta["fid"] = FieldMetadata(
            value=value,
            raw_value=str(value),
            status="normalized",
            confidence=1.0,
            evidence=[
                Evidence(
                    document_id="context",
                    quote=f"fid={value}",
                )
            ],
            extraction_method="context",
        )

    def _append_field_issues(
        self,
        field_key: str,
        metadata: FieldMetadata,
        required_keys: set[str],
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> None:
        """将字段级错误写入 errors（必填）或 warnings（选填）。"""

        if metadata.validation_errors:
            target = errors if field_key in required_keys else warnings
            target.append({"field_key": field_key, "messages": metadata.validation_errors})
        if metadata.status in {"conflict", "invalid"}:
            target = errors if field_key in required_keys else warnings
            target.append({"field_key": field_key, "messages": [f"字段状态为 {metadata.status}"]})

    @staticmethod
    def _result_value(field_key: str, value: Any) -> Any:
        """按目标字段类型写入业务结果。"""

        if field_key == "shipper" or field_key == "consignee":
            return value if isinstance(value, dict) else None
        return value

    @staticmethod
    def _unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """去除重复的错误信息。"""

        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in items:
            key = repr(item)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique
