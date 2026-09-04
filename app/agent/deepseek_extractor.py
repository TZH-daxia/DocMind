import base64
import json
import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.schemas.analysis import ExtractionEnvelope, FieldCandidate

logger = logging.getLogger(__name__)

HUMAN_PROMPT_TEMPLATE = """请处理一份托书的两个独立 MinerU 文件输入。
订单上下文：
{order_context}

确定性候选：
{deterministic_candidates}

文件一：{full_markdown_name}
文件一内容：
{full_markdown}

文件二：{structured_json_name}
文件二内容：
{structured_json}

图片视觉补充内容：
{vlm_image_content}

输出格式要求：
{format_instructions}

文件一和文件二可能存在重复内容。请在模型内部去重和交叉核对，
不要因重复内容生成重复候选；如果出现冲突，请保留冲突状态。"""


@dataclass(frozen=True)
class ImageInput:
    """提供给多模态模型的图片字节数据。"""

    content: bytes
    media_type: str
    name: str


class DeepSeekExtractionAgent:
    """使用支持图片识别的 DeepSeek 模型抽取订单字段。"""

    def __init__(self, settings: Settings) -> None:
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        self.settings = settings
        self.model = ChatOpenAI(
            api_key=SecretStr(settings.deepseek_api_key),
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            temperature=settings.deepseek_temperature,
            timeout=settings.deepseek_timeout_seconds,
            max_retries=settings.deepseek_max_retries,
        )
        self.output_parser = PydanticOutputParser(pydantic_object=ExtractionEnvelope)
        self.extraction_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", "{system_prompt}"),
                    ("human", HUMAN_PROMPT_TEMPLATE),
                    MessagesPlaceholder("image_messages", optional=True),
                ]
            )
            | self.model
            | StrOutputParser()
            | self.output_parser
        )
        self.vision_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", "{vision_prompt}"),
                    (
                        "human",
                        (
                            "图片文件名：{image_names}\n"
                            "请读取下面的托书图片，提取图片中可确认的文字、表格、字段标签、"
                            "数值、日期和版面关系。只描述图片中明确可见的内容，不要猜测或补全。"
                        ),
                    ),
                    MessagesPlaceholder("image_messages"),
                ]
            )
            | self.model
        )

    async def describe_images(
        self,
        vision_prompt: str,
        images: list[ImageInput],
    ) -> str:
        """使用视觉模型读取图片并生成可追溯的视觉内容描述。"""

        if not images:
            return ""
        image_content = self._build_image_content(images)
        response = await self.vision_chain.ainvoke(
            {
                "vision_prompt": vision_prompt,
                "image_names": "、".join(image.name for image in images[:6]),
                "image_messages": [HumanMessage(content=image_content)],
            }
        )
        return self._message_text(response)

    async def extract(
        self,
        system_prompt: str,
        full_markdown: str,
        structured_json: str,
        full_markdown_name: str,
        structured_json_name: str,
        deterministic_candidates: list[FieldCandidate],
        context: dict[str, Any],
        vlm_image_content: str,
    ) -> list[FieldCandidate]:
        """基于解析文本和图片证据返回结构化字段候选。"""

        response = await self.extraction_chain.ainvoke(
            {
                "system_prompt": system_prompt,
                "order_context": json.dumps(context, ensure_ascii=False),
                "deterministic_candidates": json.dumps(
                    [candidate.model_dump(mode="json") for candidate in deterministic_candidates],
                    ensure_ascii=False,
                ),
                "full_markdown_name": full_markdown_name,
                "full_markdown": full_markdown[:50000],
                "structured_json_name": structured_json_name,
                "structured_json": structured_json[:50000],
                "vlm_image_content": vlm_image_content or "无图片视觉补充内容。",
                "format_instructions": self.output_parser.get_format_instructions(),
                "image_messages": [],
            }
        )
        return self._parse_candidates(response)

    @staticmethod
    def _build_image_content(images: list[ImageInput]) -> list[str | dict[Any, Any]]:
        """将图片转换为多模态消息内容。"""

        image_content: list[str | dict[Any, Any]] = []
        for image in images[:6]:
            encoded = base64.b64encode(image.content).decode("ascii")
            image_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image.media_type};base64,{encoded}",
                    },
                }
            )
        return image_content

    @staticmethod
    def _message_text(response: Any) -> str:
        """提取模型响应中的文本内容。"""

        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("text")
            )
        return str(content)

    @staticmethod
    def _parse_candidates(response: Any) -> list[FieldCandidate]:
        """将模型结构化输出转换为经过校验的字段候选。"""

        if isinstance(response, ExtractionEnvelope):
            return list(response.candidates)
        payload = response.model_dump(mode="json") if hasattr(response, "model_dump") else response
        raw_candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
        candidates: list[FieldCandidate] = []
        for raw_candidate in raw_candidates:
            try:
                candidates.append(FieldCandidate.model_validate(raw_candidate))
            except ValidationError:
                logger.warning("Ignoring one invalid DeepSeek candidate")
        return candidates
