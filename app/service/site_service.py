"""唯凯站点字典：外部拉取 + 本地缓存，供「委托唯凯站点」下拉展示。

与港口/客户主数据同源（poOrder PublicWebApi）。订单新增页的站点下拉取字典
`groupid == 101` 并按 `ready04` 分组展示；这里保持同一口径，差别只是把
「分组」在服务端算好返回，前端不再解析字典原文。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.collector.site_reference_collector import SiteReferenceCollector
from app.config import Settings
from app.schemas.site import SiteGroup, SiteOption, SiteRecord, SiteReferenceCache
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)

CACHE_FILE_NAME = "sites.json"


class SiteService:
    """唯凯站点字典服务。"""

    def __init__(self, settings: Settings, file_store: FileStore) -> None:
        self.settings = settings
        self.file_store = file_store
        self.cache_path = file_store.root / "reference_cache" / CACHE_FILE_NAME
        # 与港口/客户主数据是同一个服务：未单独配置时回退用 port_api_base
        api_base = settings.site_api_base or settings.port_api_base
        self.collector = SiteReferenceCollector(api_base) if api_base else None
        self._records: list[SiteRecord] | None = None

    @property
    def enabled(self) -> bool:
        """未配置主数据接口时整体停用（前端退化为没有候选）。"""

        return self.collector is not None

    async def list_groups(self) -> list[SiteGroup]:
        """返回按分组聚合的站点候选；未启用或主数据不可用时返回空列表。"""

        if not self.enabled:
            return []
        records = await self._get_records()
        if not records:
            return []
        return self._build_groups(records)

    @staticmethod
    def _build_groups(records: list[SiteRecord]) -> list[SiteGroup]:
        """按首次出现顺序聚合分组，保持字典原有次序。"""

        groups: dict[str, SiteGroup] = {}
        for record in records:
            group = groups.get(record.group)
            if group is None:
                group = groups[record.group] = SiteGroup(label=record.group)
            group.options.append(
                SiteOption(value=record.name, label=record.full_name or record.name)
            )
        return list(groups.values())

    async def _get_records(self) -> list[SiteRecord]:
        """返回站点字典；必要时重新拉取（全量覆盖，拉取失败时沿用旧缓存）。"""

        if self._records is not None:
            return self._records
        cache = self._read_cache()
        if cache is None or self._cache_expired(cache):
            fetched = await self._fetch()
            if fetched:
                cache = SiteReferenceCache(
                    fetched_at=datetime.now().astimezone().isoformat(),
                    records=fetched,
                )
                self._write_cache(cache)
            elif cache is None:
                logger.warning("唯凯站点字典拉取失败且无本地缓存，站点候选不可用")
                return []
            # 拉取失败但有过期缓存：沿用旧数据，避免主数据抖动影响下拉
        self._records = cache.records
        logger.info("唯凯站点字典索引就绪：%s 条记录", len(self._records))
        return self._records

    def _read_cache(self) -> SiteReferenceCache | None:
        if not self.cache_path.exists():
            return None
        try:
            return SiteReferenceCache.model_validate(
                self.file_store.read_json(self.cache_path)
            )
        except Exception:
            logger.exception("唯凯站点字典缓存读取失败，将重新拉取")
            return None

    def _cache_expired(self, cache: SiteReferenceCache) -> bool:
        try:
            fetched_at = datetime.fromisoformat(cache.fetched_at)
        except ValueError:
            return True
        return (
            datetime.now().astimezone() - fetched_at
            > timedelta(hours=self.settings.site_cache_ttl_hours)
        )

    async def _fetch(self) -> list[SiteRecord]:
        if self.collector is None:
            return []
        try:
            return await self.collector.fetch_records()
        except Exception:
            logger.exception(
                "唯凯站点字典拉取失败：%s",
                self.settings.site_api_base or self.settings.port_api_base,
            )
            return []

    def _write_cache(self, cache: SiteReferenceCache) -> None:
        self.file_store.write_json_atomic(self.cache_path, cache.model_dump(mode="json"))
