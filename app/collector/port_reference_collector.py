"""港口主数据外部采集。"""

import logging
from typing import Any

import httpx

from app.schemas.port import PortRecord

logger = logging.getLogger(__name__)


class PortReferenceCollector:
    """从 poOrder PublicWebApi 拉取机场/港口主数据（一次性全量）。"""

    def __init__(self, api_base: str, timeout_seconds: float = 30.0) -> None:
        self.api_base = api_base if api_base.endswith("/") else f"{api_base}/"
        self.timeout_seconds = timeout_seconds

    async def fetch_records(self) -> list[PortRecord]:
        """拉取全量港口主数据并归一化为 PortRecord 列表。"""

        url = f"{self.api_base}api/PubAirPortArea"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise TypeError("PubAirPortArea 返回结构不是数组")
        records = [self._parse_record(item) for item in payload if isinstance(item, dict)]
        valid = [record for record in records if record.three_code]
        logger.info("港口主数据拉取完成：原始 %s 条，有效 %s 条", len(records), len(valid))
        return valid

    @staticmethod
    def _parse_record(item: dict[str, Any]) -> PortRecord:
        """兼容内网 PascalCase 与外网驼峰两种字段命名。"""

        three_code = str(item.get("ThreeCode") or item.get("threeCode") or "").strip()
        country_code = str(
            item.get("CountryDoubleCode") or item.get("countryDoubleCode") or ""
        ).strip()
        english_name = str(item.get("Ready01") or item.get("ready01") or "").strip()
        return PortRecord(
            three_code=three_code,
            country_code=country_code,
            english_name=english_name,
        )
