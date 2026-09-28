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
    service_codes: list[str] | None = Field(
        default=None,
        description=(
            "服务项目面板勾选的服务代码（按面板顺序）；"
            "不传 = 按默认的唯凯配舱（OA0010），传空数组 = 一项服务都不做"
        ),
    )
    ticket: str = Field(
        default="",
        description=(
            "poOrder 票据（**兼容旧调用方保留，优先级最低**）。"
            "正常通道是请求头 `Authorization`（备用 `X-PoOrder-Ticket`）；"
            "`?ticket=` 仅在 DOCMIND_ALLOW_URL_TICKET 打开时作为开发期兜底。"
            "DocMind 不签发、不校验、不落盘，只在本次请求内透传给 poOrder"
        ),
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
