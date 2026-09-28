"""用户默认设置：从 poOrder 的用户设置模板取「订单新增」默认值。

弹窗工具条上的三项（唯凯站点 / 服务方式 / 运输种类）应当跟操作员在 poOrder 里
保存的默认设置一致，否则每开一份托书都要手动改一遍。数据来自
`api/UserTemplet` 的 `type=110` 记录（`jsondata` 里的 `mawbAddArea` /
`mawbAddSystem`），口径与 poOrder `newOrderAdd.vue` 的 `otherInitData()` 相同。

票据只做透传：接口在 poOrder 侧可能要求 `Authorization`（poOrder 前端对**所有**
请求都会带），因此由调用方按请求透传，DocMind 不签发、不保存。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from app.collector.user_templet_collector import UserTempletCollector
from app.config import Settings
from app.schemas.user_defaults import UserDefaults

logger = logging.getLogger(__name__)

# 用户设置模板的 type：110 = 用户设置（见 poOrder src/store/index.js 的注释
# 「type: 110用户设置」，以及 api/getBasicStorageData.js 的 `i.type == 110`）
USER_SETTING_TYPE = 110
# 启用标记：停用的模板不该拿来当默认值
ACTIVE_FLAG = 1
# poOrder 侧固定的项目名（本系统对接的是 bo 后台）
DEFAULT_PROJECT = "bomanagement"
# 服务方式存成「国内」时它其实指的是运输种类，按 poOrder 口径置空
# （newOrderAdd.vue:3201：`opersystemdom == "国内" ? '' : opersystemdom`）
DOMESTIC_MODE = "国内"


def _text(value: Any) -> str:
    """转成干净的字符串：None → 空串，其余去掉首尾空白。"""

    return "" if value is None else str(value).strip()


class UserDefaultService:
    """按登录名取（并短时缓存）poOrder 的用户默认设置。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # 与港口/客户/站点主数据是同一个服务：未单独配置时回退用 port_api_base
        api_base = settings.user_templet_api_base or settings.port_api_base
        self.collector = UserTempletCollector(api_base) if api_base else None
        # 按登录名缓存。接口一次返回该用户的全部模板（实测 ~660KB），而默认设置
        # 很少变更，缓存几分钟即可。缓存键只有登录名（不含票据）——默认设置本身
        # 不含凭据，且各登录名的设置互不相同
        self._cache: dict[str, tuple[datetime, UserDefaults]] = {}

    @property
    def enabled(self) -> bool:
        """未配置用户设置接口地址时整体停用（前端退化为内置兜底值）。"""

        return self.collector is not None

    async def get_defaults(self, logname: str, ticket: str = "") -> UserDefaults:
        """返回该登录名的默认站点/运输种类/服务方式；没有则各项为空串。

        接口不可用、调用失败或该用户没配设置时都不算异常：一律返回空值，由前端
        决定兜底（内置默认值），保证「取不到默认设置」不会拦人。
        """

        name = _text(logname)
        if not self.enabled:
            return UserDefaults(enabled=False, logname=name)
        if not name:
            # 没有登录名就没法查（接口按 logname 索引），也不算失败
            return UserDefaults(enabled=True, logname="")
        cached = self._cache.get(name)
        if cached is not None and not self._expired(cached[0]):
            return cached[1]
        try:
            records = await self.collector.fetch_records(name, DEFAULT_PROJECT, ticket)
        except Exception:
            logger.exception("用户默认设置拉取失败：logname=%s", name)
            if cached is not None:
                # 主数据抖动时沿用旧值，别让工具条退回内置默认值
                logger.info("沿用上次的用户默认设置缓存：logname=%s", name)
                return cached[1]
            return UserDefaults(enabled=True, logname=name)
        defaults = self._parse(name, records)
        self._cache[name] = (datetime.now().astimezone(), defaults)
        logger.info(
            "用户默认设置就绪：logname=%s 站点=%r 运输种类=%r 服务方式=%r",
            name,
            defaults.area,
            defaults.opersystem,
            defaults.opersystemdom,
        )
        return defaults

    def _expired(self, cached_at: datetime) -> bool:
        ttl_minutes = self.settings.user_defaults_cache_ttl_minutes
        if ttl_minutes <= 0:
            # TTL 配 0 = 不缓存（见 config.py 的说明）：不能退化成"比时间差"，
            # 否则同一毫秒内的两次调用仍会命中缓存，表现为配置不起作用
            return True
        return datetime.now().astimezone() - cached_at > timedelta(minutes=ttl_minutes)

    @staticmethod
    def _pick_user_setting(records: list[dict[str, Any]]) -> dict[str, Any] | None:
        """挑出 `type=110` 的记录，**优先启用项**（`isactivate == 1`）。

        poOrder 自己用 `find(i => i.type == 110)`：只看数组顺序、不看启用状态。
        实测 admin 账号有两条 110——一条启用（站点=上海、系统=空运/出口），一条
        停用（站点与系统都是空）；当下是"碰巧"取到启用那条，顺序一变就会取到空值。
        这里显式优先启用项，同组内保持接口返回的原有次序。
        """

        candidates = [
            item for item in records if _text(item.get("type")) == str(USER_SETTING_TYPE)
        ]
        if not candidates:
            return None
        for item in candidates:
            if _text(item.get("isactivate")) == str(ACTIVE_FLAG):
                return item
        return candidates[0]

    @classmethod
    def _parse(cls, logname: str, records: list[dict[str, Any]]) -> UserDefaults:
        """把模板记录解析成默认设置；缺字段一律给空串。"""

        setting = cls._pick_user_setting(records)
        if setting is None:
            logger.info("该登录名没有用户设置模板（type=110）：logname=%s", logname)
            return UserDefaults(enabled=True, logname=logname)
        raw = setting.get("jsondata")
        data: dict[str, Any] = {}
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("用户设置模板的 jsondata 不是合法 JSON：logname=%s", logname)
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
        system = data.get("mawbAddSystem")
        system = system if isinstance(system, dict) else {}
        opersystemdom = _text(system.get("opersystemdom"))
        if opersystemdom == DOMESTIC_MODE:
            opersystemdom = ""
        return UserDefaults(
            enabled=True,
            logname=logname,
            area=_text(data.get("mawbAddArea")),
            opersystem=_text(system.get("opersystem")),
            opersystemdom=opersystemdom,
            service=_text(data.get("mawbAddService")),
        )
