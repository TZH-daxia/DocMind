"""委托项目主数据：拉取 + 本地缓存 + 按委托客户查询候选。

与港口/客户主数据同源（poOrder PublicWebApi），缓存策略也一致（落盘 + TTL +
按 timestamp 增量合并）。poOrder 订单新增页的项目下拉先按
`usr_status / comxz / customxz` 过滤，再按当前委托客户 `fid` 收敛，
**不按站点过滤** —— 站点约束发生在选中之后的校验（「该项目没有 X 站点权限！」），
所以这里保持同一口径。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.collector.project_reference_collector import ProjectReferenceCollector
from app.config import Settings
from app.schemas.project import (
    DEFAULT_LIST_LIMIT,
    ProjectCandidate,
    ProjectRecord,
    ProjectReferenceCache,
)
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)

CACHE_FILE_NAME = "projects.json"


class ProjectService:
    """委托项目主数据服务。"""

    def __init__(self, settings: Settings, file_store: FileStore) -> None:
        self.settings = settings
        self.file_store = file_store
        self.cache_path = file_store.root / "reference_cache" / CACHE_FILE_NAME
        # 与港口/客户主数据是同一个服务：未单独配置时回退用 port_api_base
        api_base = settings.project_api_base or settings.port_api_base
        self.collector = ProjectReferenceCollector(api_base) if api_base else None
        self._by_customer: dict[str, list[ProjectCandidate]] | None = None

    @property
    def enabled(self) -> bool:
        """未配置主数据接口时整体停用。"""

        return self.collector is not None

    async def list_by_customer(
        self,
        fid: str,
        keyword: str = "",
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[ProjectCandidate]:
        """返回某委托客户下的项目候选；可按关键字过滤名称/编码。

        未启用、主数据不可用、或 `fid` 为空时返回空列表。
        """

        customer_id = str(fid or "").strip()
        if not customer_id or not self.enabled:
            return []
        index = await self._get_index()
        if index is None:
            return []
        candidates = index.get(customer_id, [])
        text = str(keyword or "").strip().lower()
        if text:
            candidates = [
                item
                for item in candidates
                if text in item.name.lower()
                or text in item.code.lower()
                or text in item.full_name.lower()
            ]
        return candidates[: max(1, limit)]

    @staticmethod
    def _build_index(records: list[ProjectRecord]) -> dict[str, list[ProjectCandidate]]:
        """筛出可用项目并按委托客户分组（口径见模块说明）。"""

        index: dict[str, list[ProjectCandidate]] = {}
        for record in records:
            if not record.id or not record.fid:
                continue
            if record.usr_status != 1 or record.customxz == 2:
                continue
            comxz_values = [item.strip() for item in record.comxz.split(",")]
            if "1" not in comxz_values:
                continue
            index.setdefault(record.fid, []).append(
                ProjectCandidate(
                    id=record.id,
                    name=record.usr_name,
                    code=record.usr_code,
                    full_name=record.full_name,
                )
            )
        return index

    async def _get_index(self) -> dict[str, list[ProjectCandidate]] | None:
        """返回 fid → 项目候选的索引；必要时拉取（首次全量，之后按水位增量）。"""

        if self._by_customer is not None:
            return self._by_customer
        cache = self._read_cache()
        if cache is None or self._cache_expired(cache):
            watermark = (
                max((record.timestamp for record in cache.records), default=0)
                if cache
                else 0
            )
            fetched = await self._fetch(watermark)
            if fetched:
                cache = self._merge(cache, fetched)
                self._write_cache(cache)
            elif cache is None:
                logger.warning("项目主数据拉取失败且无本地缓存，项目候选不可用")
                return None
            # 拉取失败但有过期缓存：沿用旧数据，避免主数据抖动影响下拉
        self._by_customer = self._build_index(cache.records)
        logger.info(
            "项目主数据索引就绪：%s 条记录，覆盖 %s 个委托客户",
            len(cache.records),
            len(self._by_customer),
        )
        return self._by_customer

    def _read_cache(self) -> ProjectReferenceCache | None:
        if not self.cache_path.exists():
            return None
        try:
            return ProjectReferenceCache.model_validate(
                self.file_store.read_json(self.cache_path)
            )
        except Exception:
            logger.exception("项目主数据缓存读取失败，将重新拉取")
            return None

    def _cache_expired(self, cache: ProjectReferenceCache) -> bool:
        try:
            fetched_at = datetime.fromisoformat(cache.fetched_at)
        except ValueError:
            return True
        return (
            datetime.now().astimezone() - fetched_at
            > timedelta(hours=self.settings.project_cache_ttl_hours)
        )

    async def _fetch(self, timestamp: int) -> list[ProjectRecord]:
        if self.collector is None:
            return []
        try:
            return await self.collector.fetch_records(timestamp)
        except Exception:
            logger.exception(
                "项目主数据拉取失败：%s",
                self.settings.project_api_base or self.settings.port_api_base,
            )
            return []

    def _merge(
        self,
        cache: ProjectReferenceCache | None,
        fetched: list[ProjectRecord],
    ) -> ProjectReferenceCache:
        """合并新旧记录，按 **(fid, id)** 去重。

        不能只按 id：实测 `PubCustom` 的 id **不是全局唯一**（29447 行里 1305 个 id
        重复，同一个 id 会挂在多个委托客户下，例如 id=2729 同时属于 2729/2828/14086
        三个客户）。只按 id 合并会互相覆盖 —— 曾经因此把成都分公司的「基础」覆盖掉，
        导致该客户的项目候选凭空为空。
        """

        merged: dict[tuple[str, str], ProjectRecord] = {
            (record.fid, record.id): record
            for record in (cache.records if cache else [])
        }
        for record in fetched:
            if record.id:
                merged[(record.fid, record.id)] = record
        return ProjectReferenceCache(
            fetched_at=datetime.now().astimezone().isoformat(),
            records=list(merged.values()),
        )

    def _write_cache(self, cache: ProjectReferenceCache) -> None:
        self.file_store.write_json_atomic(self.cache_path, cache.model_dump(mode="json"))
