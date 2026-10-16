"""港口归一化的模型调用：只在本地匹配无法定论时使用。

模型不再直接产出三字码，只产出两类**可被本地校验**的中间量：
- 有候选时：从候选里选一个（`chosen_code`）；
- 无候选时：给出规范英文港口名（`english_name`），由主数据索引映射成三字码。
这样模型幻觉会落到"主数据匹配不到"，而不是变成一个存在但无关的错码。

与主抽取链路一样支持多 provider：主 provider 报错时由兜底 provider 接手。这里的
调用被 `PortNormalizationService` 用 8s 墙钟罩着，超时仍然按原设计转人工审核，
兜底只是"主 provider 快速失败（401/403/503 等）时还能出结果"。
"""

import json
import logging
from typing import Any, Literal, cast

from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from openai import BadRequestError
from pydantic import ValidationError

from app.agent.circuit import ProviderCircuit
from app.agent.deepseek_extractor import extract_json_payload
from app.agent.providers import ModelProvider, build_providers
from app.config import Settings
from app.schemas.port import (
    PortCodeSuggestionResult,
    PortSuggestionInput,
)

logger = logging.getLogger(__name__)

# 自由文本回退时的输出格式要求（结构化 response_format 不可用时使用）
PROPOSAL_FORMAT_INSTRUCTIONS = (
    '输出必须是合法 JSON 对象：{"suggestions": '
    '[{"field_key": "...", "chosen_code": "...", "english_name": "...", "reason": "..."}]}，'
    "每个输入字段都必须有对应条目，不要输出 JSON 以外的任何文字或代码块标记。"
)

# 结构化输出方法：DeepSeek 的 response_format 只支持 text / json_object，
# 不支持 OpenAI 的 json_schema，因此走 Tool Calls（function_calling）；
# 千问两种都支持，具体由 provider 配置决定
StructuredMethod = Literal["json_schema", "function_calling"]


class PortProviderClient:
    """单个 provider 的港口识别调用（结构化优先，失败降级自由文本）。"""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider
        self.model: ChatOpenAI = provider.build_chat_model()
        self.text_chain = self.model | StrOutputParser()
        # 结构化输出在构造期不可验证（400 错误发生在调用时），因此先记录
        # 方法，调用失败再降级为自由文本
        self.structured_method: StructuredMethod | None = cast(
            StructuredMethod, provider.structured_method
        ) if provider.structured_method else None

    async def suggest(
        self,
        messages: list[tuple[str, str]],
    ) -> PortCodeSuggestionResult:
        """给出"候选选择"或"规范英文名"（结构化失败自动降级自由文本）。"""

        if self.structured_method is not None:
            try:
                # tool_choice="auto"：DeepSeek 思考模式不支持具名 tool_choice
                # （langchain 默认绑定具名，会被拒），必须放行为自动选择；
                # json_schema 方式不接受 tool_choice 参数，故按方法分别传参
                structured_kwargs: dict[str, Any] = (
                    {"tool_choice": "auto"}
                    if self.structured_method == "function_calling"
                    else {}
                )
                result = await self.model.with_structured_output(
                    PortCodeSuggestionResult,
                    method=self.structured_method,
                    **structured_kwargs,
                ).ainvoke(messages)
                return _log_result(result)
            except Exception as exc:  # noqa: BLE001 - 降级为自由文本
                # 同主抽取链路：只有 400（方式不被支持）才记住降级，网络/5xx/超时
                # 只是暂时故障，不能记成"结构化不可用"，否则恢复后也用不了
                if isinstance(exc, BadRequestError):
                    logger.warning(
                        "provider=%s 不支持结构化输出 %s，后续改用自由文本：%s",
                        self.provider.name,
                        self.structured_method,
                        exc,
                    )
                    self.structured_method = None
                else:
                    logger.debug(
                        "provider=%s 结构化输出异常（暂时性，不降级）：%s",
                        self.provider.name,
                        exc,
                    )
        text = await self.text_chain.ainvoke(
            [*messages, ("human", PROPOSAL_FORMAT_INSTRUCTIONS)]
        )
        return _log_result(_parse_freeform(text))


class PortNormalizationAgent:
    """港口识别的模型调用：按 provider 顺序试（主 → 兜底）。

    与主抽取链路共用 provider 列表与熔断策略；全部 provider 失败时抛出最后一个
    异常，`PortNormalizationService` 照旧把相关字段转人工审核。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.clients = [
            PortProviderClient(provider) for provider in build_providers(settings)
        ]
        self.circuit = ProviderCircuit(
            failure_threshold=settings.model_failover_failure_threshold,
            cooldown_seconds=settings.model_failover_cooldown_seconds,
        )

    async def suggest(
        self,
        system_prompt: str,
        items: list[PortSuggestionInput],
    ) -> PortCodeSuggestionResult:
        """为每个待识别字段给出"候选选择"或"规范英文名"。"""

        lines: list[str] = []
        for item in items:
            lines.append(f"- field_key={item.field_key}，原文：{item.raw_value}")
            if item.candidates:
                rendered = "、".join(
                    f"{candidate.three_code}({candidate.english_name},{candidate.country_code})"
                    for candidate in item.candidates[:8]
                )
                lines.append(f"  主数据候选：{rendered}")
            else:
                lines.append("  主数据候选：无")
        messages = [
            ("system", system_prompt),
            ("human", "待识别的港口字段：\n" + "\n".join(lines)),
        ]

        last_error: Exception | None = None
        for index, client in enumerate(self.clients):
            has_next = index + 1 < len(self.clients)
            if has_next and self.circuit.is_open(client.provider.name):
                logger.warning(
                    "港口识别：provider %s 处于熔断期，跳过并直接走兜底",
                    client.provider.name,
                )
                continue
            try:
                result = await client.suggest(messages)
            except Exception as exc:  # noqa: BLE001 - 交给下一个 provider
                self.circuit.record_failure(client.provider.name)
                last_error = exc
                if has_next:
                    logger.warning(
                        "港口识别：provider %s 调用失败，改用兜底 provider %s：%s",
                        client.provider.name,
                        self.clients[index + 1].provider.name,
                        exc,
                    )
                continue
            self.circuit.record_success(client.provider.name)
            if index > 0:
                logger.warning(
                    "港口识别：本次已由兜底 provider %s 完成", client.provider.name
                )
            return result
        assert last_error is not None
        raise last_error


def _parse_freeform(text: str) -> PortCodeSuggestionResult:
    """解析自由文本输出为结构化结果（容忍代码块与前后缀文字）。"""

    payload = extract_json_payload(text)
    try:
        return PortCodeSuggestionResult.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"港口识别输出不符合约定结构：{exc}") from exc


def _log_result(result: Any) -> PortCodeSuggestionResult:
    parsed = (
        result
        if isinstance(result, PortCodeSuggestionResult)
        else PortCodeSuggestionResult.model_validate(result)
    )
    logger.debug(
        "港口识别结果：%s",
        json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False),
    )
    return parsed
