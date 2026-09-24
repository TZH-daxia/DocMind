"""唯凯站点字典服务：分组口径、缓存复用与未配置时的降级。"""

from pathlib import Path

from app.config import Settings
from app.schemas.site import SiteRecord
from app.service.site_service import SiteService
from app.storage.file_store import FileStore


class FakeCollector:
    """替身采集器：记录调用次数并返回固定字典，避免测试打真实接口。"""

    def __init__(self, records: list[SiteRecord]) -> None:
        self.records = records
        self.calls = 0

    async def fetch_records(self) -> list[SiteRecord]:
        self.calls += 1
        return list(self.records)


def build_service(tmp_path: Path, api_base: str = "") -> SiteService:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DEEPSEEK_API_KEY="test-key",
        # 用别名传参：这些字段声明了 validation_alias 且模型未开启 populate_by_name，
        # 字段名形式的 kwargs 会被静默忽略（extra=ignore），配置就漏成了本机 .env 的值
        DOCMIND_PORT_API_BASE="",
        DOCMIND_SITE_API_BASE=api_base,
    )
    return SiteService(settings, FileStore(settings))


def make_records() -> list[SiteRecord]:
    return [
        SiteRecord(name="上海", code="SHA", full_name="上海丨SHA", group="出口部"),
        SiteRecord(name="宁波", code="NGB", full_name="宁波丨NGB", group="出口部"),
        SiteRecord(name="北京", code="PEK", full_name="北京丨PEK", group="进口部"),
    ]


async def test_groups_follow_dictionary_order_and_keep_full_label(
    tmp_path: Path,
) -> None:
    """分组按字典出现顺序聚合；选项 value 是站点中文名、label 是字典原文。"""

    service = build_service(tmp_path, api_base="http://example.invalid/PublicWebApi/")
    service.collector = FakeCollector(make_records())

    groups = await service.list_groups()

    assert [group.label for group in groups] == ["出口部", "进口部"]
    assert [option.value for option in groups[0].options] == ["上海", "宁波"]
    assert groups[0].options[0].label == "上海丨SHA"


async def test_records_are_fetched_once_and_reused(tmp_path: Path) -> None:
    """同一进程内字典只拉一次（内存缓存），后续请求不再打接口。"""

    service = build_service(tmp_path, api_base="http://example.invalid/PublicWebApi/")
    collector = FakeCollector(make_records())
    service.collector = collector

    await service.list_groups()
    await service.list_groups()

    assert collector.calls == 1


async def test_disabled_without_api_base(tmp_path: Path) -> None:
    """未配置字典接口时整体停用，返回空列表（前端退化为不做候选）。"""

    service = build_service(tmp_path)

    assert service.enabled is False
    assert await service.list_groups() == []
