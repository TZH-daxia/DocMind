"""港口三字码归一化相关的数据结构。"""

from typing import Literal

from pydantic import BaseModel, Field


class PortRecord(BaseModel):
    """港口主数据单条记录（来源 poOrder PublicWebApi /api/PubAirPortArea）。"""

    three_code: str
    country_code: str = ""
    english_name: str = ""


class PortReferenceCache(BaseModel):
    """港口主数据本地缓存文件结构（reference_cache/hbinfo.json）。"""

    fetched_at: str
    records: list[PortRecord] = Field(default_factory=list)


class PortCandidate(BaseModel):
    """主数据里的一个候选（多义时给模型选择、给人工审核）。"""

    three_code: str
    english_name: str = ""
    country_code: str = ""


class PortSuggestionInput(BaseModel):
    """送给模型的待识别端口：原文 + 本地已匹配到的候选（可能为空）。"""

    field_key: str
    raw_value: str
    candidates: list[PortCandidate] = Field(default_factory=list)


class PortCodeSuggestion(BaseModel):
    """模型给出的可校验中间量。

    给了候选时用 `chosen_code`（必须来自候选）；没给候选时用 `english_name`
    （规范英文港口名，由系统再查主数据映射成三字码）。模型不再直接产出三字码，
    避免"码存在但与原文无关"的幻觉被静默采纳。
    """

    field_key: str
    chosen_code: str = ""
    english_name: str = ""
    reason: str = ""


class PortCodeSuggestionResult(BaseModel):
    """模型输出集合。"""

    suggestions: list[PortCodeSuggestion] = Field(default_factory=list)


class PortNormalizationOutcome(BaseModel):
    """单个港口字段的归一化结果。

    status 语义：
    - `normalized`：已确定三字码，可替换字段值；
    - `ambiguous`：本地主数据有多个候选且未能消歧，`candidates` 供人工选择；
    - `not_a_port`：判定不是港口/机场（国家、地区、费用表头词等）；
    - `failed`：尝试过但无法采信（模型超时、输出来源不明、主数据缺失）。
    """

    field_key: str
    status: Literal["normalized", "ambiguous", "not_a_port", "failed"]
    raw_value: str
    three_code: str | None = None
    english_name: str | None = None
    assembled: str | None = None
    candidates: list[PortCandidate] = Field(default_factory=list)
    matched_by: str | None = None
    reason: str | None = None


class PortOutcomeCache(BaseModel):
    """归一化结果缓存（reference_cache/port_outcomes.json）。

    `version` 取主数据缓存的 fetched_at：主数据更新后旧结论整体失效。
    """

    version: str
    entries: dict[str, PortNormalizationOutcome] = Field(default_factory=dict)
