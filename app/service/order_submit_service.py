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

# 幂等键（request_id）记录的保留时长。这期间带同一个键的请求一律回放首次结果，
# 不再调用 poOrder —— 解决「提交请求超时但 poOrder 其实已建单、用户重试后重复建单」。
# 记录只在**本进程内存**里，配合 README 的部署约定（单副本、单 uvicorn worker）即可
# 全局生效；服务重启会清空，那时若有在途重试可能重复建单，属已知取舍。
SUBMIT_IDEMPOTENCY_TTL_SECONDS = 3600.0


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


# 数值字段的报文体契约（与前端 result-dialog/fields.js 同口径）：
# 只认正的普通十进制写法，拒绝 0、负号、正号、科学计数（1e3）、千分位等；
# 整数控件（件数）不接受小数。前端已有同样规则的本地校验，这里再拦一道——
# 接口是直调的，不能只信前端。
NUMERIC_FIELD_RULES: dict[str, tuple[str, str]] = {
    # 字段 →（控件类型, 中文名，用于报错文案）
    "ybpiece": ("integer", "件数"),
    "ybweight": ("number", "实际毛重"),
    "ybvolume": ("number", "总体积"),
    "inwageallinprice": ("number", "预计运费单价"),
}
NUMERIC_PATTERN = re.compile(r"^(?:\d+(?:\.\d*)?|\.\d+)$")
INTEGER_PATTERN = re.compile(r"^\d+$")
MAX_DECIMAL_PLACES = 6


def validate_numeric_fields(form: dict[str, Any]) -> str:
    """校验报文里的数值字段；通过返回空串，否则返回给操作员看的原因。

    空值不在这里判（必填缺失由前端校验与 poOrder 兜底），只盯「填了但不合法」。
    件数 / 毛重 / 体积 / 运费单价都必须是正数：0 与负数一样无意义，一律拦下。
    """

    for key, (control, label) in NUMERIC_FIELD_RULES.items():
        text = text_of(form.get(key))
        if not text:
            continue
        if control == "integer":
            if not INTEGER_PATTERN.match(text) or int(text) <= 0:
                return f"「{label}」必须是大于 0 的整数"
            continue
        # "12." 是输入中间态、不是完整数字：与前端一致按非法处理
        if not NUMERIC_PATTERN.match(text) or text.endswith("."):
            return f"「{label}」必须是大于 0 的数字（不接受负号、科学计数等写法）"
        decimals = text.split(".", 1)[1] if "." in text else ""
        if len(decimals) > MAX_DECIMAL_PLACES:
            return f"「{label}」小数位不能超过 {MAX_DECIMAL_PLACES} 位"
        if float(text) <= 0:
            return f"「{label}」必须大于 0"
    return ""


# 订单编号形如 BOAE2609240001PVG / BOAE202601010001：前缀 + 日期 + 流水 + 始发港
ORDER_CODE_PATTERN = re.compile(r"[A-Z]{2,}[0-9]{6,}[A-Z]*")

# 提交结果的三态判定。**不能只分成功/失败**：poOrder 会返回"说成功却没给编号"这种
# 定性不了的形态，把它强行归到任何一边都要付代价（见 parse_submit_result 的说明）。
SUBMIT_CREATED = "created"      # 已建单（拿到了订单编号）
SUBMIT_REJECTED = "rejected"    # 明确被拒，确定没有建单
SUBMIT_UNCERTAIN = "uncertain"  # 拿不到编号也定不了性：保守处理，不许换键重试


def parse_submit_result(payload: Any) -> tuple[str, str, str]:
    """解析提交结果 →（判定, 订单编号, 提示文案）。

    判定取 `SUBMIT_CREATED` / `SUBMIT_REJECTED` / `SUBMIT_UNCERTAIN` 之一。

    **订单编号（`resultno`）是"已建单"的唯一硬证据**：只有拿到编号才算成功。
    「说成功却给不出编号」一律归为 `SUBMIT_UNCERTAIN`，因为两种误判的代价都比
    "让操作员去 poOrder 核对一下"高：

    - 当成功报出去：万一实际没建单，用户以为建好了、按钮还置灰 —— 直接漏单；
    - 当失败报出去：前端会换一把新的幂等键重试，万一实际已建单 —— 重复建单。

    实测（见 logs/app.log）poOrder 的返回形态：

    - 创建成功、有待办：`{"resultstatus": 9999, "resultmessage": "订单创建成功并锁定,…订单编号为:BOAE…", "resultno": "BOAE…"}`
    - 创建成功、无待办：`{"resultstatus": 0, "resultmessage": "新增成功", "resultno": "BOAE…"}`
      —— 这种文案里**根本没有编号**，只能从 `resultno` 取（曾因此编号取不到）
    - 业务校验不通过：`{"resultstatus": 1, "resultmessage": "航班日期不能小于创建日期"}`
    """

    if not isinstance(payload, dict):
        # 响应不是对象（形态异常）：定性不了，按"不确定"处理
        return SUBMIT_UNCERTAIN, "", ""
    message = text_of(payload.get("resultmessage"))
    result_no = text_of(payload.get("resultno"))
    if result_no:
        return SUBMIT_CREATED, result_no, message
    # 编号也可能只写在文案里（措辞前后换过几版）：能提取到就算拿到了编号
    match = ORDER_CODE_PATTERN.search(message)
    if match:
        return SUBMIT_CREATED, match.group(0), message
    if (
        str(payload.get("resultstatus")) == "0"
        or "创建成功" in message
        or "新增成功" in message
    ):
        # 有"成功"的迹象却没有编号：定性不了，交人工核对
        return SUBMIT_UNCERTAIN, "", message
    return SUBMIT_REJECTED, "", message


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
        # 幂等键 →（结果, 记录时刻）。结果为 None 表示"正在提交中"（占位，用来拦并发）。
        # 只在本进程内存里：见 SUBMIT_IDEMPOTENCY_TTL_SECONDS 的说明。
        self._attempts: dict[str, tuple[OrderSubmitOutcome | None, float]] = {}

    @property
    def enabled(self) -> bool:
        """接口已配置且允许真实下单时才可用。"""

        return self.collector is not None and self.submit_enabled

    def _prune_attempts(self) -> None:
        """清掉超过 TTL 的幂等记录，避免服务长期运行后内存只增不减。"""

        if not self._attempts:
            return
        deadline = time.monotonic() - SUBMIT_IDEMPOTENCY_TTL_SECONDS
        for key in [k for k, (_, ts) in self._attempts.items() if ts < deadline]:
            del self._attempts[key]

    def _recall_attempt(self, key: str) -> OrderSubmitOutcome | None:
        """回放同一幂等键的已有结果；该键从没出现过则返回 None。"""

        self._prune_attempts()
        record = self._attempts.get(key)
        if record is None:
            return None
        outcome, _ = record
        if outcome is None:
            # 上一次还没回来：拦下并发重试，避免同一单被同时发两次
            return OrderSubmitOutcome(
                ok=False,
                message="该订单正在提交中，请稍候，请勿重复提交",
            )
        # 回放首次结果：即便首次响应在半路丢了，重试也能拿到同一个编号，不会再下单
        return outcome.model_copy(update={"duplicated": True})

    def _begin_attempt(self, key: str) -> None:
        """占位：从这一刻起到结果落库，同键请求一律拦为"提交中"。"""

        self._attempts[key] = (None, time.monotonic())

    def _finish_attempt(self, key: str, outcome: OrderSubmitOutcome) -> None:
        """结果落库，后续同键请求回放它（不论成功还是失败）。"""

        self._attempts[key] = (outcome, time.monotonic())

    async def submit(
        self,
        form: dict[str, Any],
        order: dict[str, Any],
        czman: str,
        ticket: str = "",
        service_codes: list[str] | None = None,
        request_id: str = "",
    ) -> OrderSubmitOutcome:
        """提交一单；缺少操作人时按 poOrder 口径直接驳回。

        `service_codes` 是服务项目面板勾选的服务代码（按面板顺序）；None = 用默认值。

        `request_id` 是幂等键：带了它就开启去重——同一把键的重复/重试请求**不会**
        再调 poOrder，而是回放首次结果（`duplicated=True`）；首次还在途时返回
        "提交中"。留空则不启用幂等（兼容旧调用方），行为与以往一致。
        """

        key = text_of(request_id)
        if key:
            # 先看有没有同一把键的既有结果：有就回放，绝不重复下单
            recalled = self._recall_attempt(key)
            if recalled is not None:
                logger.info(
                    "提交订单：幂等键命中，回放已有结果（键=%s 成功=%s）",
                    key,
                    recalled.ok,
                )
                return recalled
        operator = text_of(czman)
        if not operator:
            # 文案与 poOrder 前端拦截器一致：提交报文必须有操作人
            # 以下三种都是"还没碰 poOrder"的拦截，不落幂等记录：用户补齐条件后
            # 用同一把键重试即可，不会被挡在缓存外
            return OrderSubmitOutcome(
                ok=False, message="无操作人数据，请重新登录", retryable=True
            )
        if not self.submit_enabled:
            # 写操作默认关闭：不在代码里猜环境，要让某环境下单只能显式打开
            return OrderSubmitOutcome(
                ok=False,
                message=(
                    "提交订单功能未开启：需在 .env 设置 "
                    "DOCMIND_ORDER_SUBMIT_ENABLED=true 后重启服务"
                ),
                retryable=True,
            )
        if self.collector is None:
            return OrderSubmitOutcome(
                ok=False,
                message="提交接口未配置（DOCMIND_ORDER_API_BASE）",
                retryable=True,
            )
        problem = validate_numeric_fields(form)
        if problem:
            # 数值不合法：还没碰 poOrder，不占幂等键。用户改好后可用同一把键重试
            return OrderSubmitOutcome(ok=False, message=problem, retryable=True)
        payload = build_submit_payload(
            form, order, operator, service_codes=service_codes
        )
        # 报文组装是同步的，放在占位之前：万一它抛错就不会留下永远"提交中"的占位。
        # 从这里往下只要带键，就必须先占位——同键请求在结果落库前一律被拦下。
        if key:
            self._begin_attempt(key)
        started = time.perf_counter()
        try:
            raw = await self.collector.submit_order(payload, ticket=ticket)
        except Exception:
            logger.exception("提交订单失败：操作人 %s", operator)
            # 结果未知：poOrder 可能已经建单，也可能没收到。这条也记账，
            # 于是同一把键的重试只会回放这句提示、不会再发一次单。
            outcome = OrderSubmitOutcome(
                ok=False,
                message=(
                    "提交接口调用失败，结果未知：订单可能已创建，"
                    "请先到 poOrder 核对后再操作（本次重试不会重复建单）"
                ),
                payload=payload,
                retryable=False,
            )
            if key:
                self._finish_attempt(key, outcome)
            return outcome
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        status, order_code, message = parse_submit_result(raw)
        # 建单偏慢（poOrder 侧要跑信控等一整套校验，实测十几秒），因此把耗时、报文与
        # 原始响应都记进服务端日志，方便事后回答「到底建成没有、慢在哪」。
        # 报文含客户与联系人信息，只进日志、不额外外发
        logger.info(
            "提交订单完成：操作人=%s 耗时=%sms 判定=%s 编号=%s",
            operator,
            elapsed_ms,
            status,
            order_code or "-",
        )
        # 报文与原始响应都记在 info：核对「到底提交了什么、返回了什么」全靠它们。
        # 报文含客户与联系人信息，生产环境若要收敛，把 DOCMIND_SYSTEM_LOG_LEVEL 调高即可
        logger.info("提交报文=%s", json.dumps(payload, ensure_ascii=False))
        logger.info("提交响应=%s", json.dumps(raw, ensure_ascii=False, default=str))
        if status == SUBMIT_UNCERTAIN:
            # 定性不了：给操作员一句能照做的提示，别让它被当成"成功"或"失败"
            detail = f"（接口提示：{message}）" if message else ""
            message = (
                f"提交结果不确定：接口没有返回订单编号{detail}。"
                "请到 poOrder 核对；为避免重复建单，本次重试不会重复提交"
            )
        # 已建单 / 明确被拒都算"已有定论"，前端据此换新的幂等键（下一次点击按全新一单
        # 处理）；"不确定"保留同一把键，重试只回放这条结果，绝不重复下单
        outcome = OrderSubmitOutcome(
            ok=status == SUBMIT_CREATED,
            order_code=order_code,
            message=message,
            payload=payload,
            response=raw if isinstance(raw, dict) else None,
            retryable=status != SUBMIT_UNCERTAIN,
        )
        if key:
            self._finish_attempt(key, outcome)
        return outcome
