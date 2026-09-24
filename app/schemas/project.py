"""委托项目（客户/供应商的项目，即最小结算单位）主数据相关的数据结构。

来源与 poOrder 订单新增页的「项目」下拉一致：poOrder PublicWebApi
`/api/PubCustom`（`comxz=-1`）。记录的 `usr_name` 形如「简称-全称」，
poOrder 在缓存派生时就切成简称（`boStatic/js/overall.js` 的 `wtxmNoname`：
`usr_name.split("-")[0]`、`usr_code.split("-")[1]`），下拉只显示简称、
提交的是项目 `id`。
"""

from pydantic import BaseModel, Field

NAME_SEPARATOR = "-"
"""`usr_name` / `usr_code` 里简称与全称（编码前后段）的分隔符。"""

DEFAULT_LIST_LIMIT = 1000
"""单个委托客户的项目候选上限。

下拉面板与 poOrder 一致，是**纯列表、不做前端搜索**，所以必须一次列全：
实测最大一个客户有 937 个项目，这里取 1000 作兜底，防止异常数据把响应撑大。
"""


def split_name(value: str) -> str:
    """取「简称-全称」里的简称；没有分隔符时原样返回。"""

    text = str(value or "").strip()
    return text.split(NAME_SEPARATOR)[0].strip() if NAME_SEPARATOR in text else text


def split_code(value: str) -> str:
    """取项目编码里分隔符后的一段；没有分隔符时原样返回。"""

    text = str(value or "").strip()
    if NAME_SEPARATOR not in text:
        return text
    _, _, tail = text.partition(NAME_SEPARATOR)
    return tail.strip() or text


class ProjectRecord(BaseModel):
    """项目主数据单条记录（只保留查询与展示需要的字段）。"""

    id: str
    # 所属委托客户 id：项目按客户归属，下拉按 fid 收敛
    fid: str = ""
    # 字典原文，形如「简称-全称」
    full_name: str = ""
    # 下拉显示用的简称
    usr_name: str = ""
    # 编码原文；对外只给分隔符后的一段（对齐 poOrder）
    usr_code: str = ""
    # 1 = 有效；2 = 无效
    usr_status: int = 0
    # 财务侧有效性：poOrder 入缓存时只保留 usr_status_cw == 1 的记录
    usr_status_cw: int = 0
    # 逗号分隔；含 1 表示客户项目（poOrder 只保留 comxz == 1 的记录）
    comxz: str = ""
    # 2 = 不参与新业务
    customxz: int = 0
    # 该项目允许的站点，逗号分隔；"-1" 表示不限站点
    #（口径见 poOrder newOrderAdd.vue 的「该项目没有X站点权限！」判定）
    area: str = ""
    # 该项目允许的业务系统 **字典 id**，逗号分隔；"-1" 表示不限
    #（poOrder 用 groupid == 57 的字典把 id 翻成「空出/海进」这类名字）
    system: str = ""
    # 增量拉取用的时间戳
    timestamp: int = 0


class ProjectReferenceCache(BaseModel):
    """项目主数据本地缓存（reference_cache/projects.json）。"""

    fetched_at: str
    records: list[ProjectRecord] = Field(default_factory=list)


class ProjectCandidate(BaseModel):
    """下拉里的一个项目：id 进提交报文，name 给人看。"""

    id: str = Field(description="项目 ID，提交值")
    name: str = Field(default="", description="项目简称，下拉显示")
    code: str = Field(default="", description="项目编码，随行带出用于拼单号")
    full_name: str = Field(default="", description="字典原文，形如「简称-全称」")
    area: str = Field(
        default="",
        description="该项目允许的站点，逗号分隔；'-1' 表示不限（站点权限判定用）",
    )
    systems: list[str] = Field(
        default_factory=list,
        description="该项目允许的业务系统名（如「空出」）；['-1'] 表示不限",
    )
