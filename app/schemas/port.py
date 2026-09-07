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


class PortFieldInput(BaseModel):
    """待归一化的港口字段原文。"""

    field_key: str
    raw_value: str


class PortCodeProposal(BaseModel):
    """模型基于自身知识给出的三字码候选。"""

    field_key: str
    three_code: str = ""
    reason: str = ""


class PortCodeProposalResult(BaseModel):
    """三字码候选生成的结构化输出。"""

    proposals: list[PortCodeProposal] = Field(default_factory=list)


class PortNormalizationOutcome(BaseModel):
    """单个港口字段的三字码归一化结果。"""

    field_key: str
    status: Literal["normalized", "skipped", "failed"]
    raw_value: str
    three_code: str | None = None
    english_name: str | None = None
    assembled: str | None = None
    reason: str | None = None
