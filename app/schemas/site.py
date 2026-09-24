"""唯凯站点（业务区域）字典相关的数据结构。

来源与 poOrder 订单新增页「委托唯凯站点」下拉一致：poOrder PublicWebApi
`/api/PubTypeCode` 的 `groupid == 101` 分组。字典项 `typename` 形如
「上海丨SHA」（站点名丨三字码），`ready04` 是分组名，下拉按分组分栏展示。
"""

from pydantic import BaseModel, Field

SITE_GROUP_ID = 101
"""唯凯站点字典在 PubTypeCode 中的分组号。"""

SITE_NAME_SEPARATOR = "丨"
"""字典 typename 中站点名与三字码的分隔符（全角竖线）。"""


class SiteRecord(BaseModel):
    """站点字典单条记录。"""

    name: str = Field(description="站点中文名（typename 中分隔符前的一段）")
    code: str = Field(default="", description="站点三字码（分隔符后的一段）")
    full_name: str = Field(default="", description="字典原文，形如「上海丨SHA」")
    group: str = Field(default="", description="分组名（ready04）")


class SiteReferenceCache(BaseModel):
    """站点字典本地缓存（reference_cache/sites.json）。"""

    fetched_at: str
    records: list[SiteRecord] = Field(default_factory=list)


class SiteOption(BaseModel):
    """下拉里的一个站点：value 进提交报文，label 给人看。"""

    value: str = Field(description="站点中文名，进提交报文（对齐 poOrder areaSelect）")
    label: str = Field(description="字典原文，形如「上海丨SHA」")


class SiteGroup(BaseModel):
    """按分组聚合的一组站点，用于下拉面板的分栏展示。"""

    label: str = Field(description="分组名")
    options: list[SiteOption] = Field(default_factory=list)
