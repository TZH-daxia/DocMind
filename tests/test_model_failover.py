"""模型 provider 兜底与熔断测试。

锁定三件事：
1. 主 provider 成功时完全不碰兜底（未配兜底时行为与改造前一致）；
2. 主 provider 整轮耗尽（含超时）后由兜底接手，出口仍是同一种返回值；
3. 全部 provider 失败仍抛 ModelCallFailedError（下游据此判 MODEL_TIMEOUT），
   而 `fatal_exceptions`（空抽取）不触发切换。
"""

import asyncio
import json
from typing import Any

import httpx
import pytest
from openai import BadRequestError

from app.agent.circuit import ProviderCircuit
from app.agent.deepseek_extractor import (
    EmptyExtractionError,
    ExtractionAgent,
    ProviderClient,
)
from app.agent.model_call import (
    ModelCallFailedError,
    ProviderCall,
    call_with_failover,
)
from app.agent.providers import build_providers
from app.config import Settings
from app.schemas.po_order import PO_ORDER_KEYS


def make_call(
    provider: str,
    value: str | None,
    *,
    error: Exception | None = None,
    timeout_seconds: float = 0.5,
    retries: int = 0,
) -> ProviderCall:
    """构造一个 provider 调用：给 value 即成功，给 error 即失败。"""

    async def factory() -> str | None:
        if error is not None:
            raise error
        return value

    return ProviderCall(
        provider=provider,
        label=f"调用[{provider}]",
        factory=factory,
        timeout_seconds=timeout_seconds,
        retries=retries,
    )


async def test_primary_success_does_not_touch_fallback() -> None:
    calls = [make_call("primary", "主结果"), make_call("fallback", "兜底结果")]

    assert await call_with_failover("测试调用", calls) == "主结果"


async def test_fallback_takes_over_after_primary_exhausted() -> None:
    calls = [
        make_call("primary", None, error=RuntimeError("上游挂了"), retries=1),
        make_call("fallback", "兜底结果"),
    ]

    assert await call_with_failover("测试调用", calls) == "兜底结果"


async def test_timeout_of_primary_falls_back() -> None:
    async def slow() -> str:
        await asyncio.sleep(1)
        return "迟到的结果"

    calls = [
        ProviderCall("primary", "调用[primary]", slow, 0.01, 0),
        make_call("fallback", "兜底结果"),
    ]

    assert await call_with_failover("测试调用", calls) == "兜底结果"


async def test_all_providers_failed_raises_model_call_error() -> None:
    calls = [
        make_call("primary", None, error=RuntimeError("主挂了")),
        make_call("fallback", None, error=RuntimeError("备也挂了")),
    ]

    with pytest.raises(ModelCallFailedError) as excinfo:
        await call_with_failover("测试调用", calls)

    # 最后一个 provider 的错误上抛：下游 _run_task 照旧判 MODEL_TIMEOUT
    assert excinfo.value.provider == "fallback"


async def test_fatal_exception_does_not_switch_provider() -> None:
    """空抽取是"模型正常返回但抽不出字段"，换 provider 重试只会拉长耗时。"""

    async def empty() -> list[Any]:
        raise EmptyExtractionError("EMPTY_EXTRACTION")

    calls = [
        ProviderCall("primary", "字段抽取[primary]", empty, 1.0, 0),
        make_call("fallback", "兜底结果"),
    ]

    with pytest.raises(EmptyExtractionError):
        await call_with_failover(
            "字段抽取", calls, fatal_exceptions=(EmptyExtractionError,)
        )


async def test_circuit_skips_primary_after_repeated_failures() -> None:
    circuit = ProviderCircuit(failure_threshold=2, cooldown_seconds=60.0)
    primary_attempts = 0

    async def failing_primary() -> str:
        nonlocal primary_attempts
        primary_attempts += 1
        raise RuntimeError("主挂了")

    def calls() -> list[ProviderCall]:
        return [
            ProviderCall("primary", "调用[primary]", failing_primary, 0.5, 0),
            make_call("fallback", "兜底结果"),
        ]

    assert await call_with_failover("测试调用", calls(), circuit=circuit) == "兜底结果"
    assert await call_with_failover("测试调用", calls(), circuit=circuit) == "兜底结果"
    assert circuit.is_open("primary") is True

    # 熔断打开后：不再调用主 provider，直接走兜底
    assert await call_with_failover("测试调用", calls(), circuit=circuit) == "兜底结果"
    assert primary_attempts == 2


def test_agent_has_single_provider_without_fallback_key() -> None:
    # 带 validation_alias 的字段必须用 alias 传参（与 DOCMIND_DATA_ROOT 等一致）
    agent = ExtractionAgent(
        Settings(deepseek_api_key="test-key", DOCMIND_FALLBACK_API_KEY="")
    )

    assert agent.provider_names == ["deepseek"]
    assert len(agent.vision_calls("视觉提示词", [])) == 1
    assert len(agent.extract_calls("抽取提示词", "视觉内容", {})) == 1


def test_agent_appends_fallback_provider_when_configured() -> None:
    settings = Settings(
        deepseek_api_key="test-key",
        DOCMIND_FALLBACK_API_KEY="sk-fallback",
        DOCMIND_FALLBACK_MODEL="qwen3.8-flash",
        DOCMIND_FALLBACK_VISION_MODEL="qwen3.8-flash",
    )
    agent = ExtractionAgent(settings)

    assert agent.provider_names == ["deepseek", "qwen"]
    calls = agent.extract_calls("抽取提示词", "视觉内容", {})
    assert [call.provider for call in calls] == ["deepseek", "qwen"]
    # 主 provider 用配置的重试预算，兜底只用一次（不叠加重试）
    assert calls[0].retries == settings.model_call_retries
    assert calls[1].retries == settings.fallback_call_retries == 0


async def test_primary_vision_call_goes_through_agent_method() -> None:
    """主 provider 的工厂取 agent 的方法，测试/调用方替换后仍然生效。"""

    agent = ExtractionAgent(
        Settings(deepseek_api_key="test-key", DOCMIND_FALLBACK_API_KEY="sk-fallback")
    )

    async def fake_describe(prompt: str, images: list[Any]) -> str:
        return "替换后的视觉内容"

    agent.describe_images = fake_describe  # type: ignore[method-assign]
    calls = agent.vision_calls("视觉提示词", [])

    assert await calls[0].factory() == "替换后的视觉内容"


def test_providers_keep_their_own_extra_body() -> None:
    """DeepSeek 用 thinking、千问用 enable_thinking，串用会 400。"""

    settings = Settings(
        deepseek_api_key="sk-main",
        deepseek_thinking=True,
        deepseek_vision_thinking=False,
        DOCMIND_FALLBACK_API_KEY="sk-fallback",
        DOCMIND_FALLBACK_THINKING=True,
    )
    providers = build_providers(settings)

    main, fallback = providers
    assert main.extra_body_extract == {"thinking": {"type": "enabled"}}
    assert main.extra_body_vision == {"thinking": {"type": "disabled"}}
    # 千问的思考开关必须显式下发：不传时平台按模型默认值走（实测默认开思考）
    assert fallback.extra_body_extract == {"enable_thinking": True}
    assert fallback.extra_body_vision == {"enable_thinking": True}
    # 结构化输出方式：DeepSeek 只能用 function_calling；
    # 千问两种都支持，但仍默认 function_calling（与主链路同一条解析/降级路径）
    assert main.structured_method == "function_calling"
    assert fallback.structured_method == "function_calling"


class FailingChain:
    """总是抛出同一个异常的链替身。"""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def ainvoke(self, _variables: dict[str, Any]) -> str:
        raise self.error


def build_provider_client() -> ProviderClient:
    providers = build_providers(
        Settings(deepseek_api_key="sk-main", DOCMIND_FALLBACK_API_KEY="")
    )
    return ProviderClient(providers[0])


def free_form_payload() -> str:
    return json.dumps(
        {
            key: (
                {
                    "value": "SHANGHAI",
                    "status": "normalized",
                    "confidence": 0.9,
                    "evidence": [{"quote": "装运港：SHANGHAI"}],
                }
                if key == "sfg"
                else {"value": None, "status": "missing"}
            )
            for key in PO_ORDER_KEYS
        },
        ensure_ascii=False,
    )


async def test_structured_chain_kept_on_transient_error() -> None:
    """上游网络故障不能把"结构化输出"记成永久不可用。

    主模型恢复后必须还能走结构化抽取 —— 这也是本次端到端验证中暴露的问题：
    死地址的 502 一度把 DeepSeek 的 structured_chain 永久置空。
    """

    client = build_provider_client()
    error = httpx.ConnectError("connection refused")
    client.structured_chain = FailingChain(error)
    client.extraction_chain = FailingChain(error)

    # 结构化失败后回退 free-form，free-form 同样失败 → 异常上抛给上层重试/兜底
    with pytest.raises(httpx.ConnectError):
        await client.extract("system", "视觉内容", {})

    assert client.structured_chain is not None


async def test_structured_chain_dropped_on_bad_request() -> None:
    """400 表示本模型不接受这种结构化方式，记住并长期降级为 free-form。"""

    client = build_provider_client()
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://example.invalid/v1/chat")
    )
    client.structured_chain = FailingChain(
        BadRequestError("bad request", response=response, body=None)
    )
    client.extraction_chain = _StubTextChain(free_form_payload())

    candidates = await client.extract("system", "视觉内容", {})

    assert client.structured_chain is None
    assert any(candidate.value == "SHANGHAI" for candidate in candidates)


class _StubTextChain:
    """返回固定文本的链替身（走 free-form 路径）。"""

    def __init__(self, text: str) -> None:
        self.text = text

    async def ainvoke(self, _variables: dict[str, Any]) -> str:
        return self.text
