"""港口归一化的模型调用：只在本地匹配无法定论时使用。

模型不再直接产出三字码，只产出两类**可被本地校验**的中间量：
- 有候选时：从候选里选一个（`chosen_code`）；
- 无候选时：给出规范英文港口名（`english_name`），由主数据索引映射成三字码。
这样模型幻觉会落到"主数据匹配不到"，而不是变成一个存在但无关的错码。
"""

import json
import logging
from typing import Any, Literal

from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from app.agent.deepseek_extractor import DeepSeekExtractionAgent
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
# 不支持 OpenAI 的 json_schema，因此走 Tool Calls（function_calling）
StructuredMethod = Literal["json_schema", "function_calling"]


class PortNormalizationAgent:
    """港口识别的模型调用：消歧与规范化。

    优先使用结构化输出（Tool Calls），模型不支持时自动降级为自由文本 +
    JSON 解析，与主抽取链路的容错策略一致。
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        self.model = ChatOpenAI(
            api_key=SecretStr(settings.deepseek_api_key),
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            temperature=settings.deepseek_temperature,
            timeout=settings.deepseek_timeout_seconds,
            max_retries=settings.deepseek_max_retries,
            extra_body={
                "thinking": {
                    "type": "enabled" if settings.deepseek_thinking else "disabled"
                }
            },
        )
        self.text_chain = self.model | StrOutputParser()
        # 结构化输出在构造期不可验证（400 错误发生在调用时），因此先记录
        # 方法，调用失败再降级为自由文本
        self._structured_method: StructuredMethod | None = "function_calling"

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
        if self._structured_method is not None:
            try:
                # tool_choice="auto"：DeepSeek 思考模式不支持具名 tool_choice
                # （langchain 默认绑定具名，会被拒），必须放行为自动选择
                result = await self.model.with_structured_output(
                    PortCodeSuggestionResult,
                    method=self._structured_method,
                    tool_choice="auto",
                ).ainvoke(messages)
                return self._log_result(result)
            except Exception as exc:  # noqa: BLE001 - 降级为自由文本
                logger.debug(
                    "结构化输出 %s 不可用，回退自由文本：%s", self._structured_method, exc
                )
                self._structured_method = None
        text = await self.text_chain.ainvoke(
            [*messages, ("human", PROPOSAL_FORMAT_INSTRUCTIONS)]
        )
        return self._log_result(self._parse_freeform(text))

    @staticmethod
    def _parse_freeform(text: str) -> PortCodeSuggestionResult:
        """解析自由文本输出为结构化结果（容忍代码块与前后缀文字）。"""

        payload = DeepSeekExtractionAgent._extract_json_payload(text)
        try:
            return PortCodeSuggestionResult.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"港口识别输出不符合约定结构：{exc}") from exc

    @staticmethod
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
