"""港口归一化流水线测试（Fake Collector / Agent，不发真实请求）。

覆盖：本地命中不调模型、多义保留候选、非港口短路、模型消歧与翻译、
幻觉拒绝、超时降级、结果缓存。
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.schemas.port import (
    PortCodeSuggestion,
    PortCodeSuggestionResult,
    PortRecord,
    PortSuggestionInput,
)
from app.service.port_normalization_service import PortNormalizationService
from app.storage.file_store import FileStore

REFERENCE_RECORDS = [
    PortRecord(three_code="PVG", country_code="CN", english_name="SHANGHAIPUDONG"),
    PortRecord(three_code="SHA", country_code="CN", english_name="SHANGHAIHONGQIAO"),
    PortRecord(three_code="FRA", country_code="DE", english_name="FRANKFURT"),
    PortRecord(three_code="NGB", country_code="CN", english_name="NINGBO"),
]


class FakeCollector:
    def __init__(self, records: list[PortRecord] | None = None, error: bool = False) -> None:
        self.records = records if records is not None else REFERENCE_RECORDS
        self.error = error
        self.calls = 0

    async def fetch_records(self) -> list[PortRecord]:
        self.calls += 1
        if self.error:
            raise RuntimeError("api down")
        return self.records


class FakeAgent:
    """按 field_key 返回预设建议，并记录调用次数与请求内容。"""

    def __init__(
        self,
        suggestions: dict[str, PortCodeSuggestion] | None = None,
        delay: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.suggestions = suggestions or {}
        self.delay = delay
        self.error = error
        self.calls = 0
        self.requested: list[PortSuggestionInput] = []

    async def suggest(
        self, system_prompt: str, items: list[PortSuggestionInput]
    ) -> PortCodeSuggestionResult:
        self.calls += 1
        self.requested = list(items)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return PortCodeSuggestionResult(
            suggestions=[
                self.suggestions.get(
                    item.field_key, PortCodeSuggestion(field_key=item.field_key)
                )
                for item in items
            ]
        )


_UNSET = object()


def make_service(
    tmp_path: Path,
    collector: FakeCollector | None | object = _UNSET,
    agent: FakeAgent | None = None,
    port_api_base: str = "http://fake/",
    timeout: float = 8.0,
) -> PortNormalizationService:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DOCMIND_PORT_API_BASE=port_api_base,
        DOCMIND_PORT_MODEL_TIMEOUT_SECONDS=timeout,
        deepseek_api_key="test-key",
    )
    service = PortNormalizationService(settings, FileStore(settings))
    if collector is _UNSET:
        service.collector = FakeCollector()
    elif collector is not None:
        service.collector = collector  # type: ignore[assignment]
    service.agent = agent or FakeAgent()
    return service


async def test_disabled_without_api_base(tmp_path: Path) -> None:
    service = make_service(tmp_path, port_api_base="", collector=None)

    assert service.enabled is False
    assert await service.normalize({"sfg": "SHANGHAI"}) == {}


async def test_three_code_passthrough_without_model(tmp_path: Path) -> None:
    agent = FakeAgent()
    service = make_service(tmp_path, agent=agent)

    outcomes = await service.normalize({"sfg": "pvg"})

    assert outcomes["sfg"].status == "normalized"
    assert outcomes["sfg"].assembled == "PVG"
    assert outcomes["sfg"].matched_by == "code"
    assert agent.calls == 0


async def test_unique_name_hit_without_model(tmp_path: Path) -> None:
    agent = FakeAgent()
    service = make_service(tmp_path, agent=agent)

    outcomes = await service.normalize({"mdg": "Frankfurt, Germany"})

    assert outcomes["mdg"].status == "normalized"
    assert outcomes["mdg"].assembled == "FRA"
    assert outcomes["mdg"].matched_by in {"name_exact", "name_token"}
    assert agent.calls == 0
    assert (tmp_path / "reference_cache" / "hbinfo.json").exists()


async def test_ambiguous_city_keeps_candidates(tmp_path: Path) -> None:
    """模型也定不下时，多义字段要带着候选转人工，而不是只剩"待审核"。"""

    agent = FakeAgent()
    service = make_service(tmp_path, agent=agent)

    outcomes = await service.normalize({"sfg": "SHANGHAI"})

    assert outcomes["sfg"].status == "ambiguous"
    assert {item.three_code for item in outcomes["sfg"].candidates} == {"PVG", "SHA"}
    assert agent.calls == 1
    assert agent.requested[0].candidates  # 候选确实交给了模型


async def test_model_missing_field_keeps_candidates(tmp_path: Path) -> None:
    """模型漏答某个字段时，多义候选不能丢。"""

    class SilentAgent(FakeAgent):
        async def suggest(
            self, system_prompt: str, items: list[PortSuggestionInput]
        ) -> PortCodeSuggestionResult:
            self.calls += 1
            return PortCodeSuggestionResult(suggestions=[])

    agent = SilentAgent()
    service = make_service(tmp_path, agent=agent)

    outcomes = await service.normalize({"sfg": "SHANGHAI"})

    assert outcomes["sfg"].status == "ambiguous"
    assert {item.three_code for item in outcomes["sfg"].candidates} == {"PVG", "SHA"}


async def test_model_picks_candidate(tmp_path: Path) -> None:
    agent = FakeAgent(
        suggestions={"sfg": PortCodeSuggestion(field_key="sfg", chosen_code="sha")}
    )
    service = make_service(tmp_path, agent=agent)

    outcomes = await service.normalize({"sfg": "SHANGHAI"})

    assert outcomes["sfg"].status == "normalized"
    assert outcomes["sfg"].assembled == "SHA"
    assert outcomes["sfg"].matched_by == "model_choice"


async def test_model_choice_outside_candidates_rejected(tmp_path: Path) -> None:
    """模型自创三字码（不在候选内）一律不采纳。"""

    agent = FakeAgent(
        suggestions={"sfg": PortCodeSuggestion(field_key="sfg", chosen_code="XXX")}
    )
    service = make_service(tmp_path, agent=agent)

    outcomes = await service.normalize({"sfg": "SHANGHAI"})

    assert outcomes["sfg"].status == "failed"
    assert outcomes["sfg"].reason == "model_choice_not_in_candidates"


async def test_not_a_port_skips_model(tmp_path: Path) -> None:
    agent = FakeAgent()
    service = make_service(tmp_path, agent=agent)

    outcomes = await service.normalize({"sfg": "FOB", "mdg": "GERMANY", "zdg": "德国"})

    assert outcomes["sfg"].status == "not_a_port"
    assert outcomes["mdg"].status == "not_a_port"
    assert outcomes["zdg"].status == "not_a_port"
    assert agent.calls == 0


async def test_model_translation_for_chinese_name(tmp_path: Path) -> None:
    """中文地名：模型只给规范英文名，三字码由主数据映射。"""

    agent = FakeAgent(
        suggestions={"sfg": PortCodeSuggestion(field_key="sfg", english_name="Frankfurt")}
    )
    service = make_service(tmp_path, agent=agent)

    outcomes = await service.normalize({"sfg": "法兰克福"})

    assert outcomes["sfg"].status == "normalized"
    assert outcomes["sfg"].assembled == "FRA"
    assert outcomes["sfg"].matched_by == "model_translation"
    assert agent.calls == 1


async def test_model_translation_hallucination_rejected(tmp_path: Path) -> None:
    """模型编造的港口名在 主数据里查不到 → 不采纳，转人工审核。"""

    agent = FakeAgent(
        suggestions={
            "sfg": PortCodeSuggestion(field_key="sfg", english_name="NOWHEREPORT")
        }
    )
    service = make_service(tmp_path, agent=agent)

    outcomes = await service.normalize({"sfg": "某某港"})

    assert outcomes["sfg"].status == "failed"
    assert outcomes["sfg"].reason == "model_name_not_found"


async def test_model_timeout_falls_back_to_review(tmp_path: Path) -> None:
    agent = FakeAgent(delay=0.3)
    service = make_service(tmp_path, agent=agent, timeout=0.05)

    outcomes = await service.normalize({"sfg": "法兰克福"})

    assert outcomes["sfg"].status == "failed"
    assert outcomes["sfg"].reason == "model_timeout"


async def test_outcome_cache_avoids_second_model_call(tmp_path: Path) -> None:
    agent = FakeAgent()
    service = make_service(tmp_path, agent=agent)

    first = await service.normalize({"sfg": "SHANGHAI"})
    assert first["sfg"].status == "ambiguous"
    assert agent.calls == 1

    second = await service.normalize({"mdg": "SHANGHAI"})
    assert second["mdg"].status == "ambiguous"
    assert {item.three_code for item in second["mdg"].candidates} == {"PVG", "SHA"}
    assert agent.calls == 1  # 命中结果缓存，不再调模型
    assert (tmp_path / "reference_cache" / "port_outcomes.json").exists()


async def test_fresh_cache_reused_without_fetch(tmp_path: Path) -> None:
    # 预写一份新鲜缓存；接口即使不可用也不应触发拉取
    service = make_service(tmp_path, collector=FakeCollector(error=True))
    cache_payload = {
        "fetched_at": datetime.now().astimezone().isoformat(),
        "records": [record.model_dump(mode="json") for record in REFERENCE_RECORDS],
    }
    service.file_store.write_json_atomic(service.cache_path, cache_payload)

    outcomes = await service.normalize({"sfg": "NINGBO"})

    assert outcomes["sfg"].assembled == "NGB"
    assert service.collector.calls == 0  # type: ignore[union-attr]


async def test_expired_cache_with_fetch_failure_uses_stale(tmp_path: Path) -> None:
    service = make_service(tmp_path, collector=FakeCollector(error=True))
    # 默认 TTL 已是 7 天（168h），过期样本要用更老的时间
    stale_time = datetime.now().astimezone() - timedelta(hours=240)
    cache_payload = {
        "fetched_at": stale_time.isoformat(),
        "records": [record.model_dump(mode="json") for record in REFERENCE_RECORDS],
    }
    service.file_store.write_json_atomic(service.cache_path, cache_payload)

    outcomes = await service.normalize({"sfg": "NINGBO"})

    assert outcomes["sfg"].status == "normalized"
    assert service.collector.calls == 1  # type: ignore[union-attr]


async def test_fetch_failure_without_cache_returns_empty(tmp_path: Path) -> None:
    service = make_service(tmp_path, collector=FakeCollector(error=True))

    assert await service.normalize({"sfg": "SHANGHAI"}) == {}
