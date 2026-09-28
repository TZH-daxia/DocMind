"""用户默认设置（poOrder 用户设置模板 type=110）的取值口径。

口径来自 poOrder：`newOrderAdd.vue` 的 `otherInitData()` 用 `mawbAddArea` 填站点、
`mawbAddSystem.opersystem/opersystemdom` 填运输种类/服务方式；记录来自
`api/UserTemplet`（本测试用替身 collector，不真的打接口）。
"""

import json
from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.api.dependencies import get_analysis_service
from app.config import Settings
from app.service.user_default_service import UserDefaultService

PORT_BASE = "http://poorder.example/PublicWebApi/"


def build_settings(monkeypatch: Any, **env: str) -> Settings:
    """按环境变量构配置；_env_file=None 保证不受本机 .env 影响。"""

    monkeypatch.setenv("DOCMIND_PORT_API_BASE", PORT_BASE)
    monkeypatch.setenv("DOCMIND_USER_DEFAULTS_CACHE_TTL_MINUTES", env.pop("ttl", "10"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


class FakeCollector:
    """替身采集器：记录调用参数，返回预置记录。"""

    def __init__(self, records: list[dict[str, Any]] | None = None, error: Exception | None = None):
        self.records = records or []
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    async def fetch_records(
        self, logname: str, project: str = "bomanagement", ticket: str = ""
    ) -> list[dict[str, Any]]:
        self.calls.append((logname, project, ticket))
        if self.error is not None:
            raise self.error
        return self.records


def setting_record(jsondata: Any, isactivate: int = 1) -> dict[str, Any]:
    return {
        "type": 110,
        "isactivate": isactivate,
        "name": "userSetting",
        "url": "all",
        "jsondata": jsondata if isinstance(jsondata, str) else json.dumps(jsondata),
    }


ACTIVE_JSON = {
    "mawbAddArea": "上海",
    "mawbAddSystem": {"opersystem": "出口", "opersystemdom": "空运"},
    "mawbAddService": "",
}
INACTIVE_JSON = {
    "mawbAddArea": "",
    "mawbAddSystem": {"opersystem": "", "opersystemdom": ""},
    "mawbAddService": "",
}
# 同一账号下真实存在的干扰项：其它类型模板不该被当成用户设置
NOISE = [
    {"type": 100, "isactivate": 1, "jsondata": json.dumps(["czlx"])},
    {"type": 80, "isactivate": 1, "jsondata": json.dumps([{"servicecode": "OA0010"}])},
]


async def test_reads_defaults_from_active_user_setting(monkeypatch: Any) -> None:
    service = UserDefaultService(build_settings(monkeypatch))
    service.collector = FakeCollector([*NOISE, setting_record(ACTIVE_JSON)])

    defaults = await service.get_defaults("admin", ticket="TICKET-1")

    assert defaults.enabled is True
    assert defaults.logname == "admin"
    assert defaults.area == "上海"
    assert defaults.opersystem == "出口"
    assert defaults.opersystemdom == "空运"
    # 票据透传给 poOrder，项目固定 bomanagement（与 poOrder 前端一致）
    assert service.collector.calls == [("admin", "bomanagement", "TICKET-1")]


async def test_prefers_active_record_over_disabled_one(monkeypatch: Any) -> None:
    """停用（isactivate=2）的记录不能当默认值。

    实测 admin 账号就是两条：启用那条在前、停用那条在后；poOrder 自己用
    `find(i => i.type == 110)` 只看顺序，顺序一变就会取到空设置 —— 这里显式优先启用项。
    """

    service = UserDefaultService(build_settings(monkeypatch))
    service.collector = FakeCollector(
        [setting_record(INACTIVE_JSON, isactivate=2), setting_record(ACTIVE_JSON, isactivate=1)]
    )

    defaults = await service.get_defaults("admin")

    assert (defaults.area, defaults.opersystemdom, defaults.opersystem) == ("上海", "空运", "出口")


async def test_disabled_record_used_as_last_resort(monkeypatch: Any) -> None:
    """只有停用记录时仍取它（与 poOrder 现状一致），不返回空。"""

    service = UserDefaultService(build_settings(monkeypatch))
    service.collector = FakeCollector(
        [setting_record({"mawbAddArea": "宁波", "mawbAddSystem": {"opersystem": "出口", "opersystemdom": "海运"}}, isactivate=2)]
    )

    defaults = await service.get_defaults("admin")

    assert defaults.area == "宁波"
    assert defaults.opersystemdom == "海运"


async def test_domestic_mode_is_cleared(monkeypatch: Any) -> None:
    """服务方式存成「国内」时按 poOrder 口径置空（newOrderAdd.vue:3201）。"""

    service = UserDefaultService(build_settings(monkeypatch))
    service.collector = FakeCollector(
        [setting_record({"mawbAddArea": "上海", "mawbAddSystem": {"opersystem": "国内", "opersystemdom": "国内"}})]
    )

    defaults = await service.get_defaults("admin")

    assert defaults.opersystem == "国内"
    assert defaults.opersystemdom == ""


async def test_missing_setting_and_broken_jsondata(monkeypatch: Any) -> None:
    service = UserDefaultService(build_settings(monkeypatch))
    service.collector = FakeCollector([*NOISE])
    defaults = await service.get_defaults("admin")
    assert (defaults.enabled, defaults.area, defaults.opersystemdom) == (True, "", "")

    service.collector = FakeCollector([setting_record("{不是合法 JSON")])
    defaults = await service.get_defaults("admin")
    assert (defaults.area, defaults.opersystemdom) == ("", "")


async def test_cache_reuses_result_and_ttl_zero_refetches(monkeypatch: Any) -> None:
    settings = build_settings(monkeypatch)
    service = UserDefaultService(settings)
    collector = FakeCollector([setting_record(ACTIVE_JSON)])
    service.collector = collector

    await service.get_defaults("admin")
    await service.get_defaults("admin")
    assert len(collector.calls) == 1  # 缓存命中，不再打接口（接口一次 ~660KB）

    # TTL=0：每次都重拉
    settings.user_defaults_cache_ttl_minutes = 0
    await service.get_defaults("admin")
    assert len(collector.calls) == 2


async def test_failure_falls_back_to_cache_then_empty(monkeypatch: Any) -> None:
    service = UserDefaultService(build_settings(monkeypatch))
    service.collector = FakeCollector([setting_record(ACTIVE_JSON)])
    await service.get_defaults("admin")

    # 有旧缓存：主数据抖动时沿用旧值，不让工具条退回内置默认
    service.collector = FakeCollector(error=RuntimeError("boom"))
    service._cache["admin"] = (
        datetime.now().astimezone() - timedelta(minutes=99),  # 已过期
        UserDefaultService._parse("admin", [setting_record(ACTIVE_JSON)]),
    )
    defaults = await service.get_defaults("admin")
    assert defaults.area == "上海"

    # 没缓存：返回空值（enabled 仍为 True，前端退内置兜底），不抛异常
    service._cache.clear()
    defaults = await service.get_defaults("admin")
    assert (defaults.enabled, defaults.area) == (True, "")


async def test_blank_logname_skips_request(monkeypatch: Any) -> None:
    service = UserDefaultService(build_settings(monkeypatch))
    collector = FakeCollector([setting_record(ACTIVE_JSON)])
    service.collector = collector

    defaults = await service.get_defaults("   ")

    assert collector.calls == []
    assert (defaults.enabled, defaults.area) == (True, "")


async def test_disabled_without_api_base(monkeypatch: Any) -> None:
    monkeypatch.delenv("DOCMIND_PORT_API_BASE", raising=False)
    monkeypatch.delenv("DOCMIND_USER_TEMPLET_API_BASE", raising=False)
    settings = Settings(_env_file=None)

    service = UserDefaultService(settings)
    defaults = await service.get_defaults("admin")

    assert service.enabled is False
    assert defaults.enabled is False


def test_user_defaults_route_passes_logname_and_ticket() -> None:
    """接线验证：路由把 logname 与请求头里的票据交给服务层。"""

    from app.main import app

    captured: dict[str, Any] = {}

    class FakeService:
        async def get_user_defaults(self, logname: str, ticket: str = "") -> dict[str, Any]:
            captured.update({"logname": logname, "ticket": ticket})
            return {"enabled": True, "logname": logname, "area": "上海"}

    app.dependency_overrides[get_analysis_service] = FakeService
    try:
        response = TestClient(app).get(
            "/docmind/analysis/user-defaults?logname=admin",
            headers={"Authorization": "HDR-TICKET"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["area"] == "上海"
    assert captured == {"logname": "admin", "ticket": "HDR-TICKET"}
