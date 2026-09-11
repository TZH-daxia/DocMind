"""委托客户索引与校验服务测试（Fake Collector，不发真实请求）。"""

from datetime import datetime, timedelta
from pathlib import Path

from app.collector.customer_reference_index import (
    CustomerReferenceIndex,
    is_customer_available,
    normalize_customer_text,
)
from app.config import Settings
from app.schemas.customer import CustomerRecord
from app.service.customer_service import CustomerService
from app.storage.file_store import FileStore

RECORDS = [
    CustomerRecord(
        id="14620",
        usr_code="XPDG",
        usr_name="XPD GLOBAL (HK) LIMITED",
        ename="XPD GLOBAL",
        usr_status=1,
        customxz=1,
        timestamp=14620,
    ),
    CustomerRecord(
        id="14576",
        usr_code="ZJHLD",
        usr_name="浙江惠隆对外贸易有限责任公司",
        ename="ZHEJIANG HUILONG",
        usr_status=1,
        customxz=1,
        timestamp=14576,
    ),
    CustomerRecord(id="100", usr_code="OLD01", usr_name="停用客户甲", usr_status=0, customxz=1),
    CustomerRecord(id="101", usr_code="OLD02", usr_name="废弃客户乙", usr_status=1, customxz=2),
]


def test_normalize_strips_spaces_and_case() -> None:
    # 真实数据里 usr_name / usr_code 带前导空格，归一化必须消化掉
    assert normalize_customer_text("  XPD GLOBAL (HK) LIMITED ") == "XPDGLOBALHKLIMITED"


def test_lookup_by_id() -> None:
    index = CustomerReferenceIndex(RECORDS)

    result = index.lookup("14620")

    assert result.kind == "unique"
    assert result.candidates[0].id == "14620"
    assert result.matched_by == "id"


def test_lookup_by_name_exact() -> None:
    index = CustomerReferenceIndex(RECORDS)

    result = index.lookup("XPD GLOBAL (HK) LIMITED")

    assert result.kind == "unique"
    assert result.candidates[0].id == "14620"
    assert result.matched_by == "usr_name"


def test_lookup_by_code_and_ename() -> None:
    index = CustomerReferenceIndex(RECORDS)

    assert index.lookup("ZJHLD").matched_by == "usr_code"
    assert index.lookup("ZHEJIANG HUILONG").matched_by == "ename"


def test_lookup_by_contains() -> None:
    index = CustomerReferenceIndex(RECORDS)

    result = index.lookup("惠隆对外贸易")

    assert result.kind == "unique"
    assert result.candidates[0].id == "14576"


def test_lookup_not_found() -> None:
    index = CustomerReferenceIndex(RECORDS)

    assert index.lookup("不存在客户XYZ").kind == "not_found"
    assert index.lookup("").kind == "not_found"


def test_availability_rule_matches_poorder() -> None:
    """可用口径与 poOrder 新增订单一致：usr_status==1 且 customxz!=2。"""

    assert is_customer_available(RECORDS[0]) is True
    assert is_customer_available(RECORDS[1]) is True
    assert is_customer_available(RECORDS[2]) is False
    assert is_customer_available(RECORDS[3]) is False


def test_unavailable_customer_is_flagged() -> None:
    index = CustomerReferenceIndex(RECORDS)

    result = index.lookup("停用客户甲")

    assert result.kind == "unique"
    assert result.candidates[0].available is False


class FakeCustomerCollector:
    def __init__(self, records: list[CustomerRecord] | None = None, error: bool = False) -> None:
        self.records = records or []
        self.error = error
        self.calls: list[int] = []

    async def fetch_records(self, timestamp: int = 0) -> list[CustomerRecord]:
        self.calls.append(timestamp)
        if self.error:
            raise RuntimeError("api down")
        return self.records


def make_customer_service(tmp_path: Path, collector: FakeCustomerCollector) -> CustomerService:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DOCMIND_CUSTOMER_API_BASE="http://fake/",
        deepseek_api_key="test-key",
    )
    service = CustomerService(settings, FileStore(settings))
    service.collector = collector  # type: ignore[assignment]
    return service


async def test_validate_ok(tmp_path: Path) -> None:
    service = make_customer_service(tmp_path, FakeCustomerCollector(RECORDS))

    outcome = await service.validate("XPD GLOBAL (HK) LIMITED")

    assert outcome.status == "ok"
    assert outcome.customer is not None and outcome.customer.id == "14620"


async def test_validate_states(tmp_path: Path) -> None:
    service = make_customer_service(tmp_path, FakeCustomerCollector(RECORDS))

    assert (await service.validate("停用客户甲")).status == "unavailable"
    assert (await service.validate("不存在客户XYZ")).status == "not_found"


async def test_validate_skipped_without_api_base(tmp_path: Path) -> None:
    # 显式置空，避免读到 .env 里的真实主数据地址
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DOCMIND_PORT_API_BASE="",
        DOCMIND_CUSTOMER_API_BASE="",
        deepseek_api_key="test-key",
    )
    service = CustomerService(settings, FileStore(settings))

    outcome = await service.validate("任意")

    assert outcome.status == "skipped"


async def test_cache_avoids_refetch(tmp_path: Path) -> None:
    collector = FakeCustomerCollector(RECORDS)
    service = make_customer_service(tmp_path, collector)

    await service.validate("14620")
    await service.validate("浙江惠隆对外贸易有限责任公司")

    assert collector.calls == [0]  # 索引已在进程内，第二次不再拉取
    assert (tmp_path / "reference_cache" / "customers.json").exists()


async def test_incremental_merge_keeps_existing_records(tmp_path: Path) -> None:
    """过期缓存 + 增量拉取：旧记录保留、新记录并入，水位用已有最大 timestamp。"""

    collector = FakeCustomerCollector(
        [CustomerRecord(id="99999", usr_name="增量新客户", usr_status=1, customxz=1, timestamp=200)]
    )
    service = make_customer_service(tmp_path, collector)
    stale_time = datetime.now().astimezone() - timedelta(hours=72)
    service.file_store.write_json_atomic(
        service.cache_path,
        {
            "fetched_at": stale_time.isoformat(),
            "records": [
                record.model_dump(mode="json")
                for record in RECORDS
                if record.id in {"14620", "14576"}
            ],
        },
    )

    outcome = await service.validate("增量新客户")

    assert outcome.status == "ok"
    assert collector.calls == [14620]  # 水位 = 已有记录的最大 timestamp
    assert (await service.validate("14620")).status == "ok"  # 旧记录仍在
