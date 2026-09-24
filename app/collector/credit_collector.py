"""委托客户信控查询（poOrder PublicWebApi /api/PubCredit）。

选完委托客户后调用，返回信控状态与提示文案。poOrder 在
`newOrderAdd.vue` 的 `loadWtkdData` 里用的就是这个接口：

    GET api/PubCredit?fid={客户id}&area={站点}&system={业务系统}
    → resultstatus == 0      通过
    → resultstatus != 0      有信控限制，`resultmessage` 就是给操作员看的提示
                             （如「该客户是C类客户,需付款买单才能继续操作」）

这是一次针对单个客户的实时查询，不是字典，因此不做本地缓存。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CreditReferenceCollector:
    """查询单个委托客户的信控情况。"""

    def __init__(self, api_base: str, timeout_seconds: float = 15.0) -> None:
        self.api_base = api_base if api_base.endswith("/") else f"{api_base}/"
        self.timeout_seconds = timeout_seconds

    async def fetch_credit(
        self, fid: str, area: str = "", system: str = ""
    ) -> dict[str, Any]:
        """返回接口原始结果；调用方只依赖 resultstatus/resultmessage/resultdic。"""

        url = f"{self.api_base}api/PubCredit"
        params = {"fid": fid, "area": area, "system": system}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("PubCredit 返回结构不是对象")
        return payload
