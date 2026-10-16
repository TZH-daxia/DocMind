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

多个 provider（主 + 兜底）时走 `call_with_failover`：按顺序把每个 provider 完整
跑一遍 `call_with_retry`，前一个整轮耗尽才交给下一个；全部失败仍然抛
`ModelCallFailedError`，出口与单 provider 时一致。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.circuit import ProviderCircuit

logger = logging.getLogger(__name__)

# 重试前的退避基数（第 n 次重试等待 n × 基数秒）：并发拉高后上游更容易限流，
# 重试前让出一点时间
RETRY_BACKOFF_SECONDS = 1.0


class ModelCallFailedError(RuntimeError):
    """模型调用在重试耗尽后仍失败（超时、报错或返回内容不可用）。"""

    def __init__(
        self,
        label: str,
        attempts: int,
        last_error: BaseException | None,
        provider: str = "",
    ) -> None:
        where = f"（provider={provider}）" if provider else ""
        super().__init__(f"{label}{where} 连续 {attempts} 次调用失败：{last_error}")
        self.label = label
        self.attempts = attempts
        self.last_error = last_error
        self.provider = provider


@dataclass(frozen=True)
class ProviderCall:
    """一次「某个 provider 的业务调用」：provider 名 + 可等待工厂 + 本次预算。

    `timeout_seconds` / `retries` 都是该 provider 自己的预算，因此主 provider 与
    兜底 provider 可以给不同的重试次数（兜底通常只试一次，避免两轮重试叠加）。
    """

    provider: str
    label: str
    factory: Callable[[], Awaitable[Any]]
    timeout_seconds: float
    retries: int


async def call_with_retry[ResultT](
    label: str,
    factory: Callable[[], Awaitable[ResultT]],
    *,
    timeout_seconds: float,
    retries: int,
    should_retry: Callable[[ResultT], bool] | None = None,
    guard: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    fatal_exceptions: tuple[type[BaseException], ...] = (),
    provider: str = "",
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
        provider: provider 短名，只用于日志与错误信息（单 provider 调用时留空）。

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
        except Exception as exc:
            if isinstance(exc, fatal_exceptions):
                raise
            last_error = exc
            logger.warning(
                "%s 第 %s/%s 次调用失败%s（单次硬超时 %.0fs）：%s",
                label,
                attempt,
                attempts,
                f"[{provider}]" if provider else "",
                timeout_seconds,
                exc,
            )
        else:
            if should_retry is not None and should_retry(result):
                last_error = RuntimeError(f"{label} 返回内容不可用")
                logger.warning(
                    "%s 第 %s/%s 次返回内容不可用，准备重试%s",
                    label,
                    attempt,
                    attempts,
                    f"[{provider}]" if provider else "",
                )
            else:
                return result
        if attempt < attempts:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise ModelCallFailedError(label, attempts, last_error, provider) from last_error


async def call_with_failover(
    label: str,
    calls: Sequence[ProviderCall],
    *,
    should_retry: Callable[[Any], bool] | None = None,
    guard: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    fatal_exceptions: tuple[type[BaseException], ...] = (),
    circuit: "ProviderCircuit | None" = None,
) -> Any:
    """按 provider 顺序调用：主 provider 整轮耗尽后由兜底 provider 接手。

    每个 provider 各自走一次完整的 `call_with_retry`（独立墙钟超时、独立重试预算、
    独立熔断计数），因此：
    - 单 provider 时与直接调用 `call_with_retry` 等价；
    - 中途切换 provider 不影响下游：成功的返回值原样返回，全部失败时抛最后一个
      `ModelCallFailedError`，`_run_task` 照旧判 `MODEL_TIMEOUT`。

    `fatal_exceptions`（如 `EmptyExtractionError`）依旧直接上抛，不触发切换：
    那是"模型正常返回但抽不出字段"，换家重试只会把失败耗时翻倍。
    """

    if not calls:
        raise ValueError("至少需要一个 provider 调用")

    last_error: ModelCallFailedError | None = None
    for index, call in enumerate(calls):
        has_next = index + 1 < len(calls)
        if has_next and circuit is not None and circuit.is_open(call.provider):
            logger.warning(
                "%s：provider %s 处于熔断期，跳过并直接走兜底", label, call.provider
            )
            continue
        try:
            result = await call_with_retry(
                call.label,
                call.factory,
                timeout_seconds=call.timeout_seconds,
                retries=call.retries,
                should_retry=should_retry,
                guard=guard,
                fatal_exceptions=fatal_exceptions,
                provider=call.provider,
            )
        except ModelCallFailedError as exc:
            if circuit is not None:
                circuit.record_failure(call.provider)
            last_error = exc
            if has_next:
                logger.warning(
                    "%s：provider %s 重试已耗尽，改用兜底 provider %s",
                    label,
                    call.provider,
                    calls[index + 1].provider,
                )
            continue
        if circuit is not None:
            circuit.record_success(call.provider)
        if index > 0:
            logger.warning(
                "%s：本次已由兜底 provider %s（%s）完成", label, call.provider, call.label
            )
        return result

    assert last_error is not None
    raise last_error
