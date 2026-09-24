"""本票客户客服联系人（poOrder BoManagementWebApi `api/CustomerRel/GetCustomerRel`）。

取值口径见 poOrder `src/components/templates/customerRel.vue` 的 `getCustomerRelData()`：
GET `api/CustomerRel/GetCustomerRel`，参数是 `fid` + `post` / `lxrtitle` / `department`
三个同名值（本票客服联系人传「客服」）。返回**直接是数组**（没有 resultstatus 包装）。

该接口与提交订单同在 BoManagementWebApi 下，因此共用同一套基址解析。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CustomerContactCollector:
    """按委托客户查询客服联系人候选。"""

    def __init__(self, api_base: str, timeout_seconds: float = 15.0) -> None:
        self.api_base = api_base if api_base.endswith("/") else f"{api_base}/"
        self.timeout_seconds = timeout_seconds

    async def fetch_contacts(
        self, fid: str, contact_type: str = "客服"
    ) -> list[dict[str, Any]]:
        """返回联系人原始列表；调用方只依赖 name/mobile/phone/email/comxz/defaultlxrjson。"""

        url = f"{self.api_base}api/CustomerRel/GetCustomerRel"
        params = {
            "fid": fid,
            "post": contact_type,
            "lxrtitle": contact_type,
            "department": contact_type,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise TypeError("GetCustomerRel 返回结构不是数组")
        return [item for item in payload if isinstance(item, dict)]
