"""始发港/到达港三字码归一化：本地确定性优先，模型只做消歧与补全。

流水线（成本递增，上一层能定论就不进下一层）：

1. 本地主数据匹配（三字码直通 / 整串相等 / 词元相等与前缀）→ 唯一命中直接出结果；
2. 多命中 → 交给模型在候选内选择（模型输出仍要回落到候选，不允许自创码）；
3. 未命中且"像地名" → 交给模型给出规范英文名，再由主数据映射成三字码；
4. 明显不是地名（国家/地区、费用条款、表头词）→ 直接判非港口，不消耗模型。

任何来自模型的结论都必须经主数据校验，校验不过一律转人工审核，绝不静默采纳。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.agent.port_normalizer import PortNormalizationAgent
from app.collector.port_reference_collector import PortReferenceCollector
from app.collector.port_reference_index import (
    PortReferenceIndex,
    looks_like_place,
    normalize_port_text,
)
from app.config import Settings
from app.prompts.loader import load_port_code_proposal_prompt
from app.schemas.port import (
    PortCandidate,
    PortCodeSuggestion,
    PortNormalizationOutcome,
    PortOutcomeCache,
    PortRecord,
    PortReferenceCache,
    PortSuggestionInput,
)
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)

CACHE_FILE_NAME = "hbinfo.json"
OUTCOME_CACHE_FILE_NAME = "port_outcomes.json"


class PortNormalizationService:
    """始发港/到达港三字码归一化。"""

    def __init__(self, settings: Settings, file_store: FileStore) -> None:
        self.settings = settings
        self.file_store = file_store
        self.cache_path = file_store.root / "reference_cache" / CACHE_FILE_NAME
        self.outcome_cache_path = (
            file_store.root / "reference_cache" / OUTCOME_CACHE_FILE_NAME
        )
        self.collector = (
            PortReferenceCollector(settings.port_api_base) if settings.port_api_base else None
        )
        self.agent = PortNormalizationAgent(settings)
        self._index: PortReferenceIndex | None = None
        self._outcome_version: str = ""
        self._outcomes: dict[str, PortNormalizationOutcome] = {}

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

        outcomes: dict[str, PortNormalizationOutcome] = {}
        pending: list[PortSuggestionInput] = []
        for key, value in fields.items():
            raw_value = str(value).strip()
            cached = self._outcomes.get(normalize_port_text(raw_value))
            if cached is not None:
                outcomes[key] = cached.model_copy(
                    update={"field_key": key, "raw_value": raw_value}
                )
                continue
            lookup = index.lookup(raw_value)
            if lookup.kind == "unique":
                outcomes[key] = self._normalized(
                    key, raw_value, lookup.candidates[0], lookup.matched_by or "name_exact"
                )
                continue
            if lookup.kind == "ambiguous":
                outcomes[key] = PortNormalizationOutcome(
                    field_key=key,
                    status="ambiguous",
                    raw_value=raw_value,
                    candidates=list(lookup.candidates),
                    matched_by=lookup.matched_by,
                    reason=f"主数据匹配到 {lookup.total} 个候选，原文未限定具体机场/港口",
                )
                pending.append(
                    PortSuggestionInput(
                        field_key=key,
                        raw_value=raw_value,
                        candidates=list(lookup.candidates),
                    )
                )
                continue
            if not looks_like_place(raw_value):
                outcomes[key] = PortNormalizationOutcome(
                    field_key=key,
                    status="not_a_port",
                    raw_value=raw_value,
                    reason="不是港口或机场（国家/地区、费用条款或表头词）",
                )
                continue
            # 主数据没有、但像地名：先占位为待审核，等模型给出规范英文名后再映射
            outcomes[key] = PortNormalizationOutcome(
                field_key=key,
                status="failed",
                raw_value=raw_value,
                reason="not_in_reference",
            )
            pending.append(
                PortSuggestionInput(field_key=key, raw_value=raw_value, candidates=[])
            )

        if pending:
            await self._apply_model_suggestions(index, pending, outcomes)
        self._store_outcomes(outcomes)
        return outcomes

    async def _apply_model_suggestions(
        self,
        index: PortReferenceIndex,
        pending: list[PortSuggestionInput],
        outcomes: dict[str, PortNormalizationOutcome],
    ) -> None:
        """调用模型消歧/补全，并把输出回落到主数据做校验。"""

        timeout = self.settings.port_model_timeout_seconds
        try:
            result = await asyncio.wait_for(
                self.agent.suggest(load_port_code_proposal_prompt(), pending),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning(
                "港口识别模型超时（%.1fs），%s 个字段转人工审核", timeout, len(pending)
            )
            self._mark_pending(pending, outcomes, "model_timeout")
            return
        except Exception:
            logger.exception("港口识别模型调用失败，相关字段转人工审核")
            self._mark_pending(pending, outcomes, "model_failed")
            return

        suggestion_by_key = {item.field_key: item for item in result.suggestions}
        for request in pending:
            outcomes[request.field_key] = self._resolve(
                index, request, suggestion_by_key.get(request.field_key)
            )

    @staticmethod
    def _mark_pending(
        pending: list[PortSuggestionInput],
        outcomes: dict[str, PortNormalizationOutcome],
        reason: str,
    ) -> None:
        for request in pending:
            current = outcomes.get(request.field_key)
            if current is not None:
                outcomes[request.field_key] = current.model_copy(
                    update={"reason": reason}
                )

    def _resolve(
        self,
        index: PortReferenceIndex,
        request: PortSuggestionInput,
        suggestion: PortCodeSuggestion | None,
    ) -> PortNormalizationOutcome:
        """把模型的输出落到主数据上；任何对不上的结论都不采纳。"""

        raw_value = request.raw_value
        if suggestion is None:
            # 模型漏掉了这个字段：本地有候选就保留候选（人工可选），否则转人工审核
            if request.candidates:
                return PortNormalizationOutcome(
                    field_key=request.field_key,
                    status="ambiguous",
                    raw_value=raw_value,
                    candidates=list(request.candidates),
                    reason="model_no_output",
                )
            return PortNormalizationOutcome(
                field_key=request.field_key,
                status="failed",
                raw_value=raw_value,
                reason="model_no_output",
            )
        reason = (suggestion.reason or "").strip() or None
        # ① 候选选择：必须来自本地候选集，避免模型自创三字码
        code = (suggestion.chosen_code or "").strip().upper()
        if code:
            candidate = next(
                (item for item in request.candidates if item.three_code.upper() == code),
                None,
            )
            if candidate is None:
                return PortNormalizationOutcome(
                    field_key=request.field_key,
                    status="failed",
                    raw_value=raw_value,
                    candidates=list(request.candidates),
                    reason="model_choice_not_in_candidates",
                )
            return self._normalized(
                request.field_key, raw_value, candidate, "model_choice"
            )
        # ② 规范英文名：回到主数据做确定性映射（模型不直接给码）
        english_name = (suggestion.english_name or "").strip()
        if english_name:
            lookup = index.lookup(english_name)
            if lookup.kind == "unique":
                return self._normalized(
                    request.field_key, raw_value, lookup.candidates[0], "model_translation"
                )
            if lookup.kind == "ambiguous":
                return PortNormalizationOutcome(
                    field_key=request.field_key,
                    status="ambiguous",
                    raw_value=raw_value,
                    candidates=list(lookup.candidates),
                    matched_by=lookup.matched_by,
                    reason=reason or "模型给出的港口名仍对应多个候选",
                )
            return PortNormalizationOutcome(
                field_key=request.field_key,
                status="failed",
                raw_value=raw_value,
                reason="model_name_not_found",
            )
        # ③ 模型无法定论：本地本来就有多个候选时保留候选（人工一眼可选），
        # 否则转人工审核；两种情况都不采纳任何"猜"出来的码
        if request.candidates:
            return PortNormalizationOutcome(
                field_key=request.field_key,
                status="ambiguous",
                raw_value=raw_value,
                candidates=list(request.candidates),
                reason=reason or "原文未限定具体机场/港口",
            )
        return PortNormalizationOutcome(
            field_key=request.field_key,
            status="failed",
            raw_value=raw_value,
            reason=reason or "model_uncertain",
        )

    @staticmethod
    def _normalized(
        field_key: str,
        raw_value: str,
        candidate: PortCandidate,
        matched_by: str,
    ) -> PortNormalizationOutcome:
        return PortNormalizationOutcome(
            field_key=field_key,
            status="normalized",
            raw_value=raw_value,
            three_code=candidate.three_code,
            english_name=candidate.english_name,
            assembled=candidate.three_code,
            candidates=[candidate],
            matched_by=matched_by,
        )

    async def _get_reference_index(self) -> PortReferenceIndex | None:
        """返回主数据索引；必要时拉取并写缓存。"""

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
        self._index = PortReferenceIndex(cache.records)
        logger.info(
            "港口主数据索引就绪：%s 条记录（版本 %s）", self._index.size, cache.fetched_at
        )
        self._load_outcomes(cache.fetched_at)
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

    def _load_outcomes(self, version: str) -> None:
        """载入归一化结果缓存；主数据版本变化时整体作废。"""

        if self._outcome_version == version:
            return
        self._outcome_version = version
        self._outcomes = {}
        if not self.outcome_cache_path.exists():
            return
        try:
            cache = PortOutcomeCache.model_validate(
                self.file_store.read_json(self.outcome_cache_path)
            )
        except Exception:
            logger.exception("港口归一化结果缓存读取失败，将重新计算")
            return
        if cache.version != version:
            logger.info("港口主数据已更新，丢弃旧的归一化结果缓存")
            return
        self._outcomes = dict(cache.entries)

    def _store_outcomes(self, outcomes: dict[str, PortNormalizationOutcome]) -> None:
        """把本次结论写回缓存：按原文索引，字段无关，供后续任务直接复用。"""

        for outcome in outcomes.values():
            key = normalize_port_text(outcome.raw_value)
            if not key:
                continue
            self._outcomes[key] = outcome.model_copy(update={"field_key": ""})
        try:
            self.file_store.write_json_atomic(
                self.outcome_cache_path,
                PortOutcomeCache(
                    version=self._outcome_version, entries=self._outcomes
                ).model_dump(mode="json"),
            )
        except Exception:
            logger.exception("港口归一化结果缓存写入失败")
