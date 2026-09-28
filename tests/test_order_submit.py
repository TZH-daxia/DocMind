"""提交订单：报文组装逐字段断言 + 结果解析 + 缺操作人/调用失败的降级。"""

from pathlib import Path

from app.config import Settings
from app.service.order_submit_service import (
    OrderSubmitService,
    build_submit_payload,
    compute_system,
    parse_submit_result,
)


class FakeSubmitCollector:
    """替身提交器：记录调用并返回固定结果，避免测试真的下单。"""

    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[tuple[dict, str]] = []

    async def submit_order(self, payload: dict, ticket: str = ""):
        self.calls.append((payload, ticket))
        if self.error is not None:
            raise self.error
        return self.payload


FORM = {
    "fid": "12794",
    "gid": "55",
    "sfg": "PVG",
    "mdg": "FRA",
    "ybpiece": "12",
    "ybweight": "168.5",
    "ybvolume": "0.78",
    "hbrq": "2026-09-06",
    "chinesepm": "轴承配件",
    "englishpm": "BEARING PARTS",
    "khjcno": "KH20260906",
    "ybvolumeremark": "120x80x60CM",
    "shipper": {
        "name": "Cleva International Trading Limited",
        "address": "ROOM 1, HONGKONG",
        "phone": "13800000000",
        "email": "shipper@example.com",
    },
    "consignee": {
        "name": "Pace GmbH",
        "address": "FRANKFURT, GERMANY",
        "phone": "0049",
        "email": "consignee@example.com",
    },
}

ORDER = {"area": "上海", "opersystem": "出口", "opersystemdom": "空运", "czlx": "自货"}


def test_system_derivation() -> None:
    """system = 运输种类首位 + 服务方式首位；「其它」「国内」退化为国内服务。"""

    assert compute_system("出口", "空运") == "空出"
    assert compute_system("出口", "海运") == "海出"
    assert compute_system("进口", "空运") == "空进"
    assert compute_system("国内", "空运") == "国内服务"
    assert compute_system("出口", "其它") == "国内服务"


def test_payload_maps_form_and_fixed_values() -> None:
    payload = build_submit_payload(FORM, ORDER, "zhangsan", today="2026-09-24")

    # 取工具条与表单的值
    assert payload["czlx"] == "自货"
    assert payload["fid"] == "12794"
    assert payload["gid"] == "55"
    assert payload["area"] == "上海"
    assert payload["opersystem"] == "出口"
    assert payload["opersystemdom"] == "空运"
    assert payload["system"] == "空出"
    assert payload["sfg"] == "PVG"
    assert payload["mdg"] == "FRA"
    assert payload["ybpiece"] == "12"
    assert payload["ybweight"] == "168.5"
    assert payload["ybvolume"] == "0.78"
    assert payload["hbrq"] == "2026-09-06"
    assert payload["chinesepm"] == "轴承配件"
    assert payload["englishpm"] == "BEARING PARTS"
    # 发货人 / 收货人拆成四个报文字段
    assert payload["companytitle_fhr_mawb"] == "Cleva International Trading Limited"
    assert payload["address_fhr_mawb"] == "ROOM 1, HONGKONG"
    assert payload["email_fhr_mawb"] == "shipper@example.com"
    assert payload["phone_fhr_mawb"] == "13800000000"
    assert payload["companytitle_shr_mawb"] == "Pace GmbH"
    assert payload["address_shr_mawb"] == "FRANKFURT, GERMANY"
    assert payload["email_shr_mawb"] == "consignee@example.com"
    assert payload["phone_shr_mawb"] == "0049"
    # 固定值
    assert payload["orderdom"] == "总单"
    assert payload["czman"] == "zhangsan"
    assert payload["logExtraData"] == "小凯,上海"
    # 部门：poOrder 的列表查询默认按 dom 过滤，缺了会让新单在综合查询里查不到，
    # 因此报文必须带；缺省回落「出口部」，传入真实部门时以传入值为准
    assert payload["dom"] == "出口部"
    assert (
        build_submit_payload(FORM, {**ORDER, "dom": "进口部"}, "zhangsan")["dom"]
        == "进口部"
    )
    # 需求文档漏掉、后续需求要求加上的预报尺寸备注
    assert payload["ybvolumeremark"] == "120x80x60CM"
    # 应收运费相关（契约《点击提交订单按钮》）：单价取表单，其余写死。
    # 注意：早前一份需求要求"不进本接口"，这份契约又要回来了
    assert payload["currency"] == "人民币"
    assert payload["inwageallinclude"] == "4"
    assert payload["isinwageallin"] == "1"
    assert payload["inwageallinprice"] == ""  # FORM 里没填单价
    assert payload["self_real_bp_freight_in"] == 10
    assert payload["cus_real_bp_freight_in"] == 0
    assert payload["isinwageallin_trans"] == 666666
    assert payload["inwageallinprice_trans"] == 666666
    assert payload["self_real_bp_trans_in"] == 10
    assert payload["cus_real_bp_trans_in"] == 0
    # 传了单价就带上（字符串，与其它数字字段一致）
    priced = build_submit_payload({**FORM, "inwageallinprice": "168.5"}, ORDER, "zhangsan")
    assert priced["inwageallinprice"] == "168.5"
    # 前端派生字段仍不进报文
    assert "inwageallintotal" not in payload


def test_payload_keeps_blank_flight_number() -> None:
    """要求航班号（hbh）：报文里保留这个键、值恒为空串。

    前端已按需求去掉这个输入框，表单不再提交 hbh；契约里这个字段仍在，
    所以这里只保证「键在、值为空」，也不再参与前端必填校验。
    """

    assert build_submit_payload(FORM, ORDER, "zhangsan")["hbh"] == ""


def test_service_list_follows_panel_selection() -> None:
    """serviceList 由服务项目面板的勾选决定：顺序、去重、空选择都按前端给的来。"""

    item = {
        "requestcode": "",
        "oprequest": "",
        "assignstatus": "0",
        "isdel": "1",
    }
    picked = build_submit_payload(
        FORM, ORDER, "zhangsan", service_codes=["AA0110", "OA0010", "AG0145"]
    )

    assert picked["serviceList"] == [
        {"servicecode": "AA0110", **item},
        {"servicecode": "OA0010", **item},
        {"servicecode": "AG0145", **item},
    ]

    # 不传 = 默认唯凯配舱（与面板的默认勾选一致）
    assert [
        entry["servicecode"]
        for entry in build_submit_payload(FORM, ORDER, "zhangsan")["serviceList"]
    ] == ["OA0010"]

    # 空列表 = 一项服务都不做
    assert (
        build_submit_payload(FORM, ORDER, "zhangsan", service_codes=[])[
            "serviceList"
        ]
        == []
    )

    # 重复的服务代码只保留第一次出现，空值丢掉
    assert [
        entry["servicecode"]
        for entry in build_submit_payload(
            FORM, ORDER, "zhangsan", service_codes=["AA0110", "", "AA0110", "AG0145"]
        )["serviceList"]
    ] == ["AA0110", "AG0145"]


def test_service_list_drops_booking_service_for_home_business() -> None:
    """「国内服务」业务不带 OA0010（poOrder 保存报文时会 continue 掉它）。"""

    home_order = {
        "area": "上海",
        "opersystem": "国内",
        "opersystemdom": "空运",
        "czlx": "自货",
    }
    payload = build_submit_payload(
        FORM, home_order, "zhangsan", service_codes=["OA0010", "AA0110"]
    )

    assert payload["system"] == "国内服务"
    assert [entry["servicecode"] for entry in payload["serviceList"]] == ["AA0110"]


def test_payload_nested_lists() -> None:
    payload = build_submit_payload(FORM, ORDER, "zhangsan", today="2026-09-24")

    contact = payload["customerRelList"][0]
    assert contact["addman"] == "zhangsan"
    assert contact["adddate"] == "2026-09-24"
    assert contact["comxz"] == "1"
    assert contact["defaultlxr"] is True
    assert contact["department"] == "客服"
    assert contact["lxrss"] == "2"
    assert contact["lxrtitle"] == "客服"
    assert contact["post"] == "客服"
    assert contact["area"] == "" and contact["email"] == "" and contact["qq"] == ""
    # 需求明确要求不带这两项（契约样例里有 id=-1 / system=380，按需求去掉）
    assert "id" not in contact
    assert "system" not in contact
    # 客服联系人主数据未接入：姓名/手机/电话先为空
    assert contact["name"] == "" and contact["mobile"] == "" and contact["phone"] == ""

    assert payload["serviceList"] == [
        {
            "servicecode": "OA0010",
            "requestcode": "",
            "oprequest": "",
            "assignstatus": "0",
            "isdel": "1",
        }
    ]

    store = payload["ybstoreList"][0]
    assert store["khjcno"] == "KH20260906"
    assert store["piece"] == "12"
    assert store["weight"] == "168.5"
    assert store["volume"] == "0.78"
    assert store["ybstorevolumeList"] == []


def test_contact_uses_form_values_when_present() -> None:
    form = {
        **FORM,
        "customerRelList": [{"name": "客服小李", "mobile": "13900000000", "phone": "021-1"}],
    }

    contact = build_submit_payload(form, ORDER, "zhangsan", today="2026-09-24")[
        "customerRelList"
    ][0]

    assert contact["name"] == "客服小李"
    assert contact["mobile"] == "13900000000"
    assert contact["phone"] == "021-1"


def test_parse_submit_result() -> None:
    # 实测形态一：创建成功、有待办（信控受限），文案里有编号
    ok, code, message = parse_submit_result(
        {
            "resultstatus": 9999,
            "resultmessage": (
                "订单创建成功并锁定,该客户是C类客户,需付款买单才能继续操作"
                "请等待上级审批,订单编号为:BOAE2609240001PVG"
            ),
            "resultno": "BOAE2609240001PVG",
        }
    )
    assert ok is True
    assert code == "BOAE2609240001PVG"
    assert "订单创建成功并锁定" in message

    # 实测形态二：创建成功、文案里根本没有编号，只能从 resultno 取
    ok, code, message = parse_submit_result(
        {"resultstatus": 0, "resultmessage": "新增成功", "resultno": "BOAE2609240005PVG"}
    )
    assert ok is True
    assert code == "BOAE2609240005PVG"
    assert message == "新增成功"

    # 实测形态三：业务校验不通过，不能算成功、也不能误取编号
    ok, code, message = parse_submit_result(
        {"resultstatus": 1, "resultmessage": "航班日期不能小于创建日期", "resultno": None}
    )
    assert ok is False
    assert code == ""
    assert message == "航班日期不能小于创建日期"

    # 文档示例的措辞同样要认
    ok, code, _ = parse_submit_result(
        {"resultstatus": 0, "resultmessage": "新增成功，订舱编号BOAE202601010001"}
    )
    assert ok is True
    assert code == "BOAE202601010001"

    # resultstatus 不是 0，但文案明确说创建成功：仍算成功（否则会漏掉已建的单）
    ok, code, _ = parse_submit_result(
        {"resultstatus": 999, "resultmessage": "订单创建成功并锁定,订单编号为:BOAE2609240009PVG"}
    )
    assert ok is True
    assert code == "BOAE2609240009PVG"

    # 纯提示、没有建单的返回（如信控拦截）：失败，且不误取编号
    ok, code, message = parse_submit_result(
        {"resultstatus": 999, "resultmessage": "该客户是C类客户,需付款买单才能继续操作"}
    )
    assert ok is False
    assert code == ""
    assert message == "该客户是C类客户,需付款买单才能继续操作"

    # resultstatus 是字符串 "0" 也算成功
    assert parse_submit_result({"resultstatus": "0", "resultmessage": "新增成功"})[0] is True
    # 成功但文案里没有编号：不报错，编号留空
    assert parse_submit_result({"resultstatus": 0, "resultmessage": "新增成功，订舱编号：BOAE1"})[1] == ""
    assert parse_submit_result(None) == (False, "", "")


def build_service(tmp_path: Path) -> OrderSubmitService:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        DEEPSEEK_API_KEY="test-key",
        # 用别名传参：这些字段声明了 validation_alias，字段名形式的 kwargs 会被忽略
        DOCMIND_PORT_API_BASE="http://example.invalid/PublicWebApi/",
        # 真实下单默认关闭，测试里显式打开
        DOCMIND_ORDER_SUBMIT_ENABLED="true",
    )
    return OrderSubmitService(settings)


async def test_submit_disabled_by_default(tmp_path: Path) -> None:
    """没显式打开开关就不许下单：不做环境猜测，一律拦在调接口之前。"""

    service = OrderSubmitService(
        Settings(
            DOCMIND_DATA_ROOT=tmp_path,
            DEEPSEEK_API_KEY="test-key",
            DOCMIND_PORT_API_BASE="http://example.invalid/PublicWebApi/",
            # 显式关掉：本机 .env 里开关是打开的，不显式传就会被环境值覆盖
            DOCMIND_ORDER_SUBMIT_ENABLED="false",
        )
    )
    collector = FakeSubmitCollector({"resultstatus": 0})
    service.collector = collector

    outcome = await service.submit(FORM, ORDER, "zhangsan")

    assert outcome.ok is False
    # 提示里带上开关名，方便直接照着改 .env
    assert "DOCMIND_ORDER_SUBMIT_ENABLED" in outcome.message
    assert collector.calls == []


async def test_submit_success_returns_order_code(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    collector = FakeSubmitCollector(
        {"resultstatus": 0, "resultmessage": "新增成功，订舱编号BOAE202601010001"}
    )
    service.collector = collector

    outcome = await service.submit(FORM, ORDER, "zhangsan", ticket="TICKET-1")

    assert outcome.ok is True
    assert outcome.order_code == "BOAE202601010001"
    # 票据透传给 poOrder（该接口在 BoManagementWebApi 下，前端一律带 Authorization）
    assert collector.calls[0][1] == "TICKET-1"
    assert collector.calls[0][0]["czman"] == "zhangsan"
    # 实际发出的报文随结果回传，便于核对（浏览器网络面板 / 服务端日志）
    assert outcome.payload is not None
    assert outcome.payload["system"] == "空出"
    assert outcome.payload["ybstoreList"][0]["khjcno"] == "KH20260906"


async def test_submit_requires_operator(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    collector = FakeSubmitCollector({"resultstatus": 0})
    service.collector = collector

    outcome = await service.submit(FORM, ORDER, "  ")

    assert outcome.ok is False
    assert outcome.message == "无操作人数据，请重新登录"
    assert collector.calls == []


async def test_submit_call_failure(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    service.collector = FakeSubmitCollector(error=RuntimeError("boom"))

    outcome = await service.submit(FORM, ORDER, "zhangsan")

    assert outcome.ok is False
    assert outcome.message


def test_management_api_base_derivation(tmp_path: Path) -> None:
    """默认按 PublicWebApi → BoManagementWebApi 换应用名；配了专用项则以其为准。"""

    derived = OrderSubmitService(
        Settings(
            DOCMIND_DATA_ROOT=tmp_path,
            DEEPSEEK_API_KEY="k",
            DOCMIND_PORT_API_BASE="http://192.168.0.113/PublicWebApi/",
        )
    )
    assert derived.collector.api_base == "http://192.168.0.113/BoManagementWebApi/"

    explicit = OrderSubmitService(
        Settings(
            DOCMIND_DATA_ROOT=tmp_path,
            DEEPSEEK_API_KEY="k",
            DOCMIND_PORT_API_BASE="http://192.168.0.113/PublicWebApi/",
            DOCMIND_ORDER_API_BASE="http://192.168.0.113/BoWebApi/",
        )
    )
    assert explicit.collector.api_base == "http://192.168.0.113/BoWebApi/"
