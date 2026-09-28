"""poOrder 的用户设置模板（`api/UserTemplet`，挂在 PublicWebApi 下）。

接口按登录名返回该用户的**全部模板**（实测 377 条 / 660KB），其中 `type == 110`
那几条就是「用户设置」，`jsondata` 里放着订单新增页的默认值：

    {"mawbAddArea": "上海",
     "mawbAddSystem": {"opersystem": "出口", "opersystemdom": "空运"},
     "mawbAddService": "", ...}

对应关系（与 poOrder `newOrderAdd.vue` 的 `otherInitData()` 同口径）：
`mawbAddArea` → 唯凯站点，`mawbAddSystem.opersystem` → 运输种类，
`mawbAddSystem.opersystemdom` → 服务方式。

poOrder 前端对所有请求统一带 `Authorization: sessionStorage.ticket`
（`src/common/http.js`），这里同样支持透传票据——由调用方提供，DocMind 不签发、不保存。
"""

from __future__ import annotations

from typing import Any

import httpx


class UserTempletCollector:
    """拉取某个登录名的用户设置模板。"""

    def __init__(self, api_base: str, timeout_seconds: float = 20.0) -> None:
        self.api_base = api_base if api_base.endswith("/") else f"{api_base}/"
        # 响应体很大（该用户的全部模板，实测 ~660KB），超时给宽一点
        self.timeout_seconds = timeout_seconds

    async def fetch_records(
        self,
        logname: str,
        project: str = "bomanagement",
        ticket: str = "",
    ) -> list[dict[str, Any]]:
        """返回该登录名在指定项目下的模板原始列表；形状不对时返回空列表。"""

        url = f"{self.api_base}api/UserTemplet"
        headers = {"Authorization": ticket} if ticket else {}
        params = {"logname": logname, "project": project}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]
