"""港口三字码归一化服务测试（Fake Collector / Agent，不发真实请求）。"""

from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.schemas.port import (
    PortCodeProposal,
    PortCodeProposalResult,
    PortFieldInput,
    PortRecord,
)
from app.service.port_normalization_service import PortNormalizationService
from app.storage.file_store import FileStore

REFERENCE_RECORDS = [
    PortRecord(three_code="PVG", country_code="CN", english_name="Shanghai Pudong Intl"),
    PortRecord(three_code="FRA", country_code="DE", english_name="Frankfurt Intl"),
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
    def __init__(self, codes: dict[str, str] | None = None) -> None:
        self.codes = codes or {"sfg": "pvg", "mdg": "fra"}  # 故意小写，验证大小写归一

    async def propose_codes(
        self, system_prompt: str, fields: list[PortFieldInput]
    ) -> PortCodeProposalResult:
        return PortCodeProposalResult(
            proposals=[
                PortCodeProposal(field_key=item.field_key, three_code=self.codes.get(item.field_key, ""))
                for item in fields
            ]
        )

_UNSET = object()


def make_service(
    tmp_path: Path,
    collector: FakeCollector | None | object = _UNSET,
    agent: FakeAgent | None = None,
    port_api_base: str = "http://fake/",
) -> PortNormalizationService:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DOCMIND_PORT_API_BASE=port_api_base,
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


async def test_normalize_success_and_case_insensitive(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    outcomes = await service.normalize({"sfg": "SHANGHAI", "mdg": "Frankfurt, Germany"})
    assert outcomes["sfg"].status == "normalized"
    assert outcomes["sfg"].assembled == "PVG"
    assert outcomes["sfg"].three_code == "PVG"
    assert outcomes["mdg"].assembled == "FRA"
    # 缓存文件已落盘
    assert (tmp_path / "reference_cache" / "hbinfo.json").exists()


async def test_reference_miss_marks_failed(tmp_path: Path) -> None:
    agent = FakeAgent(codes={"sfg": "pvg", "mdg": "XXX"})
    service = make_service(tmp_path, agent=agent)
    outcomes = await service.normalize({"sfg": "SHANGHAI", "mdg": "Schweinfurt, Germany"})
    assert outcomes["sfg"].status == "normalized"
    assert outcomes["mdg"].status == "failed"
    assert outcomes["mdg"].reason == "reference_miss"


async def test_ambiguous_place_skips_normalization(tmp_path: Path) -> None:
    service = make_service(tmp_path, agent=FakeAgent(codes={"sfg": ""}))
    outcomes = await service.normalize({"sfg": "SHANGHAI"})
    assert outcomes["sfg"].status == "skipped"
    assert outcomes["sfg"].reason == "no_unique_code"


async def test_fresh_cache_reused_without_fetch(tmp_path: Path) -> None:
    # 预写一份新鲜缓存；接口即使不可用也不应触发拉取
    service = make_service(tmp_path, collector=FakeCollector(error=True))
    cache_payload = {
        "fetched_at": datetime.now().astimezone().isoformat(),
        "records": [record.model_dump(mode="json") for record in REFERENCE_RECORDS],
    }
    service.file_store.write_json_atomic(service.cache_path, cache_payload)
    outcomes = await service.normalize({"sfg": "SHANGHAI"})
    assert outcomes["sfg"].assembled == "PVG"
    assert service.collector.calls == 0


async def test_expired_cache_with_fetch_failure_uses_stale(tmp_path: Path) -> None:
    service = make_service(tmp_path, collector=FakeCollector(error=True))
    stale_time = datetime.now().astimezone() - timedelta(hours=72)
    cache_payload = {
        "fetched_at": stale_time.isoformat(),
        "records": [record.model_dump(mode="json") for record in REFERENCE_RECORDS],
    }
    service.file_store.write_json_atomic(service.cache_path, cache_payload)
    outcomes = await service.normalize({"sfg": "SHANGHAI"})
    assert outcomes["sfg"].status == "normalized"
    assert service.collector.calls == 1


async def test_fetch_failure_without_cache_returns_empty(tmp_path: Path) -> None:
    service = make_service(tmp_path, collector=FakeCollector(error=True))
    assert await service.normalize({"sfg": "SHANGHAI"}) == {}
