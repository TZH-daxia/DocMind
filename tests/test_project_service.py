"""项目主数据服务：按委托客户收敛、可用性过滤、缓存复用与未配置时的降级。"""

from pathlib import Path

from app.config import Settings
from app.schemas.project import ProjectRecord
from app.service.project_service import ProjectService
from app.storage.file_store import FileStore


class FakeCollector:
    """替身采集器：记录调用次数并返回固定记录，避免测试打真实接口。"""

    def __init__(self, records: list[ProjectRecord]) -> None:
        self.records = records
        self.calls = 0

    async def fetch_records(self, timestamp: int = 0) -> list[ProjectRecord]:
        self.calls += 1
        return list(self.records)


def build_service(tmp_path: Path, api_base: str = "") -> ProjectService:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DEEPSEEK_API_KEY="test-key",
        # 用别名传参：这些字段声明了 validation_alias 且模型未开启 populate_by_name，
        # 字段名形式的 kwargs 会被静默忽略（extra=ignore），配置就漏成本机 .env 的值
        DOCMIND_PORT_API_BASE="",
        DOCMIND_PROJECT_API_BASE=api_base,
    )
    return ProjectService(settings, FileStore(settings))


def record(
    project_id: str,
    fid: str,
    name: str = "项目简称",
    code: str = "",
    status: int = 1,
    comxz: str = "1",
    customxz: int = 0,
) -> ProjectRecord:
    return ProjectRecord(
        id=project_id,
        fid=fid,
        full_name=f"{name}-全称",
        usr_name=name,
        usr_code=code,
        usr_status=status,
        usr_status_cw=1,
        comxz=comxz,
        customxz=customxz,
    )


def make_records() -> list[ProjectRecord]:
    return [
        record("1", "100", name="空运项目", code="A001"),
        record("2", "100", name="海运项目", code="B002"),
        # 无效：不进候选
        record("3", "100", name="停用项目", status=0),
        # comxz 不含 1：不是客户项目
        record("4", "100", name="供应商项目", comxz="2"),
        # 不参与新业务
        record("5", "100", name="历史项目", customxz=2),
        # 别的委托客户的项目：按 fid 收敛时不应出现
        record("6", "200", name="空运项目", code="A001"),
    ]


async def test_lists_only_available_projects_of_the_customer(tmp_path: Path) -> None:
    """候选 = 该委托客户名下、且通过可用性口径的项目。"""

    service = build_service(tmp_path, api_base="http://example.invalid/PublicWebApi/")
    service.collector = FakeCollector(make_records())

    items = await service.list_by_customer("100")

    assert [item.id for item in items] == ["1", "2"]
    assert items[0].name == "空运项目"
    assert items[0].code == "A001"


async def test_keyword_filters_by_name_or_code(tmp_path: Path) -> None:
    service = build_service(tmp_path, api_base="http://example.invalid/PublicWebApi/")
    service.collector = FakeCollector(make_records())

    assert [item.id for item in await service.list_by_customer("100", "海运")] == ["2"]
    assert [item.id for item in await service.list_by_customer("100", "b002")] == ["2"]
    assert await service.list_by_customer("100", "不存在的项目") == []


async def test_records_are_fetched_once_and_reused(tmp_path: Path) -> None:
    """同一进程内项目主数据只拉一次（内存索引），后续请求不再打接口。"""

    service = build_service(tmp_path, api_base="http://example.invalid/PublicWebApi/")
    collector = FakeCollector(make_records())
    service.collector = collector

    await service.list_by_customer("100")
    await service.list_by_customer("200")

    assert collector.calls == 1


async def test_blank_fid_returns_empty(tmp_path: Path) -> None:
    """没有委托客户就没有项目候选，也不触发主数据拉取。"""

    service = build_service(tmp_path, api_base="http://example.invalid/PublicWebApi/")
    collector = FakeCollector(make_records())
    service.collector = collector

    assert await service.list_by_customer("") == []
    assert collector.calls == 0


async def test_disabled_without_api_base(tmp_path: Path) -> None:
    """未配置主数据接口时整体停用，返回空列表。"""

    service = build_service(tmp_path)

    assert service.enabled is False
    assert await service.list_by_customer("100") == []


def test_merge_keeps_records_sharing_an_id_across_customers(tmp_path: Path) -> None:
    """id 不全局唯一：同一 id 挂在不同客户下时，合并不能互相覆盖。"""

    service = build_service(tmp_path)

    merged = service._merge(
        None,
        [record("2729", "2729", name="基础"), record("2729", "2828", name="基础")],
    )

    assert len(merged.records) == 2
    assert sorted(record.fid for record in merged.records) == ["2729", "2828"]


def test_merge_replaces_same_customer_same_id(tmp_path: Path) -> None:
    """同一客户的同 id 记录：增量更新按新值覆盖，而不是多出一条。"""

    service = build_service(tmp_path)
    first = service._merge(None, [record("2729", "2729", name="基础")])

    second = service._merge(first, [record("2729", "2729", name="基础-改名")])

    assert len(second.records) == 1
    assert second.records[0].usr_name == "基础-改名"
