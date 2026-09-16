"""委托客户关键字搜索测试：索引排序与「输入即下拉」的服务出口。"""

from pathlib import Path

from app.collector.customer_reference_index import CustomerReferenceIndex
from app.config import Settings
from app.schemas.customer import CustomerRecord
from app.service.customer_service import CustomerService
from app.storage.file_store import FileStore


def record(
    customer_id: str,
    name: str,
    code: str = "",
    ename: str = "",
    status: int = 1,
    customxz: int = 0,
) -> CustomerRecord:
    return CustomerRecord(
        id=customer_id,
        usr_name=name,
        usr_code=code,
        ename=ename,
        usr_status=status,
        customxz=customxz,
    )


def make_index() -> CustomerReferenceIndex:
    return CustomerReferenceIndex(
        [
            record("1", "上海浦东国际物流有限公司", "XPD", "SHANGHAI PUDONG LOGISTICS"),
            record("2", "浦西货运代理", "XPX", "SHANGHAI PUXI FORWARDING"),
            # 已停用：同级别排序时排在可用客户之后
            record("3", "上海浦西贸易", "PX", "SHANGHAI PUXI TRADING", status=0),
            record("4", "XXP Logistics", "XXP", "XXP LOGISTICS"),
        ]
    )


def test_search_by_id_returns_exact_hit() -> None:
    items = make_index().search("3")

    assert [item.id for item in items] == ["3"]
    assert items[0].available is False


def test_search_ranks_prefix_before_contains() -> None:
    items = make_index().search("浦西")

    # id=2 名称以"浦西"开头（前缀），id=3 只是包含，前缀优先
    assert [item.id for item in items] == ["2", "3"]


def test_search_prefers_available_within_same_rank() -> None:
    items = make_index().search("上海")

    # id=1 与 id=3 都是前缀命中，可用的 id=1 排在停用的 id=3 之前
    assert [item.id for item in items] == ["1", "3"]


def test_search_matches_code_exactly() -> None:
    items = make_index().search("XPX")

    assert [item.id for item in items] == ["2"]


def test_search_respects_limit_and_ignores_blank_keyword() -> None:
    index = make_index()

    assert len(index.search("上海", limit=1)) == 1
    assert index.search("   ") == []


async def test_service_search_returns_empty_when_api_not_configured(
    tmp_path: Path,
) -> None:
    # 显式清空主数据地址，避免受本地 .env 影响
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DOCMIND_PORT_API_BASE="",
        DOCMIND_CUSTOMER_API_BASE="",
        deepseek_api_key="test-key",
    )
    service = CustomerService(settings, FileStore(settings))

    assert service.enabled is False
    assert await service.search("上海") == []


async def test_service_search_uses_cached_index(tmp_path: Path) -> None:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DOCMIND_PORT_API_BASE="http://127.0.0.1:1/PublicWebApi/",
        deepseek_api_key="test-key",
    )
    service = CustomerService(settings, FileStore(settings))
    # 直接注入索引：跳过主数据拉取，只验证服务出口
    service._index = make_index()  # type: ignore[assignment]

    items = await service.search("浦西")

    assert [item.id for item in items] == ["2", "3"]
