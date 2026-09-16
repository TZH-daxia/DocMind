"""港口关键字搜索测试：索引排序与「输入即下拉」的服务出口。"""

from pathlib import Path

from app.collector.port_reference_index import PortReferenceIndex
from app.config import Settings
from app.schemas.port import PortRecord
from app.service.port_normalization_service import PortNormalizationService
from app.storage.file_store import FileStore


def record(code: str, name: str, country: str = "") -> PortRecord:
    return PortRecord(three_code=code, english_name=name, country_code=country)


def make_index() -> PortReferenceIndex:
    return PortReferenceIndex(
        [
            record("PVG", "SHANGHAI PUDONG", "CN"),
            record("SHA", "SHANGHAI HONGQIAO", "CN"),
            record("FRA", "FRANKFURT INTL", "DE"),
            # 少数非 3 位的港口码：精确查询也要能命中
            record("1234", "INLAND CARGO CITY", "CN"),
            # 三字码为空的历史脏数据：不应出现在候选中
            record("", "SHANGHAI OLD", "CN"),
        ]
    )


def test_search_by_three_code_returns_exact_hit() -> None:
    items = make_index().search("pvg")

    assert [item.three_code for item in items] == ["PVG"]
    assert items[0].english_name == "SHANGHAI PUDONG"
    assert items[0].country_code == "CN"


def test_search_by_code_prefix() -> None:
    items = make_index().search("PV")

    assert [item.three_code for item in items] == ["PVG"]


def test_search_by_non_three_char_code_is_exact() -> None:
    # 主数据里存在少数非 3 位码，精确查询不应被"必须是三字码"的规则挡住
    items = make_index().search("1234")

    assert [item.three_code for item in items] == ["1234"]


def test_search_by_code_prefix_accepts_digits() -> None:
    items = make_index().search("12")

    assert [item.three_code for item in items] == ["1234"]


def test_search_by_name_prefix_orders_by_name() -> None:
    items = make_index().search("SHANGHAI")

    # 两个都是前缀命中，按名称排序（HONGQIAO 在 PUDONG 之前）保证结果稳定
    assert [item.three_code for item in items] == ["SHA", "PVG"]


def test_search_by_name_contains() -> None:
    items = make_index().search("PUDONG")

    assert [item.three_code for item in items] == ["PVG"]


def test_search_by_name_token() -> None:
    items = make_index().search("FRANKFURT")

    assert [item.three_code for item in items] == ["FRA"]


def test_search_ignores_blank_and_too_short_keyword() -> None:
    index = make_index()

    assert index.search("   ") == []
    assert index.search("S") == []


def test_search_respects_limit() -> None:
    items = make_index().search("SHANGHAI", limit=1)

    assert len(items) == 1


async def test_service_search_returns_empty_when_api_not_configured(
    tmp_path: Path,
) -> None:
    # 显式清空主数据地址，避免受本地 .env 影响
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DOCMIND_PORT_API_BASE="",
        deepseek_api_key="test-key",
    )
    service = PortNormalizationService(settings, FileStore(settings))

    assert service.enabled is False
    assert await service.search("SHANGHAI") == []


async def test_service_search_uses_cached_index(tmp_path: Path) -> None:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DOCMIND_PORT_API_BASE="http://127.0.0.1:1/PublicWebApi/",
        deepseek_api_key="test-key",
    )
    service = PortNormalizationService(settings, FileStore(settings))
    # 直接注入索引：跳过主数据拉取，只验证服务出口
    service._index = make_index()  # type: ignore[assignment]

    items = await service.search("SHANGHAI")

    assert [item.three_code for item in items] == ["SHA", "PVG"]
