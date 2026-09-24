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
    # 信用等级：A / B / B- / C，空表示未评级。
    # poOrder 选完委托客户后显示的「A类」「C类」就来自这里
    #（newOrderAdd.vue 的 loadWtkdData：先从 wtkhUseful 取 creditlevel）
    creditlevel: str = ""
    # 增量拉取用的时间戳
    timestamp: int = 0


class CustomerCandidate(BaseModel):
    """命中的客户候选（多义时给人工选择）。"""

    id: str = Field(description="客户 ID")
    usr_name: str = Field(default="", description="客户中文名称")
    usr_code: str = Field(default="", description="客户编码")
    available: bool = Field(default=True, description="是否可用于新增订单")


class CustomerReferenceCache(BaseModel):
    """委托客户主数据本地缓存（reference_cache/customers.json）。"""

    fetched_at: str
    records: list[CustomerRecord] = Field(default_factory=list)


class CustomerCreditHint(BaseModel):
    """选完委托客户后、显示在其输入框下方的信用等级与信控提示。

    文案口径与 poOrder 一致（`newOrderAdd.vue` 的 `loadWtkdData`）：
    - `resultstatus == 0`（通过）：只显示等级，如「A类」
    - `resultstatus != 0`（受限）：等级 + 接口给的提示，
      如「C类,该客户是C类客户,需付款买单才能继续操作」
    """

    enabled: bool = Field(default=False, description="信控接口是否可用")
    level: str = Field(
        default="", description="信用等级原始值：A / B / B- / C；空表示未评级"
    )
    message: str = Field(default="", description="信控提示原文；通过时为空")
    hint: str = Field(
        default="", description="合成后的展示文案，直接显示在委托客户输入框下方"
    )


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
