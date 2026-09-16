"""委托客户校验：拉取全量主数据 + 本地确定性匹配，供提交前校验使用。

与港口主数据同源（poOrder PublicWebApi，同一个服务）。PubFCustom 首次全量
约 1.4 万条，因此：
- 本地只保留校验需要的少数字段（见 schemas/customer.py）；
- 落盘缓存 + TTL，过期后按 timestamp 水位增量合并，避免反复拉全量；
- 拉取失败时沿用旧缓存，不影响主流程。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.collector.customer_reference_collector import CustomerReferenceCollector
from app.collector.customer_reference_index import (
    DEFAULT_SEARCH_LIMIT,
    CustomerReferenceIndex,
)
from app.config import Settings
from app.schemas.customer import (
    CustomerCandidate,
    CustomerRecord,
    CustomerReferenceCache,
    CustomerValidationOutcome,
)
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)

CACHE_FILE_NAME = "customers.json"


class CustomerService:
    """委托客户校验服务。"""

    def __init__(self, settings: Settings, file_store: FileStore) -> None:
        self.settings = settings
        self.file_store = file_store
        self.cache_path = file_store.root / "reference_cache" / CACHE_FILE_NAME
        # 与港口主数据是同一个服务：未单独配置时回退用 port_api_base
        api_base = settings.customer_api_base or settings.port_api_base
        self.collector = CustomerReferenceCollector(api_base) if api_base else None
        self._index: CustomerReferenceIndex | None = None

    @property
    def enabled(self) -> bool:
        """未配置主数据接口时整体停用。"""

        return self.collector is not None

    async def validate(self, raw_value: str) -> CustomerValidationOutcome:
        """校验一个委托客户输入（ID / 编码 / 名称 / 英文名均可）。"""

        text = str(raw_value or "").strip()
        if not self.enabled:
            return CustomerValidationOutcome(
                status="skipped",
                raw_value=text,
                reason="customer_api_not_configured",
            )
        index = await self._get_index()
        if index is None:
            return CustomerValidationOutcome(
                status="skipped",
                raw_value=text,
                reason="customer_data_unavailable",
            )
        lookup = index.lookup(text)
        if lookup.kind == "unique":
            candidate = lookup.candidates[0]
            if not candidate.available:
                return CustomerValidationOutcome(
                    status="unavailable",
                    raw_value=text,
                    customer=candidate,
                    matched_by=lookup.matched_by,
                    reason="customer_disabled",
                )
            return CustomerValidationOutcome(
                status="ok",
                raw_value=text,
                customer=candidate,
                matched_by=lookup.matched_by,
            )
        if lookup.kind == "ambiguous":
            return CustomerValidationOutcome(
                status="ambiguous",
                raw_value=text,
                candidates=list(lookup.candidates),
                matched_by=lookup.matched_by,
                reason=f"匹配到 {len(lookup.candidates)} 个客户，请人工确认",
            )
        return CustomerValidationOutcome(
            status="not_found", raw_value=text, reason="customer_not_found"
        )

    async def search(
        self, keyword: str, limit: int = DEFAULT_SEARCH_LIMIT
    ) -> list[CustomerCandidate]:
        """按关键字搜索委托客户候选；未启用或主数据不可用时返回空列表。

        与 validate 的区别：这里返回的是「供人挑选的候选列表」，不做唯一性
        判定；调用方（前端下拉）据此让用户选定一个存在的客户。
        """

        if not self.enabled:
            return []
        index = await self._get_index()
        if index is None:
            return []
        return index.search(keyword, limit)

    async def _get_index(self) -> CustomerReferenceIndex | None:
        """返回客户索引；必要时拉取（首次全量，之后按 timestamp 增量）。"""

        if self._index is not None:
            return self._index
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
                logger.warning("委托客户主数据拉取失败且无本地缓存，客户校验跳过")
                return None
            # 拉取失败但有过期缓存：沿用旧数据，避免主数据抖动影响校验
        self._index = CustomerReferenceIndex(cache.records)
        logger.info("委托客户主数据索引就绪：%s 条记录", self._index.size)
        return self._index

    def _read_cache(self) -> CustomerReferenceCache | None:
        if not self.cache_path.exists():
            return None
        try:
            return CustomerReferenceCache.model_validate(
                self.file_store.read_json(self.cache_path)
            )
        except Exception:
            logger.exception("委托客户主数据缓存读取失败，将重新拉取")
            return None

    def _cache_expired(self, cache: CustomerReferenceCache) -> bool:
        try:
            fetched_at = datetime.fromisoformat(cache.fetched_at)
        except ValueError:
            return True
        return (
            datetime.now().astimezone() - fetched_at
            > timedelta(hours=self.settings.customer_cache_ttl_hours)
        )

    async def _fetch(self, timestamp: int) -> list[CustomerRecord]:
        if self.collector is None:
            return []
        try:
            return await self.collector.fetch_records(timestamp)
        except Exception:
            logger.exception(
                "委托客户主数据拉取失败：%s",
                self.settings.customer_api_base or self.settings.port_api_base,
            )
            return []

    def _merge(
        self,
        cache: CustomerReferenceCache | None,
        fetched: list[CustomerRecord],
    ) -> CustomerReferenceCache:
        by_id: dict[str, CustomerRecord] = {
            record.id: record for record in (cache.records if cache else [])
        }
        for record in fetched:
            if record.id:
                by_id[record.id] = record
        return CustomerReferenceCache(
            fetched_at=datetime.now().astimezone().isoformat(),
            records=list(by_id.values()),
        )

    def _write_cache(self, cache: CustomerReferenceCache) -> None:
        self.file_store.write_json_atomic(self.cache_path, cache.model_dump(mode="json"))
