"""模型提供方（provider）抽象：把"用哪家的哪个模型"与链路逻辑解耦。

一个 provider = 一套「接口地址 + 密钥 + 模型 ID + 该家专属的调用参数」。链路
（提示词、schema、解析、闸门、错误码）只认识"按顺序试的 provider 列表"，因此
换家、加兜底都不会碰到业务逻辑。

两处必须按 provider 区分的差异（串用会直接 400）：

1. `extra_body`：DeepSeek 用 `thinking`，通义千问用 `enable_thinking`；
2. `structured_method`：DeepSeek 的 `response_format` 只支持 text / json_object，
   结构化输出只能走 function_calling；千问两种方式都支持，本项目默认仍用
   function_calling（与主链路同一条解析/降级路径，见 config.fallback_structured_method）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import Settings


@dataclass(frozen=True)
class ModelProvider:
    """一个模型提供方的全部调用参数（不含业务语义）。"""

    name: str
    """日志与熔断用的短名，如 `deepseek`、`qwen`。"""

    api_key: str
    base_url: str
    model: str
    """字段抽取用的模型 ID。"""

    vision_model: str
    """图片识别用的模型 ID，可与抽取不同（如 flash 转写 + plus 抽取）。"""

    temperature: float
    read_timeout_seconds: float
    """单次 HTTP 的读超时；真正的调用上限由墙钟超时兜底（见 model_call）。"""

    connect_timeout_seconds: float
    sdk_max_retries: int
    """openai SDK 自己的重试次数。"""

    call_retries: int
    """墙钟超时下的重试次数（总尝试次数 = 1 + 本值），由 call_with_failover 使用。"""

    structured_method: str | None
    """结构化输出方式：`json_schema` / `function_calling`；None 表示直接走自由文本。"""

    extra_body_extract: Mapping[str, Any] = field(default_factory=dict)
    extra_body_vision: Mapping[str, Any] = field(default_factory=dict)

    def build_chat_model(self, *, vision: bool = False) -> ChatOpenAI:
        """构造该 provider 的 ChatOpenAI 客户端（vision=True 用图片识别那份参数）。"""

        extra_body = dict(
            self.extra_body_vision if vision else self.extra_body_extract
        )
        return ChatOpenAI(
            api_key=SecretStr(self.api_key),
            base_url=self.base_url,
            model=self.vision_model if vision else self.model,
            temperature=self.temperature,
            timeout=httpx.Timeout(
                self.read_timeout_seconds, connect=self.connect_timeout_seconds
            ),
            max_retries=self.sdk_max_retries,
            extra_body=extra_body or None,
        )


def build_providers(settings: Settings) -> list[ModelProvider]:
    """按优先级返回 provider 列表：主 provider 在前，兜底 provider 在后。

    兜底 provider 未配置密钥时不入列，此时列表只有一个元素，行为与"没有兜底"
    完全一致（零分支、零额外耗时）。
    """

    if not settings.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY is required")

    providers: list[ModelProvider] = [
        ModelProvider(
            name="deepseek",
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            vision_model=settings.deepseek_model,
            temperature=settings.deepseek_temperature,
            read_timeout_seconds=settings.deepseek_timeout_seconds,
            connect_timeout_seconds=settings.deepseek_connect_timeout_seconds,
            sdk_max_retries=settings.deepseek_max_retries,
            call_retries=settings.model_call_retries,
            structured_method="function_calling",
            extra_body_extract=_deepseek_thinking_body(settings.deepseek_thinking),
            extra_body_vision=_deepseek_thinking_body(settings.deepseek_vision_thinking),
        )
    ]
    fallback = _build_fallback_provider(settings)
    if fallback is not None:
        providers.append(fallback)
    return providers


def _deepseek_thinking_body(enabled: bool) -> dict[str, Any]:
    return {"thinking": {"type": "enabled" if enabled else "disabled"}}


def _build_fallback_provider(settings: Settings) -> ModelProvider | None:
    """兜底 provider：未配密钥返回 None（此时整条兜底链路不生效）。"""

    if not settings.fallback_api_key:
        return None
    vision_model = settings.fallback_vision_model or settings.fallback_model
    # 千问的思考开关是 enable_thinking（不是 DeepSeek 的 thinking），且**必须显式
    # 下发**：不传时平台按模型默认值走（实测 qwen3.8-flash 默认开思考，结构化抽取
    # 会慢一倍以上、字段值还出现整体错位），所以这里 True/False 都显式给。
    # 换到不支持该参数的模型（如部分纯视觉模型）时，需要在此处按模型调整。
    thinking_body: dict[str, Any] = {"enable_thinking": settings.fallback_thinking}
    return ModelProvider(
        name=settings.fallback_name,
        api_key=settings.fallback_api_key,
        # 兼容模式地址的唯一出口是配置项（见 config.fallback_base_url 默认值）
        base_url=settings.fallback_base_url,
        model=settings.fallback_model,
        vision_model=vision_model,
        temperature=settings.fallback_temperature,
        read_timeout_seconds=settings.fallback_timeout_seconds,
        connect_timeout_seconds=settings.fallback_connect_timeout_seconds,
        sdk_max_retries=settings.fallback_max_retries,
        call_retries=settings.fallback_call_retries,
        structured_method=settings.fallback_structured_method or None,
        extra_body_extract=thinking_body,
        extra_body_vision=thinking_body,
    )
