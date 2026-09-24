"""本票客户客服联系人：有效过滤 + 默认联系人判定（口径同 poOrder）。"""

from pathlib import Path

from app.config import Settings
from app.service.customer_contact_service import (
    CustomerContactService,
    is_default_contact,
)


class FakeContactCollector:
    """替身联系人收集器：返回固定记录或抛错，避免测试打真实接口。"""

    def __init__(self, records: list | None = None, error: Exception | None = None):
        self.records = records or []
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def fetch_contacts(self, fid: str, contact_type: str = "客服"):
        self.calls.append((fid, contact_type))
        if self.error is not None:
            raise self.error
        return self.records


def build_service(tmp_path: Path) -> CustomerContactService:
    return CustomerContactService(
        Settings(
            DOCMIND_DATA_ROOT=tmp_path,
            DEEPSEEK_API_KEY="test-key",
            # 用别名传参：这些字段声明了 validation_alias，字段名形式的 kwargs 会被忽略
            DOCMIND_PORT_API_BASE="http://example.invalid/PublicWebApi/",
        )
    )


def test_default_contact_rules() -> None:
    """默认联系人 = defaultlxrjson 里同时匹配站点与业务系统（-1 为通配）。"""

    record = {"defaultlxrjson": '[{"area": "上海", "system": "空出"}]'}
    assert is_default_contact(record, "上海", "空出") is True
    assert is_default_contact(record, "上海", "海进") is False
    assert is_default_contact(record, "深圳", "空出") is False

    wildcard = {"defaultlxrjson": '[{"area": "上海", "system": "-1"}]'}
    assert is_default_contact(wildcard, "上海", "海进") is True

    # 没有 / 坏掉的 defaultlxrjson 都不算默认
    assert is_default_contact({}, "上海", "空出") is False
    assert is_default_contact({"defaultlxrjson": "not-json"}, "上海", "空出") is False
    assert is_default_contact({"defaultlxrjson": '{"area": "上海"}'}, "上海", "空出") is False


async def test_list_contacts_filters_and_flags(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    service.collector = FakeContactCollector(
        [
            {
                "name": "张小三",
                "mobile": "13800000000",
                "phone": "021-1",
                "email": "a@b.c",
                "comxz": "1",
                "defaultlxrjson": '[{"area": "上海", "system": "空出"}]',
            },
            # comxz != 1：已停用，必须被过滤掉
            {"name": "已停用", "comxz": "2"},
            {"name": "李四", "mobile": "13900000000", "phone": "", "email": "", "comxz": "1"},
        ]
    )

    payload = await service.list_contacts("12794", "上海", "空出")

    assert payload["enabled"] is True
    assert [item["name"] for item in payload["items"]] == ["张小三", "李四"]
    assert payload["items"][0]["is_default"] is True
    assert payload["items"][1]["is_default"] is False
    assert payload["items"][0]["mobile"] == "13800000000"
    # 查询用的是「客服」类型（与报文里固定的 post/lxrtitle/department 一致）
    assert service.collector.calls == [("12794", "客服")]


async def test_list_contacts_degrades(tmp_path: Path) -> None:
    """接口报错或未配置时不阻断填写：enabled 有值、items 为空。"""

    service = build_service(tmp_path)
    service.collector = FakeContactCollector(error=RuntimeError("boom"))

    payload = await service.list_contacts("12794", "上海", "空出")

    assert payload["enabled"] is True
    assert payload["items"] == []

    service.collector = None
    payload = await service.list_contacts("12794")

    assert payload["enabled"] is False
    assert payload["items"] == []
