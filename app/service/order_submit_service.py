"""提交订单：把弹窗里核对过的表单组装成 poOrder 的订单报文并提交。

报文契约来自需求文档《点击提交订单按钮》：接口 `api/ExHpoAxpline`，

- 中括号项取本项目表单/工具条的值；
- 其余按文档给定值原样传（本模块顶部的常量）；
- `system` 由服务方式与运输种类派生（见 `compute_system`）。

组装逻辑是纯函数（`build_submit_payload`），便于逐字段断言。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from typing import Any

from app.collector.order_submit_collector import OrderSubmitCollector
from app.config import Settings
from app.schemas.submit import OrderSubmitOutcome

logger = logging.getLogger(__name__)

# 报文固定值：需求文档给定，不随表单变化
FIXED_ORDERDOM = "总单"
FIXED_LOG_EXTRA_DATA = "小凯,上海"
# 配舱服务的服务代码（唯凯配舱 / 唯凯代操作共用它，两者靠 czlx 区分）
FIXED_SERVICE_CODE = "OA0010"
# compute_system 的「国内服务」退化值：该业务不带 OA0010（见 build_submit_payload）
HOME_SYSTEM = "国内服务"


def text_of(value: Any) -> str:
    """表单值转报文值：None → 空串，其余去掉首尾空白。"""

    return "" if value is None else str(value).strip()


def compute_system(opersystem: str, opersystemdom: str) -> str:
    """报文的 `system`：运输种类首位 + 服务方式首位；特殊情形退化为「国内服务」。

    值域按 poOrder 前端命名（本项目工具条同步）：`opersystem` 是运输种类
    （出口/进口/国内），`opersystemdom` 是服务方式（空运/海运/陆运/铁运/其它）。
    于是「空运 + 出口」→「空出」，与 poOrder 自己的算法一致
    （`newOrderAdd.vue` 的 serviceList 传参：`opersystemdom.substr(0,1) + system.substr(0,1)`，
    其中 poOrder 的 `system` 就是运输种类的值）。

    需求文档写成「opersystem 首位 + opersystemdom 首位」，那是按文档自己的 key 命名
    描述的；本项目按 poOrder 命名传值，所以首位顺序相应对调，才是业务上正确的「空出」。
    「服务方式 = 其它」或「运输种类 = 国内」统一为「国内服务」（同 poOrder 既有判断）。
    """

    if opersystemdom == "其它" or opersystem == "国内":
        return "国内服务"
    return f"{opersystemdom[:1]}{opersystem[:1]}"


def party_text(form: dict[str, Any], key: str, sub_key: str) -> str:
    """取发货人/收货人的某个子项（name/address/phone/email）。"""

    party = form.get(key)
    if not isinstance(party, dict):
        return ""
    return text_of(party.get(sub_key))


def build_contact(
    form: dict[str, Any], czman: str, submit_date: str
) -> dict[str, Any]:
    """组成本票客户客服联系人（customerRelList 的第一项）。

    `mobile` / `name` / `phone` 取表单里已有的联系人（客服联系人接口接入前可能为空），
    其余按需求文档固定。
    """

    contacts = form.get("customerRelList")
    first = contacts[0] if isinstance(contacts, list) and contacts else {}
    if not isinstance(first, dict):
        first = {}
    return {
        "adddate": submit_date,
        "addman": czman,
        "area": "",
        # 刻意不带 id / system：需求明确要求去掉这两项
        # （poOrder 新增联系人时会带 id=-1 与 system=380，我们按需求不发）
        "comxz": "1",
        "defaultlxr": True,
        "defaultlxrjson": "",
        "department": "客服",
        "email": "",
        "lxrss": "2",
        "lxrtitle": "客服",
        "mobile": text_of(first.get("mobile")),
        "name": text_of(first.get("name")),
        "phone": text_of(first.get("phone")),
        "post": "客服",
        "qq": "",
    }


def build_service_list(codes: list[str] | None) -> list[dict[str, Any]]:
    """按勾选的服务代码生成报文的 serviceList。

    字段口径取自 poOrder（`src/components/orderDetails/mawbAddPutAi.vue:8428` 的注释
    「{servicecode:"服务code", requestcode:"要求code", oprequest:"服务要求",
    isdel:"1.选中 2.未选中"}」）：`isdel` 恒为 "1"（选中）、`requestcode` 恒为空串、
    `assignstatus` 固定 "0"（未分配）；`oprequest`（操作要求）面板暂未采集，先留空。

    顺序按调用方给的原样（前端已按面板顺序排好），重复的服务代码只保留第一次出现。
    """

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for raw in codes or []:
        code = text_of(raw)
        if not code or code in seen:
            continue
        seen.add(code)
        items.append(
            {
                "servicecode": code,
                "requestcode": "",
                "oprequest": "",
                "assignstatus": "0",
                "isdel": "1",
            }
        )
    return items


def build_submit_payload(
    form: dict[str, Any],
    order: dict[str, Any],
    czman: str,
    today: str | None = None,
    service_codes: list[str] | None = None,
) -> dict[str, Any]:
    """按需求文档组装提交报文。`today` 可注入，便于测试。

    `service_codes` 是服务项目面板勾选的服务代码（按面板顺序）：`None` 表示调用方
    没给，按默认的唯凯配舱（OA0010）处理；空列表表示一项服务都不做。
    """

    submit_date = today or date.today().isoformat()
    # 运输种类（出口/进口/国内）与服务方式（空运/海运/…）：命名沿用 poOrder 前端
    opersystem = text_of(order.get("opersystem"))
    opersystemdom = text_of(order.get("opersystemdom"))
    piece = text_of(form.get("ybpiece"))
    weight = text_of(form.get("ybweight"))
    volume = text_of(form.get("ybvolume"))
    system_text = compute_system(opersystem, opersystemdom)
    # 服务项目：None = 调用方没给（按默认唯凯配舱），给了就按给的来
    codes = [FIXED_SERVICE_CODE] if service_codes is None else list(service_codes)

    return {
        # 订舱操作：取工具条的值（需求确认：不是固定值）
        "czlx": text_of(order.get("czlx")),
        "fid": text_of(form.get("fid")),
        "gid": text_of(form.get("gid")),
        "area": text_of(order.get("area")),
        "opersystem": opersystem,
        "opersystemdom": opersystemdom,
        "orderdom": FIXED_ORDERDOM,
        "sfg": text_of(form.get("sfg")),
        "mdg": text_of(form.get("mdg")),
        "ybpiece": piece,
        "ybweight": weight,
        "ybvolume": volume,
        "system": system_text,
        # 要求航班号：托书里没有、由操作员手工填写（前端必填），有值就带上
        "hbh": text_of(form.get("hbh")),
        "hbrq": text_of(form.get("hbrq")),
        "englishpm": text_of(form.get("englishpm")),
        "chinesepm": text_of(form.get("chinesepm")),
        "companytitle_fhr_mawb": party_text(form, "shipper", "name"),
        "address_fhr_mawb": party_text(form, "shipper", "address"),
        "email_fhr_mawb": party_text(form, "shipper", "email"),
        "phone_fhr_mawb": party_text(form, "shipper", "phone"),
        "companytitle_shr_mawb": party_text(form, "consignee", "name"),
        "address_shr_mawb": party_text(form, "consignee", "address"),
        "email_shr_mawb": party_text(form, "consignee", "email"),
        "phone_shr_mawb": party_text(form, "consignee", "phone"),
        # 应收运费相关（契约《点击提交订单按钮》）：币种 / 是否含运费 / 费用包含方式，
        # 以及几个"不做特殊处理"的哨兵值都写死；单价取表单。类型照契约原文——带引号的
        # 是字符串、其余是数字（poOrder 前端 getInfo() 也是这么补默认值的：
        # `inwageallinprice = inwageallinprice || 666666`、`isinwageallin_trans || 666666`）
        "currency": "人民币",
        "inwageallinclude": "4",
        "isinwageallin": "1",
        "inwageallinprice": text_of(form.get("inwageallinprice")),
        "self_real_bp_freight_in": 10,
        "cus_real_bp_freight_in": 0,
        "isinwageallin_trans": 666666,
        "inwageallinprice_trans": 666666,
        "self_real_bp_trans_in": 10,
        "cus_real_bp_trans_in": 0,
        "customerRelList": [build_contact(form, czman, submit_date)],
        # 服务项目：按面板勾选生成（顺序同面板）。「国内服务」业务不带 OA0010 ——
        # poOrder 在 mawbAddPut.vue / mawbAddPutAi.vue 保存时会 continue 掉它
        "serviceList": build_service_list(
            [code for code in codes if code != FIXED_SERVICE_CODE]
            if system_text == HOME_SYSTEM
            else codes
        ),
        "ybstoreList": [
            {
                "khjcno": text_of(form.get("khjcno")),
                "piece": piece,
                "weight": weight,
                "volume": volume,
                "ybstorevolumeList": [],
            }
        ],
        # 需求文档漏了这一项，另一个需求要求加上（预报尺寸备注）
        "ybvolumeremark": text_of(form.get("ybvolumeremark")),
        "czman": czman,
        "logExtraData": FIXED_LOG_EXTRA_DATA,
        # 部门：poOrder 的 GET 列表查询会自动按 `where.dom = '出口部'` 过滤
        # （src/common/http.js:150-158），而它的前端每个非 GET 请求也会自动补这个字段
        # （同文件 88-90）。我们在服务端直调、绕过了那层拦截器，所以必须自己补，
        # 否则建出来的单在「综合查询」里按部门过滤时查不到。
        # 值优先取调用方传入的真实部门（poOrder 里是 localStorage.dom），缺省回落「出口部」
        "dom": text_of(order.get("dom")) or "出口部",
    }


# 订单编号形如 BOAE2609240001PVG / BOAE202601010001：前缀 + 日期 + 流水 + 始发港
ORDER_CODE_PATTERN = re.compile(r"[A-Z]{2,}[0-9]{6,}[A-Z]*")


def parse_submit_result(payload: Any) -> tuple[bool, str, str]:
    """解析提交结果 →（是否成功, 订单编号, 提示文案）。

    实测（见 logs/app.log）poOrder 的返回有几种形态，**订单编号都在 `resultno` 里**：

    - 创建成功、有待办：`{"resultstatus": 9999, "resultmessage": "订单创建成功并锁定,…订单编号为:BOAE…", "resultno": "BOAE…"}`
    - 创建成功、无待办：`{"resultstatus": 0, "resultmessage": "新增成功", "resultno": "BOAE…"}`
      —— 这种文案里**根本没有编号**，只能从 `resultno` 取（曾因此编号取不到）
    - 业务校验不通过：`{"resultstatus": 1, "resultmessage": "航班日期不能小于创建日期"}`

    所以：编号优先读 `resultno`；成功判定为「有编号 / resultstatus == 0 / 文案含
    「创建成功」「新增成功」」；文案里的编号只作最后兜底（措辞前后换过几版）。
    """

    if not isinstance(payload, dict):
        return False, "", ""
    message = text_of(payload.get("resultmessage"))
    result_no = text_of(payload.get("resultno"))
    ok = (
        bool(result_no)
        or str(payload.get("resultstatus")) == "0"
        or "创建成功" in message
        or "新增成功" in message
    )
    order_code = result_no if ok else ""
    if ok and not order_code:
        match = ORDER_CODE_PATTERN.search(message)
        if match:
            order_code = match.group(0)
    return ok, order_code, message


def management_api_base(settings: Settings) -> str:
    """BoManagementWebApi 根地址：提交订单、客服联系人等接口都挂在这个应用下。

    优先用专用配置 `DOCMIND_ORDER_API_BASE`；否则把公共主数据的应用名换成
    BoManagementWebApi（同主机、不同应用名，见 poOrder src/store/index.js:82）。
    """

    if settings.order_api_base:
        return settings.order_api_base
    base = settings.port_api_base
    if "/PublicWebApi" in base:
        return base.replace("/PublicWebApi", "/BoManagementWebApi")
    return base


class OrderSubmitService:
    """提交订单：组装报文 → 调用 poOrder → 解析订舱编号。"""

    def __init__(self, settings: Settings) -> None:
        api_base = management_api_base(settings)
        self.collector = OrderSubmitCollector(api_base) if api_base else None
        # 真实下单开关：默认关闭，哪个环境要下单就在该环境的 .env 里显式打开
        self.submit_enabled = settings.order_submit_enabled

    @property
    def enabled(self) -> bool:
        """接口已配置且允许真实下单时才可用。"""

        return self.collector is not None and self.submit_enabled

    async def submit(
        self,
        form: dict[str, Any],
        order: dict[str, Any],
        czman: str,
        ticket: str = "",
        service_codes: list[str] | None = None,
    ) -> OrderSubmitOutcome:
        """提交一单；缺少操作人时按 poOrder 口径直接驳回。

        `service_codes` 是服务项目面板勾选的服务代码（按面板顺序）；None = 用默认值。
        """

        operator = text_of(czman)
        if not operator:
            # 文案与 poOrder 前端拦截器一致：提交报文必须有操作人
            return OrderSubmitOutcome(ok=False, message="无操作人数据，请重新登录")
        if not self.submit_enabled:
            # 写操作默认关闭：不在代码里猜环境，要让某环境下单只能显式打开
            return OrderSubmitOutcome(
                ok=False,
                message=(
                    "提交订单功能未开启：需在 .env 设置 "
                    "DOCMIND_ORDER_SUBMIT_ENABLED=true 后重启服务"
                ),
            )
        if self.collector is None:
            return OrderSubmitOutcome(
                ok=False, message="提交接口未配置（DOCMIND_ORDER_API_BASE）"
            )
        payload = build_submit_payload(
            form, order, operator, service_codes=service_codes
        )
        started = time.perf_counter()
        try:
            raw = await self.collector.submit_order(payload, ticket=ticket)
        except Exception:
            logger.exception("提交订单失败：操作人 %s", operator)
            return OrderSubmitOutcome(
                ok=False, message="提交接口调用失败，请稍后重试", payload=payload
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        ok, order_code, message = parse_submit_result(raw)
        # 建单偏慢（poOrder 侧要跑信控等一整套校验，实测十几秒），因此把耗时、报文与
        # 原始响应都记进服务端日志，方便事后回答「到底建成没有、慢在哪」。
        # 报文含客户与联系人信息，只进日志、不额外外发
        logger.info(
            "提交订单完成：操作人=%s 耗时=%sms 成功=%s 编号=%s",
            operator,
            elapsed_ms,
            ok,
            order_code or "-",
        )
        # 报文与原始响应都记在 info：核对「到底提交了什么、返回了什么」全靠它们。
        # 报文含客户与联系人信息，生产环境若要收敛，把 DOCMIND_SYSTEM_LOG_LEVEL 调高即可
        logger.info("提交报文=%s", json.dumps(payload, ensure_ascii=False))
        logger.info("提交响应=%s", json.dumps(raw, ensure_ascii=False, default=str))
        return OrderSubmitOutcome(
            ok=ok,
            order_code=order_code,
            message=message,
            payload=payload,
            response=raw if isinstance(raw, dict) else None,
        )
