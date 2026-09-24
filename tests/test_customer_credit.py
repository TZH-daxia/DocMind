"""信用等级与信控提示：等级取自客户主数据，提示取自 PubCredit。"""

from pathlib import Path

from app.collector.customer_reference_index import CustomerReferenceIndex
from app.config import Settings
from app.schemas.customer import CustomerRecord
from app.service.customer_service import CustomerService
from app.storage.file_store import FileStore


class FakeCreditCollector:
    """替身信控收集器：返回固定结果或抛错，避免测试打真实接口。"""

    def __init__(self, payload: dict | None = None, error: Exception | None = None):
        self.payload = payload or {}
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    async def fetch_credit(self, fid: str, area: str = "", system: str = "") -> dict:
        self.calls.append((fid, area, system))
        if self.error is not None:
            raise self.error
        return self.payload


async def test_credit_query_passes_system(tmp_path: Path) -> None:
    """信控按 fid + 站点 + 业务系统 三个维度查（与 poOrder 的 PubCredit 调用一致）。"""

    service = build_service(tmp_path)
    service._index = make_index()  # type: ignore[assignment]
    collector = FakeCreditCollector({"resultstatus": 0})
    service.credit_collector = collector

    await service.credit_hint("12794", "上海", "空出")

    assert collector.calls == [("12794", "上海", "空出")]


def build_service(tmp_path: Path) -> CustomerService:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DEEPSEEK_API_KEY="test-key",
        # 用别名传参：这些字段声明了 validation_alias 且模型未开启 populate_by_name，
        # 字段名形式的 kwargs 会被静默忽略（extra=ignore），配置就漏成本机 .env 的值
        DOCMIND_PORT_API_BASE="",
        DOCMIND_CUSTOMER_API_BASE="http://example.invalid/PublicWebApi/",
    )
    return CustomerService(settings, FileStore(settings))


def make_index() -> CustomerReferenceIndex:
    return CustomerReferenceIndex(
        [
            CustomerRecord(id="1719", usr_name="德迅（中国）货运代理有限公司", creditlevel="A"),
            CustomerRecord(id="12794", usr_name="上海奥南国际物流有限公司宁波分公司", creditlevel="C"),
        ]
    )


async def test_passed_credit_shows_level_only(tmp_path: Path) -> None:
    """resultstatus == 0（通过）：只显示等级，如「A类」。"""

    service = build_service(tmp_path)
    service._index = make_index()  # type: ignore[assignment]
    service.credit_collector = FakeCreditCollector(
        {"resultstatus": 0, "resultmessage": "不该显示"}
    )

    outcome = await service.credit_hint("1719", "上海")

    assert outcome.enabled is True
    assert outcome.level == "A"
    assert outcome.message == ""
    assert outcome.hint == "A类"


async def test_restricted_credit_appends_message(tmp_path: Path) -> None:
    """有信控限制：等级 + 提示原文拼成一行（口径同 poOrder）。"""

    service = build_service(tmp_path)
    service._index = make_index()  # type: ignore[assignment]
    service.credit_collector = FakeCreditCollector(
        {
            "resultstatus": 999,
            "resultmessage": "该客户是C类客户,需付款买单才能继续操作",
            "resultdic": {"creditlevel": "C"},
        }
    )

    outcome = await service.credit_hint("12794", "上海")

    assert outcome.level == "C"
    assert outcome.hint == "C类, 该客户是C类客户,需付款买单才能继续操作"


async def test_credit_failure_degrades_to_level(tmp_path: Path) -> None:
    """信控接口报错时降级为「只显示等级」，不阻断填写。"""

    service = build_service(tmp_path)
    service._index = make_index()  # type: ignore[assignment]
    service.credit_collector = FakeCreditCollector(error=RuntimeError("boom"))

    outcome = await service.credit_hint("12794", "上海")

    assert outcome.enabled is True
    assert outcome.hint == "C类"


async def test_blank_customer_returns_empty(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    collector = FakeCreditCollector({"resultstatus": 0})
    service.credit_collector = collector

    outcome = await service.credit_hint("", "上海")

    assert outcome.hint == ""
    assert collector.calls == []
