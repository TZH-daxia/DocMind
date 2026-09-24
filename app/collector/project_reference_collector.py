"""委托项目主数据外部采集（poOrder PublicWebApi /api/PubCustom）。

与委托客户主数据同一套参数结构（`type=all` + `timestamp` 增量），差别只是
`comxz=-1`（客户 + 供应商的项目，即最小结算单位）。poOrder 在缓存时既按
`usr_status_cw == 1` 过滤，又把名称切成简称，这里保持同一口径，避免下游
再各自处理一遍。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.schemas.project import ProjectRecord, split_code, split_name

logger = logging.getLogger(__name__)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class ProjectReferenceCollector:
    """拉取项目主数据（支持增量）。"""

    def __init__(self, api_base: str, timeout_seconds: float = 30.0) -> None:
        self.api_base = api_base if api_base.endswith("/") else f"{api_base}/"
        self.timeout_seconds = timeout_seconds

    async def fetch_records(self, timestamp: int = 0) -> list[ProjectRecord]:
        """拉取项目记录；`timestamp` 给上次拉取的水位则只拿增量。"""

        url = f"{self.api_base}api/PubCustom"
        params: dict[str, str | int] = {
            "type": "all",
            "comxz": "-1",
            "area": "",
            "timestamp": timestamp,
            "system": "",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise TypeError("PubCustom 返回结构不是数组")
        records = [
            self._parse_record(item) for item in payload if isinstance(item, dict)
        ]
        # 与 poOrder 一致：只保留财务有效的记录，无效记录不进缓存
        valid = [record for record in records if record.usr_status_cw == 1]
        logger.info("项目主数据拉取完成：原始 %s 条，财务有效 %s 条", len(records), len(valid))
        return valid

    @staticmethod
    def _parse_record(item: dict[str, Any]) -> ProjectRecord:
        def text(key: str) -> str:
            return str(item.get(key) or "").strip()

        full_name = text("usr_name")
        return ProjectRecord(
            id=text("id"),
            fid=text("fid"),
            full_name=full_name,
            usr_name=split_name(full_name),
            usr_code=split_code(text("usr_code")),
            usr_status=_to_int(item.get("usr_status")),
            usr_status_cw=_to_int(item.get("usr_status_cw")),
            comxz=text("comxz"),
            customxz=_to_int(item.get("customxz")),
            timestamp=_to_int(item.get("timestamp")),
        )
