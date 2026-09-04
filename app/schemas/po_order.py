from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

# 订单新增最终输出字段（12 个）。顺序即输出顺序：必填 8 个在前，选填 4 个在后。
# 字段名已与 poOrder 订单新增模块（newOrderAdd.vue）核对对齐。
PO_ORDER_REQUIRED_KEYS = (
    "sfg",                # 始发港
    "mdg",                # 目的港
    "ybpiece",            # 件数
    "ybweight",           # 重量
    "ybvolume",           # 体积
    "inwageallinprice",   # 运费（应收运费价格；COLLECT/PREPAID 条款视为无效）
    "hbrq",               # 预计航班日期/船期
    "fid",                # 委托客户（托书无此字段，由调用方 context 提供）
)

PO_ORDER_OPTIONAL_KEYS = (
    "shipper",   # 发货人
    "consignee", # 收货人
    "chinesepm", # 中文品名
    "englishpm", # 英文品名
)

PO_ORDER_KEYS = PO_ORDER_REQUIRED_KEYS + PO_ORDER_OPTIONAL_KEYS

# 字段中文名称（用于前端展示/字段说明，不作为 JSON key）。
FIELD_TITLES: dict[str, str] = {
    "sfg": "始发港",
    "mdg": "目的港",
    "ybpiece": "件数",
    "ybweight": "重量",
    "ybvolume": "体积",
    "inwageallinprice": "运费",
    "hbrq": "预计航班日期",
    "fid": "委托客户",
    "shipper": "发货人",
    "consignee": "收货人",
    "chinesepm": "中文品名",
    "englishpm": "英文品名",
}

CONTEXT_ONLY_KEYS = frozenset({"fid"})

NUMBER_KEYS = frozenset({"ybpiece", "ybweight", "ybvolume", "inwageallinprice"})

OBJECT_KEYS = frozenset({"shipper", "consignee"})

# 发货人/收货人对象只保留名称、地址、电话、邮箱。
PARTY_KEYS: tuple[str, ...] = ("name", "address", "phone", "email")


class PoOrderFields(BaseModel):
    """托书抽取最终输出的 12 个字段。"""

    model_config = ConfigDict(extra="allow")

    sfg: str | None = None
    mdg: str | None = None
    ybpiece: int | float | str | None = None
    ybweight: int | float | str | None = None
    ybvolume: int | float | str | None = None
    inwageallinprice: int | float | str | None = None
    hbrq: str | None = None
    fid: int | str | None = None
    shipper: dict[str, Any] | None = None
    consignee: dict[str, Any] | None = None
    chinesepm: str | None = None
    englishpm: str | None = None


class PartyInfo(BaseModel):
    """托书中的发货人或收货人参考信息，只保留名称、地址、电话、邮箱。"""

    name: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None

    @field_validator("address", mode="before")
    @classmethod
    def _merge_address_lines(cls, value: Any) -> Any:
        """地址必须是单个完整字符串。

        国外地址中的逗号/换行是同一地址的层级写法（街道、邮编、城市、国家），
        不是多个地址；模型若仍输出列表，这里按顺序合并为一行。
        """

        if isinstance(value, list):
            merged = ", ".join(str(item).strip() for item in value if str(item).strip())
            return merged or None
        if isinstance(value, str):
            return value.strip() or None
        return value
