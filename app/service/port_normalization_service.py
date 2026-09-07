"""港口三字码归一化服务：拉取/缓存主数据，模型生成三字码后查表校验。"""

import logging
from datetime import datetime, timedelta

from app.agent.port_normalizer import PortNormalizationAgent
from app.collector.port_reference_collector import PortReferenceCollector
from app.config import Settings
from app.prompts.loader import load_port_code_proposal_prompt
from app.schemas.port import (
    PortFieldInput,
    PortNormalizationOutcome,
    PortRecord,
    PortReferenceCache,
)
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)

CACHE_FILE_NAME = "hbinfo.json"


class PortNormalizationService:
    """始发港/到达港三字码归一化。

    流程：本地缓存/拉取 PubAirPortArea 全量主数据 → 模型给出三字码候选 →
    查表校验三字码存在 → 输出标准大写三字码。
    无法唯一确定三字码时保留原值并返回 skipped；查表失败时返回 failed，
    由调用方决定降级策略。
    """

    def __init__(self, settings: Settings, file_store: FileStore) -> None:
        self.settings = settings
        self.file_store = file_store
        self.cache_path = file_store.root / "reference_cache" / CACHE_FILE_NAME
        self.collector = (
            PortReferenceCollector(settings.port_api_base) if settings.port_api_base else None
        )
        self.agent = PortNormalizationAgent(settings)
        self._index: dict[str, PortRecord] | None = None

    @property
    def enabled(self) -> bool:
        """未配置港口主数据接口时整体停用。"""

        return self.collector is not None

    async def normalize(self, fields: dict[str, str]) -> dict[str, PortNormalizationOutcome]:
        """对给定字段执行归一化；未启用或主数据不可用时返回空 dict（调用方保留原值）。"""

        if not fields or not self.enabled:
            return {}
        index = await self._get_reference_index()
        if index is None:
            return {}
        inputs = [
            PortFieldInput(field_key=key, raw_value=value.strip())
            for key, value in fields.items()
        ]
        try:
            proposals = await self.agent.propose_codes(
                load_port_code_proposal_prompt(), inputs
            )
        except Exception:
            logger.exception("港口三字码候选生成失败，跳过归一化")
            return {}
        proposal_by_key = {item.field_key: item for item in proposals.proposals}

        outcomes: dict[str, PortNormalizationOutcome] = {}
        for item in inputs:
            proposal = proposal_by_key.get(item.field_key)
            code = (proposal.three_code if proposal else "").strip().upper()
            if not code:
                outcomes[item.field_key] = PortNormalizationOutcome(
                    field_key=item.field_key,
                    status="skipped",
                    raw_value=item.raw_value,
                    reason=(proposal.reason if proposal else "") or "no_unique_code",
                )
                continue
            record = index.get(code)
            if record is None:
                # 查表查不到：可能是内陆城市或模型候选码有误，交回调用方降级
                outcomes[item.field_key] = PortNormalizationOutcome(
                    field_key=item.field_key,
                    status="failed",
                    raw_value=item.raw_value,
                    three_code=code or None,
                    reason="reference_miss",
                )
            else:
                outcomes[item.field_key] = PortNormalizationOutcome(
                    field_key=item.field_key,
                    status="normalized",
                    raw_value=item.raw_value,
                    three_code=record.three_code.upper(),
                    english_name=record.english_name,
                    assembled=record.three_code.upper(),
                )
        return outcomes

    async def _get_reference_index(self) -> dict[str, PortRecord] | None:
        """返回按三字码（大写）索引的主数据；必要时拉取并写缓存。"""

        if self._index is not None:
            return self._index
        cache = self._read_cache()
        if cache is None or self._cache_expired(cache):
            records = await self._fetch_records()
            if records:
                cache = self._write_cache(records)
            elif cache is None:
                logger.warning("港口主数据拉取失败且无本地缓存，港口归一化本次跳过")
                return None
            # 拉取失败但有过期缓存：沿用旧数据，避免主数据抖动影响主流程
        self._index = {record.three_code.upper(): record for record in cache.records}
        return self._index

    def _read_cache(self) -> PortReferenceCache | None:
        if not self.cache_path.exists():
            return None
        try:
            return PortReferenceCache.model_validate(self.file_store.read_json(self.cache_path))
        except Exception:
            logger.exception("港口主数据缓存读取失败，将重新拉取")
            return None

    def _cache_expired(self, cache: PortReferenceCache) -> bool:
        try:
            fetched_at = datetime.fromisoformat(cache.fetched_at)
        except ValueError:
            return True
        return (
            datetime.now().astimezone() - fetched_at
            > timedelta(hours=self.settings.port_cache_ttl_hours)
        )

    async def _fetch_records(self) -> list[PortRecord]:
        if self.collector is None:
            return []
        try:
            return await self.collector.fetch_records()
        except Exception:
            logger.exception("港口主数据拉取失败：%s", self.settings.port_api_base)
            return []

    def _write_cache(self, records: list[PortRecord]) -> PortReferenceCache:
        cache = PortReferenceCache(
            fetched_at=datetime.now().astimezone().isoformat(),
            records=records,
        )
        self.file_store.write_json_atomic(self.cache_path, cache.model_dump(mode="json"))
        return cache
