"""委托客户主数据外部采集（poOrder PublicWebApi /api/PubFCustom）。

与港口主数据同一个服务：接口 `api/PubFCustom` 支持按 `timestamp` 增量返回，
首次拉全量约 1.4 万条，因此本地只保留校验需要的少数字段以压缩缓存体积。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.schemas.customer import CustomerRecord

logger = logging.getLogger(__name__)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class CustomerReferenceCollector:
    """拉取委托客户主数据（支持增量）。"""

    def __init__(self, api_base: str, timeout_seconds: float = 30.0) -> None:
        self.api_base = api_base if api_base.endswith("/") else f"{api_base}/"
        self.timeout_seconds = timeout_seconds

    async def fetch_records(self, timestamp: int = 0) -> list[CustomerRecord]:
        """拉取客户记录；`timestamp` 给上次拉取的水位则只拿增量。"""

        url = f"{self.api_base}api/PubFCustom"
        params: dict[str, str | int] = {
            "type": "all",
            "area": "",
            "timestamp": timestamp,
            "system": "",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise TypeError("PubFCustom 返回结构不是数组")
        return [self._parse_record(item) for item in payload if isinstance(item, dict)]

    @staticmethod
    def _parse_record(item: dict[str, Any]) -> CustomerRecord:
        def text(key: str) -> str:
            return str(item.get(key) or "").strip()

        return CustomerRecord(
            id=str(item.get("id") or "").strip(),
            usr_code=text("usr_code"),
            usr_name=text("usr_name"),
            ename=text("ename"),
            usr_status=_to_int(item.get("usr_status")),
            customxz=_to_int(item.get("customxz")),
            timestamp=_to_int(item.get("timestamp")),
        )
