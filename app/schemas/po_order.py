# 订单新增最终输出字段（12 个）。必填性与输出顺序分别维护。
# 字段名已与 poOrder 订单新增模块（newOrderAdd.vue）核对对齐。
PO_ORDER_REQUIRED_KEYS = (
    "fid",                # 委托客户（托书无此字段，由调用方 context 提供）
    "sfg",                # 始发港
    "mdg",                # 目的港
    "ybpiece",            # 件数
    "ybweight",           # 重量
    "ybvolume",           # 体积
    "hbrq",               # 航班日期/船期
    "inwageallinprice",   # 运费单价（应收运费价格；COLLECT/PREPAID 条款视为无效）
)

PO_ORDER_OPTIONAL_KEYS = (
    "chinesepm", # 中文品名
    "englishpm", # 英文品名
    "shipper",   # 发货人信息
    "consignee", # 收货人信息
)

PO_ORDER_KEYS = PO_ORDER_REQUIRED_KEYS + PO_ORDER_OPTIONAL_KEYS

CONTEXT_ONLY_KEYS = frozenset({"fid"})

# 发货人/收货人对象只保留名称、地址、电话、邮箱。
PARTY_KEYS: tuple[str, ...] = ("name", "address", "phone", "email")

# 需要做三字码归一化（模型识别 + 主数据查表校验）的航线字段。
PORT_FIELD_KEYS: tuple[str, ...] = ("sfg", "mdg")
