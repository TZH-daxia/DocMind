"""提交订单（poOrder `api/ExHpoAxpline`）。

该接口挂在 poOrder 的 **BoManagementWebApi** 下（不是 PublicWebApi：
见 poOrder `src/store/index.js:82` 的 `webApi` 定义），与港口/客户/站点字典
同主机、不同应用名。

poOrder 前端对这个接口一律带 `Authorization: sessionStorage.ticket`
（见 `src/common/http.js`），因此这里支持透传票据——票据由调用方（官网/客服）
提供，DocMind 自己不签发、不保存。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class OrderSubmitCollector:
    """调用 poOrder 的订单提交接口。"""

    def __init__(self, api_base: str, timeout_seconds: float = 60.0) -> None:
        self.api_base = api_base if api_base.endswith("/") else f"{api_base}/"
        # 提交是写操作，poOrder 侧建单要跑信控等一整套校验，实测一次要十几秒：
        # 给足 60 秒，避免超时被误判为失败（重试会重复建单）
        self.timeout_seconds = timeout_seconds

    async def submit_order(self, payload: dict[str, Any], ticket: str = "") -> Any:
        """提交订单报文，返回接口原始响应内容。"""

        url = f"{self.api_base}api/ExHpoAxpline"
        headers = {"Content-Type": "application/json"}
        if ticket:
            headers["Authorization"] = ticket
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
