import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.schemas.analysis import (
    Evidence,
    ExtractionEnvelope,
    FieldCandidate,
    PoOrderExtraction,
)
from app.schemas.po_order import PO_ORDER_KEYS

logger = logging.getLogger(__name__)

HUMAN_PROMPT_TEMPLATE = """请仅依据下面的视觉理解文档抽取订单字段候选。
订单上下文：
{order_context}

视觉理解文档（唯一字段来源）：
{vlm_image_content}

输出格式要求：
{format_instructions}

约束：
- 只从视觉理解文档中有明确文字依据的内容生成候选，文档没有的字段不要编造；
- 文档中标注“无法确认”的段落一律不要生成候选；
- 每个候选的 evidence 必须逐字引用视觉理解文档中的原文片段。"""

# 结构化抽取专用 human 模板：强调“逐字段填写”，输出形状由 PoOrderExtraction 强制，
# 不再要求模型手写 JSON 信封，避免其漏输出字段。
STRUCTURED_HUMAN_TEMPLATE = """请仅依据下面的视觉理解文档，为下列 12 个订单字段逐一填写提取值。

订单上下文：
{order_context}

视觉理解文档（唯一字段来源）：
{vlm_image_content}

要求：
- 输出结构已由系统强制约定（共 12 个字段），每个字段都必须出现，禁止遗漏；
- 文档没有明确文字依据的字段：value 填 null、status 填 "missing"；
- 文档中标注“无法确认”的段落一律不要生成值；
- 每个字段的 evidence 必须是逐字引用视觉理解文档的原文片段字符串列表；
- 直接按结构填充，不要输出 JSON 信封或任何额外文字。"""


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
            extra_body={
                "thinking": {
                    "type": "enabled" if settings.deepseek_thinking else "disabled"
                }
            },
        )
        # 图片识别单独一个客户端：该环节是逐字转写，不需要推理，默认关闭 thinking
        self.vision_model = ChatOpenAI(
            api_key=SecretStr(settings.deepseek_api_key),
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            temperature=settings.deepseek_temperature,
            timeout=settings.deepseek_timeout_seconds,
            max_retries=settings.deepseek_max_retries,
            extra_body={
                "thinking": {
                    "type": (
                        "enabled" if settings.deepseek_vision_thinking else "disabled"
                    )
                }
            },
        )
        self.output_parser = None
        # 扁平 12 键模板：明确要求模型逐个字段填写，缺一不可（弱模型也能按骨架填充）。
        self._format_instructions = (
            "输出必须是合法 JSON 对象，顶层键为以下 12 个字段名（一个都不能少）："
            "sfg, mdg, ybpiece, ybweight, ybvolume, inwageallinprice, hbrq, fid, "
            "shipper, consignee, chinesepm, englishpm。"
            "每个键的值都是一个对象："
            '{"value": <提取值或 null>, "status": "normalized"|"missing"|"needs_review"|..., '
            '"confidence": 0.0~1.0, "evidence": [{"quote": "..."}]}。'
            "文档没有明确文字依据的字段：value 填 null、status 填 \"missing\"。"
            "不要包含 JSON 以外的任何文字或代码块标记。"
        )
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
        )
        # 结构化抽取链：用 PoOrderExtraction 的 JSON schema 硬约束 12 字段。
        # 优先 json_schema（response_format，DeepSeek 支持更好且 strict 强制必填），
        # 失败再退到 function_calling；两者都不可用则禁用并回退 free-form。
        self.structured_chain = None
        for method in ("json_schema", "function_calling"):
            try:
                self.structured_chain = (
                    ChatPromptTemplate.from_messages(
                        [
                            ("system", "{system_prompt}"),
                            ("human", STRUCTURED_HUMAN_TEMPLATE),
                            MessagesPlaceholder("image_messages", optional=True),
                        ]
                    )
                    | self.model.with_structured_output(PoOrderExtraction, method=method)
                )
                break
            except Exception as exc:  # noqa: BLE001 - 该结构化方法不可用
                logger.debug("结构化输出方法 %s 不可用：%s", method, exc)
        if self.structured_chain is None:
            logger.debug("结构化输出不可用，将仅使用 free-form 抽取。")
        self.vision_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", "{vision_prompt}"),
                    (
                        "human",
                        (
                            "图片顺序：{image_names}\n"
                            "第一张是托书整页图，用于理解版面关系；其后是同一页按"
                            "左上/右上/左下/右下切分的四块局部放大图，用于逐字转写"
                            "小字号文本。请先描述整页版面，再逐张转写放大图中的"
                            "全部可见文字（公司名、地址、电话、邮箱、单号、数值等必须与图片像素"
                            "完全一致，禁止按语言习惯补全；无法辨认的字符标记“无法确认”）。"
                        ),
                    ),
                    MessagesPlaceholder("image_messages"),
                ]
            )
            | self.vision_model
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
        vlm_image_content: str,
        context: dict[str, Any],
    ) -> list[FieldCandidate]:
        """仅基于 VLM 视觉理解文档返回结构化字段候选。"""

        variables = {
            "system_prompt": system_prompt,
            "order_context": json.dumps(context, ensure_ascii=False),
            "vlm_image_content": vlm_image_content or "无视觉理解内容。",
            "format_instructions": self._format_instructions,
            "image_messages": [],
        }
        # 1) 优先结构化输出：schema 硬约束 12 字段必填，从根上杜绝漏字段。
        if self.structured_chain is not None:
            try:
                result = await self.structured_chain.ainvoke(variables)
                candidates = self._schema_to_candidates(result)
                logger.debug("结构化抽取得到候选数：%s", len(candidates))
                return self._ensure_all_fields(candidates)
            except Exception as exc:  # noqa: BLE001 - 结构化失败则回退
                logger.debug("结构化抽取失败，回退 free-form：%s", exc)
        # 2) 回退 free-form：解析模型文本输出（扁平 12 键或旧式信封）。
        last_error: ValueError | None = None
        for attempt in range(2):
            text = await self.extraction_chain.ainvoke(variables)
            logger.debug(
                "DeepSeek 抽取原始输出（第 %s 次，长度 %s）：%s",
                attempt + 1,
                len(text),
                text[:4000],
            )
            try:
                candidates = self._parse_candidates(text)
            except ValueError as exc:
                last_error = exc
                logger.warning("DeepSeek 输出解析失败（第 %s 次）：%s", attempt + 1, exc)
            else:
                logger.debug("DeepSeek 抽取解析得到候选数：%s", len(candidates))
                return self._ensure_all_fields(candidates)
        raise ValueError(f"模型输出无法解析为 JSON：{last_error}")

    @staticmethod
    def _schema_to_candidates(result: PoOrderExtraction) -> list[FieldCandidate]:
        """把结构化抽取结果（12 字段）转换为下游通用的 list[FieldCandidate]。"""

        candidates: list[FieldCandidate] = []
        for key in PO_ORDER_KEYS:
            field = getattr(result, key)
            evidence = [Evidence(quote=quote) for quote in (field.evidence or [])]
            candidates.append(
                FieldCandidate(
                    field_key=key,
                    value=field.value,
                    status=field.status,
                    confidence=field.confidence,
                    evidence=evidence,
                )
            )
        return candidates

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
    def _parse_candidates(text: str) -> list[FieldCandidate]:
        """将模型文本输出容错解析为字段候选（容忍代码块与前后缀文字）。

        支持两种结构：扁平 12 键对象（推荐）与旧式 {"candidates": [...]} 信封。
        """

        payload = DeepSeekExtractionAgent._extract_json_payload(text)
        if isinstance(payload, dict) and "candidates" not in payload:
            return DeepSeekExtractionAgent._parse_flat(payload)
        try:
            envelope = ExtractionEnvelope.model_validate(payload)
            return list(envelope.candidates)
        except ValidationError:
            pass
        raw_candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
        candidates: list[FieldCandidate] = []
        for raw_candidate in raw_candidates:
            try:
                candidates.append(FieldCandidate.model_validate(raw_candidate))
            except ValidationError:
                logger.warning("Ignoring one invalid DeepSeek candidate")
        return candidates

    @staticmethod
    def _parse_flat(payload: dict[str, Any]) -> list[FieldCandidate]:
        """解析扁平 12 键对象：每个键的值是一个字段提取值对象。"""

        candidates: list[FieldCandidate] = []
        for key in PO_ORDER_KEYS:
            raw = payload.get(key)
            if not isinstance(raw, dict):
                candidates.append(
                    FieldCandidate(
                        field_key=key,
                        value=raw,
                        status="missing" if raw is None else "needs_review",
                    )
                )
                continue
            evidence = [
                Evidence(quote=ev.get("quote"))
                for ev in (raw.get("evidence") or [])
                if isinstance(ev, dict)
            ]
            try:
                candidates.append(
                    FieldCandidate(
                        field_key=key,
                        value=raw.get("value"),
                        status=raw.get("status", "needs_review"),
                        confidence=raw.get("confidence", 0.0),
                        evidence=evidence,
                    )
                )
            except ValidationError:
                candidates.append(
                    FieldCandidate(field_key=key, value=raw.get("value"), status="needs_review")
                )
        return candidates

    @staticmethod
    def _ensure_all_fields(candidates: list[FieldCandidate]) -> list[FieldCandidate]:
        """保证 12 个字段全部出现在候选列表中（模型漏输出的按 missing 补齐）。

        与模型能力无关：即使模型漏字段，下游 build_result 也能拿到完整结构。
        """

        present = {candidate.field_key for candidate in candidates}
        for key in PO_ORDER_KEYS:
            if key not in present:
                candidates.append(FieldCandidate(field_key=key, value=None, status="missing"))
        return candidates

    @staticmethod
    def _extract_json_payload(text: str) -> dict[str, Any]:
        """从模型文本中提取 JSON 对象：剥掉代码块标记和 JSON 前后的文字。"""

        cleaned = text.strip()
        fenced = re.search(r"```(?:json)?\s*(.+?)```", cleaned, flags=re.DOTALL)
        if fenced:
            cleaned = fenced.group(1).strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("输出中没有找到 JSON 对象")
        return json.loads(cleaned[start : end + 1])
