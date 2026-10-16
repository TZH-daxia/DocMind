"""模型调用的统一超时与重试。

openai SDK 的 `timeout` 只是单次 HTTP 的 connect/read/write 超时，**没有"这次调用
总共最多允许多久"的上限**：上游（DeepSeek）异常时，只要连接上一直有零星流量
（代理心跳、半开连接），read 超时永远不会触发，`await chain.ainvoke(...)` 就无限
挂着 —— 表现是任务永远停在"正在解析中"，直到 nginx 的 3600s 才断。

因此所有模型调用都过 `call_with_retry`：
- 用 `asyncio.wait_for` 加一个**墙钟硬超时**，不受 read 超时语义影响；
- 失败按配置重试，退避等待期间释放模型并发闸门（见 `guard`）；
- 重试耗尽抛 `ModelCallFailedError`，由 `AnalysisService._run_task` 判
  `MODEL_TIMEOUT`，把中文原因回给前端，不再退化成无差别的"分析任务执行失败"。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")

# 重试前的退避基数（第 n 次重试等待 n × 基数秒）：并发拉高后上游更容易限流，
# 重试前让出一点时间
RETRY_BACKOFF_SECONDS = 1.0


class ModelCallFailedError(RuntimeError):
    """模型调用在重试耗尽后仍失败（超时、报错或返回内容不可用）。"""

    def __init__(
        self, label: str, attempts: int, last_error: BaseException | None
    ) -> None:
        super().__init__(f"{label} 连续 {attempts} 次调用失败：{last_error}")
        self.label = label
        self.attempts = attempts
        self.last_error = last_error


async def call_with_retry(
    label: str,
    factory: Callable[[], Awaitable[ResultT]],
    *,
    timeout_seconds: float,
    retries: int,
    should_retry: Callable[[ResultT], bool] | None = None,
    guard: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    fatal_exceptions: tuple[type[BaseException], ...] = (),
) -> ResultT:
    """执行一次模型调用：单次墙钟硬超时 + 最多 `retries` 次重试。

    Args:
        label: 日志与错误信息里的调用名（如 "VLM 图片识别"）。
        factory: 返回可等待对象的零参工厂，每次尝试都重新调用一次。
        timeout_seconds: 单次尝试的墙钟上限，超时即取消本次调用并计为失败。
        retries: 失败后的重试次数，总尝试次数为 `retries + 1`。
        should_retry: "调用成功但结果不可用"的判定（如 VLM 声称看不到图片），
            返回 True 则按失败重试。
        guard: 每次尝试外围的并发闸门工厂（如 `lambda: self._gate("model", n)`）。
            只包住单次调用，退避等待期间释放槽位，重试不占并发额度。
        fatal_exceptions: 不重试、直接上抛的异常（如 `EmptyExtractionError`，那是
            "模型正常返回但抽不出字段"，extract() 内部已重试过，再重试只会拉长失败耗时）。

    用户主动暂停/取消（`asyncio.CancelledError`）原样透传，不计入失败重试。
    """

    attempts = max(1, retries + 1)
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            if guard is None:
                result = await asyncio.wait_for(factory(), timeout=timeout_seconds)
            else:
                # 闸门只包住单次调用：退避等待时释放槽位，别让重试占着并发额度
                async with guard():
                    result = await asyncio.wait_for(factory(), timeout=timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - 重试耗尽后才上抛
            if isinstance(exc, fatal_exceptions):
                raise
            last_error = exc
            logger.warning(
                "%s 第 %s/%s 次调用失败（单次硬超时 %.0fs）：%s",
                label,
                attempt,
                attempts,
                timeout_seconds,
                exc,
            )
        else:
            if should_retry is not None and should_retry(result):
                last_error = RuntimeError(f"{label} 返回内容不可用")
                logger.warning(
                    "%s 第 %s/%s 次返回内容不可用，准备重试",
                    label,
                    attempt,
                    attempts,
                )
            else:
                return result
        if attempt < attempts:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise ModelCallFailedError(label, attempts, last_error) from last_error
