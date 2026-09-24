"""提交订单的请求与结果结构。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OrderSubmitRequest(BaseModel):
    """提交订单请求。

    表单与订单上下文都由前端提交：弹窗里的值可能被人工改过（也含本地草稿），
    后端只按报文契约取值，不回头查任务里的抽取结果。
    """

    model_config = ConfigDict(extra="ignore")

    form: dict[str, Any] = Field(default_factory=dict, description="弹窗核对后的表单值")
    order: dict[str, Any] = Field(
        default_factory=dict, description="工具条上的订单上下文（站点/服务方式/运输种类/订舱操作）"
    )
    czman: str = Field(
        default="", description="当前用户（登录名）：同时进报文的 czman 与 customerRelList[].addman"
    )
    ticket: str = Field(
        default="", description="poOrder 票据；提交接口需要鉴权时透传，DocMind 不保存"
    )


class OrderSubmitOutcome(BaseModel):
    """提交订单的结果。"""

    ok: bool = Field(default=False, description="是否创建成功")
    order_code: str = Field(default="", description="订单编号；成功后用于弹窗顶部展示")
    message: str = Field(default="", description="接口返回的提示文案（失败时为原因）")
    payload: dict[str, Any] | None = Field(
        default=None, description="实际发给 poOrder 的报文，便于核对（无则未组装）"
    )
    response: dict[str, Any] | None = Field(
        default=None,
        description="poOrder 的原始响应，便于核对（resultstatus / resultno / resultmessage）",
    )
