"""用户默认设置（poOrder 用户设置模板 `type=110`）的输出结构。"""

from pydantic import BaseModel, Field


class UserDefaults(BaseModel):
    """某个登录名在 poOrder 里的「订单新增默认设置」。

    取值口径与 poOrder `newOrderAdd.vue` 的 `otherInitData()` 一致：
    `mawbAddArea` → 唯凯站点，`mawbAddSystem.opersystem` → 运输种类，
    `mawbAddSystem.opersystemdom` → 服务方式。取不到的项一律为空串
    （该用户没配、接口未接入、或接口调用失败），由调用方决定怎么兜底。
    """

    enabled: bool = Field(
        default=False, description="用户设置接口是否已接入（未配置地址时为 false）"
    )
    logname: str = Field(default="", description="查询用的登录名")
    area: str = Field(default="", description="默认唯凯站点（mawbAddArea）")
    opersystem: str = Field(
        default="", description="默认运输种类（mawbAddSystem.opersystem：出口/进口/国内）"
    )
    opersystemdom: str = Field(
        default="", description="默认服务方式（mawbAddSystem.opersystemdom：空运/海运/…）"
    )
    service: str = Field(
        default="", description="默认服务项目（mawbAddService，逗号分隔，可空）"
    )
