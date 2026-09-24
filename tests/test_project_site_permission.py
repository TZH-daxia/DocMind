"""项目候选的站点/系统权限：候选口径与判定依据（同 poOrder）。"""
from pathlib import Path

from app.config import Settings
from app.schemas.project import ProjectRecord
from app.service.project_service import ProjectService
from app.storage.file_store import FileStore


def build_service(tmp_path: Path) -> ProjectService:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DEEPSEEK_API_KEY="test-key",
        # 用别名传参：这些字段声明了 validation_alias，字段名形式的 kwargs 会被忽略
        DOCMIND_PORT_API_BASE="http://example.invalid/PublicWebApi/",
    )
    return ProjectService(settings, FileStore(settings))


RECORDS = [
    # 允许上海、南京两个站点；只允许「空出」
    ProjectRecord(
        id="1",
        fid="1719",
        usr_name="上海项目",
        area="上海,南京",
        system="101",
        usr_status=1,
        comxz="1",
    ),
    # 站点与系统都不限
    ProjectRecord(
        id="2",
        fid="1719",
        usr_name="通用项目",
        area="-1",
        system="-1",
        usr_status=1,
        comxz="1",
    ),
    # 只允许成都
    ProjectRecord(
        id="3",
        fid="1719",
        usr_name="成都项目",
        area="成都",
        system="102",
        usr_status=1,
        comxz="1",
    ),
]

# groupid == 57 的字典：id → 系统名
SYSTEM_NAMES = {"101": "空出", "102": "海进"}


async def test_list_by_customer_keeps_projects_of_every_site(tmp_path: Path) -> None:
    """候选**不按站点过滤**：站点权限留到前端选中之后判定（与 poOrder 一致）。

    这一点必须成立——候选一旦按站点过滤，没有该站点权限的项目会从列表里消失，
    前端「该项目没有X站点权限！」的判定就永远触发不到，站点也就不会被清空。
    """

    service = build_service(tmp_path)
    service._by_customer = service._build_index(RECORDS, SYSTEM_NAMES)  # type: ignore[assignment]

    items = await service.list_by_customer("1719")

    # 三个项目全部返回，与站点无关
    assert [item.id for item in items] == ["1", "2", "3"]
    # 每个候选都带着自己的站点/系统权限，供前端判定
    assert items[0].area == "上海,南京"
    assert items[0].systems == ["空出"]
    assert items[1].area == "-1"
    assert items[1].systems == ["-1"]
    assert items[2].area == "成都"
    assert items[2].systems == ["海进"]

    # 关键字过滤照旧（站点不再是这个方法的维度）
    assert [item.id for item in await service.list_by_customer("1719", "成都")] == ["3"]


def test_resolve_systems(tmp_path: Path) -> None:
    """系统 id 串 → 系统名；`-1` 或字典不可用时视为不限。"""

    assert ProjectService._resolve_systems("-1", SYSTEM_NAMES) == ["-1"]
    assert ProjectService._resolve_systems("101,102", SYSTEM_NAMES) == ["空出", "海进"]
    # 字典里没有的 id 原样保留，便于排查数据问题
    assert ProjectService._resolve_systems("999", SYSTEM_NAMES) == ["999"]
    # 字典拿不到 / 字段为空：按不限处理，避免因主数据缺字段把操作员全拦死
    assert ProjectService._resolve_systems("101", {}) == ["-1"]
    assert ProjectService._resolve_systems("", SYSTEM_NAMES) == ["-1"]
