"""provider 熔断：主模型整体挂掉时，别让每个任务都先陪跑一遍它的重试。

没有熔断时，"主模型挂了"的表现是每个任务仍先花满主 provider 的重试预算
（VLM 2×60s、抽取 2×180s）才轮到兜底，用户体感依然是"卡住"。有了熔断，
连续失败若干次后直接跳过主 provider，兜底即时生效。

只在进程内计数：本项目只跑一个副本、一个 worker（见 DEPLOY.md），够用。
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class ProviderCircuit:
    """按 provider 记的简易熔断器。

    - 连续失败达到 `failure_threshold` 次 → 打开，`cooldown_seconds` 内直接跳过；
    - 冷却到点后放行一次试探（半开）：成功即清零，失败则重新计时；
    - 只统计"整轮调用失败"（超时、报错）；用户主动暂停/取消不经过这里。
    """

    failure_threshold: int
    cooldown_seconds: float

    def __init__(
        self, *, failure_threshold: int = 3, cooldown_seconds: float = 90.0
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def is_open(self, name: str) -> bool:
        """该 provider 当前是否处于熔断期（冷却到点会放行一次试探）。"""

        opened_at = self._opened_at.get(name)
        if opened_at is None:
            return False
        if time.monotonic() - opened_at < self.cooldown_seconds:
            return True
        # 冷却结束：半开，放一次试探
        del self._opened_at[name]
        self._failures[name] = 0
        logger.info("模型 provider %s 熔断冷却结束，放行一次试探调用", name)
        return False

    def record_success(self, name: str) -> None:
        if name in self._failures or name in self._opened_at:
            logger.info("模型 provider %s 调用成功，熔断计数清零", name)
        if name in self._failures:
            del self._failures[name]
        if name in self._opened_at:
            del self._opened_at[name]

    def record_failure(self, name: str) -> None:
        count = self._failures.get(name, 0) + 1
        self._failures[name] = count
        if count >= self.failure_threshold and name not in self._opened_at:
            self._opened_at[name] = time.monotonic()
            logger.warning(
                "模型 provider %s 连续失败 %s 次，熔断 %.0fs（期间直接走兜底）",
                name,
                count,
                self.cooldown_seconds,
            )
