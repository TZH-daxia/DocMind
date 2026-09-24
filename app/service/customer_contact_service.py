"""本票客户客服联系人：取候选 + 挑出本票的默认联系人。

口径与 poOrder 一致（`src/components/templates/customerRel.vue`）：

- 只保留 `comxz == '1'`（有效）的联系人——poOrder 里那句 `pageArea.includes(i.area)`
  是把自己刚赋的值再比一次，等于没有过滤，所以站点维度实际只在"默认联系人"里用；
- **默认联系人**：该项 `defaultlxrjson`（形如 `[{area, system}]`）里存在
  `area == 当前站点` 且（`system == 当前系统` 或 `system == -1` 通配）；
- 没有任何默认标记时取第一条（poOrder 的列表里也是点第一条带出）。

查询失败或未配置接口时返回 `enabled=False` + 空列表，弹窗退化为空，不阻断填写。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.collector.customer_contact_collector import CustomerContactCollector
from app.config import Settings
from app.service.order_submit_service import management_api_base, text_of

logger = logging.getLogger(__name__)

# 本票客服联系人的类型值：与报文里固定的 post/lxrtitle/department 一致
CONTACT_TYPE = "客服"


def is_default_contact(record: dict[str, Any], area: str, system: str) -> bool:
    """该项是否是「当前站点 + 当前系统」下的默认联系人。"""

    raw = text_of(record.get("defaultlxrjson"))
    if not raw:
        return False
    try:
        entries = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if text_of(entry.get("area")) != area:
            continue
        target = text_of(entry.get("system"))
        if target == "-1" or (system and target == system):
            return True
    return False


def to_item(record: dict[str, Any], area: str, system: str) -> dict[str, Any]:
    """联系人 → 前端可用结构（只有这几项会进报文或用于展示）。"""

    return {
        "name": text_of(record.get("name")),
        "mobile": text_of(record.get("mobile")),
        "phone": text_of(record.get("phone")),
        "email": text_of(record.get("email")),
        "is_default": is_default_contact(record, area, system),
    }


class CustomerContactService:
    """本票客户客服联系人查询。"""

    def __init__(self, settings: Settings) -> None:
        api_base = management_api_base(settings)
        self.collector = CustomerContactCollector(api_base) if api_base else None

    @property
    def enabled(self) -> bool:
        """未配置 BoManagementWebApi 根地址时整体停用。"""

        return self.collector is not None

    async def list_contacts(
        self, fid: str, area: str = "", system: str = ""
    ) -> dict[str, Any]:
        """列出某委托客户的客服联系人候选（默认联系人排在结果里用 is_default 标注）。"""

        customer_id = text_of(fid)
        if not customer_id or self.collector is None:
            return {"enabled": self.enabled, "items": []}
        try:
            raw = await self.collector.fetch_contacts(customer_id, CONTACT_TYPE)
        except Exception:
            logger.exception("客服联系人查询失败：客户 %s", customer_id)
            return {"enabled": True, "items": []}
        items = [
            to_item(record, area, system)
            for record in raw
            if text_of(record.get("comxz")) == "1"
        ]
        return {"enabled": True, "items": items}
