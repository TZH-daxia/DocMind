"""港口三字码归一化的模型调用。"""

import json
import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from app.agent.deepseek_extractor import DeepSeekExtractionAgent
from app.config import Settings
from app.schemas.port import (
    PortCodeProposalResult,
    PortFieldInput,
)

logger = logging.getLogger(__name__)

# 自由文本回退时的输出格式要求（结构化 response_format 不可用时使用）
PROPOSAL_FORMAT_INSTRUCTIONS = (
    '输出必须是合法 JSON 对象：{"proposals": '
    '[{"field_key": "...", "three_code": "...", "reason": "..."}]}，'
    "每个输入字段都必须有对应条目，不要输出 JSON 以外的任何文字或代码块标记。"
)
class PortNormalizationAgent:
    """港口归一化的模型调用：生成三字码候选。

    优先使用结构化输出（json_schema → function_calling），模型不支持时
    自动降级为自由文本 + JSON 解析，与主抽取链路的容错策略一致。
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
        # 结构化输出方法在构造期不可验证（400 错误发生在调用时），
        # 因此先记录候选方法，调用失败再降级
        self._structured_method: str | None = "json_schema"

    async def propose_codes(
        self,
        system_prompt: str,
        fields: list[PortFieldInput],
    ) -> PortCodeProposalResult:
        """基于模型自身知识，为每个港口原文给出三字码候选。"""

        items = "\n".join(
            f"- field_key={item.field_key}，原文：{item.raw_value}" for item in fields
        )
        messages = [
            ("system", system_prompt),
            ("human", f"待编码的港口字段：\n{items}"),
        ]
        if self._structured_method is not None:
            try:
                result = await self.model.with_structured_output(
                    PortCodeProposalResult, method=self._structured_method
                ).ainvoke(messages)
                return self._log_result("三字码候选", result)
            except Exception as exc:  # noqa: BLE001 - 降级为自由文本
                logger.debug(
                    "结构化输出 %s 不可用，回退自由文本：%s", self._structured_method, exc
                )
                self._structured_method = None
        text = await self.text_chain.ainvoke(
            [*messages, ("human", PROPOSAL_FORMAT_INSTRUCTIONS)]
        )
        return self._log_result("三字码候选", self._parse_freeform(text, PortCodeProposalResult))

    @staticmethod
    def _parse_freeform(text: str, schema: type) -> PortCodeProposalResult:
        """解析自由文本输出为结构化结果（容忍代码块与前后缀文字）。"""

        payload = DeepSeekExtractionAgent._extract_json_payload(text)
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"港口归一化输出不符合约定结构：{exc}") from exc

    @staticmethod
    def _log_result(label: str, result: PortCodeProposalResult) -> PortCodeProposalResult:
        logger.debug("%s：%s", label, json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
        return result
