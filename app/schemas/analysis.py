from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisContext(BaseModel):
    """由调用方提供的订单页面上下文。"""

    model_config = ConfigDict(extra="allow")

    opersystem: str | None = None
    opersystemdom: str | None = None
    area: str | None = None
    czlx: str | None = None
    orderdom: str | None = None
    orderdom_out: str | None = Field(default=None, alias="orderdomOut")
    ordertype: int | str | None = None
    fcllcllx: int | str | None = None
    isimperfect: int | str | None = None
    fid: int | str | None = None
    gid: int | str | None = None
    service_codes: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """支持字段抽取值的原文位置。"""

    document_id: str
    page_no: int | None = None
    block_id: str | None = None
    quote: str | None = None


FieldStatus = Literal[
    "confirmed",
    "normalized",
    "conflict",
    "missing",
    "invalid",
    "needs_review",
    "not_applicable",
]


class FieldCandidate(BaseModel):
    """由规则或模型抽取出的一个字段候选值。"""

    field_key: str
    value: Any = None
    raw_value: str | None = None
    unit: str | None = None
    status: FieldStatus = "needs_review"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    extraction_method: Literal["regex", "table", "ocr", "llm", "context", "manual"] = "llm"
    validation_errors: list[str] = Field(default_factory=list)


class ExtractionEnvelope(BaseModel):
    """DeepSeek 抽取结果的结构化响应约束。"""

    candidates: list[FieldCandidate] = Field(default_factory=list)


class FieldMetadata(BaseModel):
    """一个输出字段的最终元数据。"""

    value: Any = None
    raw_value: str | None = None
    unit: str | None = None
    status: FieldStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    extraction_method: str
    validation_errors: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    """完整订单 JSON 的校验结果。"""

    is_valid: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """返回给调用方的完整分析结果。"""

    task_id: str
    schema_version: str
    result: dict[str, Any]
    overall_status: Literal["ready", "needs_review", "failed"]
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_meta: dict[str, FieldMetadata] = Field(default_factory=dict)
    validation: ValidationResult
