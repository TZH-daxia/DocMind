"""委托客户校验相关的数据结构。

主数据来源与 poOrder「新增订单」一致：poOrder PublicWebApi /api/PubFCustom，
即同一个 base 下的委托客户全量列表；可用性判定也沿用它的口径。
"""

from typing import Literal

from pydantic import BaseModel, Field


class CustomerRecord(BaseModel):
    """委托客户主数据单条记录（只保留校验需要的字段）。"""

    id: str
    usr_code: str = ""
    usr_name: str = ""
    ename: str = ""
    # 1 = 启用；poOrder 新增订单要求 usr_status == 1
    usr_status: int = 0
    # 2 = 不参与新业务；poOrder 新增订单会排除 customxz == 2
    customxz: int = 0
    # 增量拉取用的时间戳
    timestamp: int = 0


class CustomerCandidate(BaseModel):
    """命中的客户候选（多义时给人工选择）。"""

    id: str
    usr_name: str = ""
    usr_code: str = ""
    available: bool = True


class CustomerReferenceCache(BaseModel):
    """委托客户主数据本地缓存（reference_cache/customers.json）。"""

    fetched_at: str
    records: list[CustomerRecord] = Field(default_factory=list)


class CustomerValidationOutcome(BaseModel):
    """单个委托客户字段的校验结果。

    status 语义：
    - `ok`：唯一命中且可用；
    - `ambiguous`：命中多个客户，需要人工选择；
    - `not_found`：主数据里没有这个客户；
    - `unavailable`：客户存在但已停用/不参与新业务；
    - `skipped`：未配置客户主数据接口，本次未校验。
    """

    status: Literal["ok", "ambiguous", "not_found", "unavailable", "skipped"]
    raw_value: str
    customer: CustomerCandidate | None = None
    candidates: list[CustomerCandidate] = Field(default_factory=list)
    matched_by: str | None = None
    reason: str | None = None
