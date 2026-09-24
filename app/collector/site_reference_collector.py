"""唯凯站点字典外部采集。"""

import logging
from typing import Any

import httpx

from app.schemas.site import SITE_GROUP_ID, SITE_NAME_SEPARATOR, SiteRecord

logger = logging.getLogger(__name__)


class SiteReferenceCollector:
    """从 poOrder PublicWebApi 拉取唯凯站点字典（一次性全量）。"""

    def __init__(self, api_base: str, timeout_seconds: float = 30.0) -> None:
        self.api_base = api_base if api_base.endswith("/") else f"{api_base}/"
        self.timeout_seconds = timeout_seconds

    async def fetch_records(self) -> list[SiteRecord]:
        """拉取站点字典并归一化为 SiteRecord 列表。"""

        url = f"{self.api_base}api/PubTypeCode"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params={"groupid": SITE_GROUP_ID})
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise TypeError("PubTypeCode 返回结构不是数组")
        records = [
            self._parse_record(item) for item in payload if isinstance(item, dict)
        ]
        valid = [record for record in records if record.name]
        logger.info(
            "唯凯站点字典拉取完成：原始 %s 条，有效 %s 条", len(records), len(valid)
        )
        return valid

    @staticmethod
    def _parse_record(item: dict[str, Any]) -> SiteRecord:
        """兼容内网 PascalCase 与外网驼峰两种字段命名。"""

        full_name = str(item.get("typename") or item.get("TypeName") or "").strip()
        group = str(item.get("ready04") or item.get("Ready04") or "").strip()
        if SITE_NAME_SEPARATOR in full_name:
            name, _, code = full_name.partition(SITE_NAME_SEPARATOR)
        else:
            name, code = full_name, ""
        return SiteRecord(
            name=name.strip(),
            code=code.strip(),
            full_name=full_name,
            group=group,
        )
