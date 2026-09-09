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
    """支持字段抽取值的原文位置（仅保留逐字引用片段）。"""

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
    status: FieldStatus = "needs_review"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)


class ExtractionEnvelope(BaseModel):
    """DeepSeek 抽取结果的结构化响应约束。"""

    candidates: list[FieldCandidate] = Field(default_factory=list)


class ExtractedField(BaseModel):
    """结构化抽取中单个字段的提取值；用于 with_structured_output 的 schema 约束。

    与 FieldCandidate 的区别：evidence 只保留原文片段字符串（不强制 bounding box），
    value 用宽松联合类型，便于模型直接填充；转换回 FieldCandidate 时再补全 Evidence。
    """

    value: str | int | float | dict[str, Any] | None = None
    status: FieldStatus = "needs_review"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class PoOrderExtraction(BaseModel):
    """DeepSeek 结构化抽取结果：12 个字段全部必填。

    字段缺失由 schema 默认填 None / needs_review，从结构上杜绝“模型漏输出字段”。
    description 随 JSON schema 下发，帮助模型理解每个字段含义。
    """

    sfg: ExtractedField = Field(description="始发港")
    mdg: ExtractedField = Field(description="目的港")
    ybpiece: ExtractedField = Field(description="件数")
    ybweight: ExtractedField = Field(description="重量")
    ybvolume: ExtractedField = Field(description="体积")
    inwageallinprice: ExtractedField = Field(description="运费（应收运费价格；COLLECT/PREPAID 条款视为无效）")
    hbrq: ExtractedField = Field(description="预计航班日期/船期")
    fid: ExtractedField = Field(description="委托客户（托书无此字段，由调用方 context 提供）")
    shipper: ExtractedField = Field(description="发货人（name/address/phone/email）")
    consignee: ExtractedField = Field(description="收货人（name/address/phone/email）")
    chinesepm: ExtractedField = Field(description="中文品名")
    englishpm: ExtractedField = Field(description="英文品名")


class FieldMetadata(BaseModel):
    """一个输出字段的最终元数据。"""

    value: Any = None
    status: FieldStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)


class ValidationResult(BaseModel):
    """完整订单 JSON 的校验结果。"""

    is_valid: bool


class AnalysisResult(BaseModel):
    """返回给调用方的完整分析结果。"""

    task_id: str
    schema_version: str
    result: dict[str, Any]
    overall_status: Literal["ready", "needs_review", "failed"]
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_meta: dict[str, FieldMetadata] = Field(default_factory=dict)
    review_fields: list[str] = Field(default_factory=list)
    validation: ValidationResult
