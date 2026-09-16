from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.port import PortCandidate


class AnalysisContext(BaseModel):
    """由调用方（订单页面）提供的上下文。

    这些字段均为透传性质：托书本身无法提供的信息（如委托客户 `fid`）通过它们
    补充给抽取流程；未使用的字段可省略，额外字段也允许传入（extra=allow）。
    """

    model_config = ConfigDict(extra="allow")

    opersystem: str | None = Field(default=None, description="操作系统标识")
    opersystemdom: str | None = Field(default=None, description="操作系统对应域名")
    area: str | None = Field(default=None, description="业务区域")
    czlx: str | None = Field(default=None, description="操作类型")
    orderdom: str | None = Field(default=None, description="订单境内/境外属性")
    orderdom_out: str | None = Field(
        default=None, alias="orderdomOut", description="订单境外属性（orderdomOut）"
    )
    ordertype: int | str | None = Field(default=None, description="订单类型")
    fcllcllx: int | str | None = Field(
        default=None, description="整箱/拼箱业务类型（FCL/LCL）"
    )
    isimperfect: int | str | None = Field(
        default=None, description="是否为不完整订单：1 表示是"
    )
    fid: int | str | None = Field(
        default=None,
        description="委托客户 ID；托书无此字段，必须由调用方提供",
    )
    gid: int | str | None = Field(default=None, description="业务员（操作员）ID")
    service_codes: list[str] = Field(default_factory=list, description="服务代码列表")


class Evidence(BaseModel):
    """支持字段抽取值的原文位置（仅保留逐字引用片段）。"""

    quote: str | None = Field(default=None, description="托书原文中的逐字引用片段")


FieldStatus = Literal[
    "confirmed",
    "normalized",
    "conflict",
    "missing",
    "invalid",
    "needs_review",
    "not_applicable",
]
"""字段状态：已确认 / 已归一化 / 冲突 / 缺失 / 非法 / 待人工复核 / 不适用。"""


class FieldCandidate(BaseModel):
    """由规则或模型抽取出的一个字段候选值。"""

    field_key: str = Field(description="字段标识，如 sfg、mdg、ybweight")
    value: Any = Field(default=None, description="候选值，未抽出时为 null")
    status: FieldStatus = Field(default="needs_review", description="该候选的状态")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="置信度，取值 0~1"
    )
    evidence: list[Evidence] = Field(
        default_factory=list, description="支持该取值的原文引用列表"
    )


class ExtractionEnvelope(BaseModel):
    """DeepSeek 抽取结果的结构化响应约束。"""

    candidates: list[FieldCandidate] = Field(
        default_factory=list, description="本次抽取产出的全部字段候选"
    )


class ExtractedField(BaseModel):
    """结构化抽取中单个字段的提取值；用于 with_structured_output 的 schema 约束。

    与 FieldCandidate 的区别：evidence 只保留原文片段字符串（不强制 bounding box），
    value 用宽松联合类型，便于模型直接填充；转换回 FieldCandidate 时再补全 Evidence。
    """

    value: str | int | float | dict[str, Any] | None = Field(
        default=None, description="字段提取值，未识别时为 null"
    )
    status: FieldStatus = Field(default="needs_review", description="该字段的状态")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="置信度，取值 0~1"
    )
    evidence: list[str] = Field(
        default_factory=list, description="支持该取值的原文片段列表"
    )


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
    inwageallinprice: ExtractedField = Field(
        description="运费（应收运费价格；COLLECT/PREPAID 条款视为无效）"
    )
    hbrq: ExtractedField = Field(description="预计航班日期/船期")
    fid: ExtractedField = Field(
        description="委托客户（托书无此字段，由调用方 context 提供）"
    )
    shipper: ExtractedField = Field(description="发货人（name/address/phone/email）")
    consignee: ExtractedField = Field(description="收货人（name/address/phone/email）")
    chinesepm: ExtractedField = Field(description="中文品名")
    englishpm: ExtractedField = Field(description="英文品名")


class EvidenceLocation(BaseModel):
    """字段值在源文档中的位置，供前端在原件预览上叠加高亮框。

    target 标明该位置对应表单的哪一行：标量字段为字段 key，参与人字段为
    `key.subkey`（如 `shipper.address`）；bbox 为归一化坐标 [x, y, w, h]（0~1，
    相对页面宽高），因此与页面图片的渲染分辨率无关。
    """

    target: str = Field(
        description="对应表单位置：标量字段为字段 key，参与人字段为 key.subkey"
    )
    page: int = Field(ge=1, description="所在页码，从 1 开始")
    bbox: list[float] = Field(
        min_length=4,
        max_length=4,
        description="归一化坐标 [x, y, w, h]，取值范围 0~1，与图片分辨率无关",
    )


class FieldMetadata(BaseModel):
    """一个输出字段的最终元数据。"""

    value: Any = Field(default=None, description="最终字段值，未取到时为 null")
    status: FieldStatus = Field(description="字段最终状态")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="置信度，取值 0~1"
    )
    evidence: list[Evidence] = Field(
        default_factory=list, description="支持该取值的原文引用列表"
    )
    # 定位不到时为空列表，前端按"未定位"展示，不画框
    locations: list[EvidenceLocation] = Field(
        default_factory=list, description="字段在原件中的位置，空列表表示未定位"
    )
    # 归一化前的原始抽取值：港口字段转三字码失败时 value 置空，原文留在这里，
    # 供前端提示、坐标定位与人工核对使用
    raw_value: Any = Field(
        default=None, description="归一化前的原始抽取值，供人工核对使用"
    )
    # 港口归一化未定论时的候选（来自主数据）：前端在空字段下方展示供人工选择
    candidates: list[PortCandidate] = Field(
        default_factory=list, description="港口归一化未定论时的主数据候选"
    )


class ValidationResult(BaseModel):
    """完整订单 JSON 的校验结果。"""

    is_valid: bool = Field(description="是否通过完整性/合法性校验")


class AnalysisResult(BaseModel):
    """返回给调用方的完整分析结果。"""

    task_id: str = Field(description="任务 ID")
    schema_version: str = Field(description="结果对应的字段结构版本")
    result: dict[str, Any] = Field(description="结构化订单 JSON，键为字段标识")
    overall_status: Literal["ready", "needs_review", "failed"] = Field(
        description="整体状态：可直接提交 / 需人工复核 / 分析失败"
    )
    overall_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="整体置信度，取值 0~1"
    )
    field_meta: dict[str, FieldMetadata] = Field(
        default_factory=dict, description="字段级元数据，键为字段标识"
    )
    review_fields: list[str] = Field(
        default_factory=list, description="需要人工复核的字段标识列表"
    )
    validation: ValidationResult = Field(description="订单 JSON 的校验结果")
