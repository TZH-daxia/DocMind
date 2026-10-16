"""托书订单字段抽取（支持多 provider 兜底）。

主路径是 DeepSeek；`DOCMIND_FALLBACK_API_KEY` 配好后，主 provider 整轮重试
仍失败时由兜底 provider（默认阿里云百炼 qwen3.8-flash）接手。两条链路的
提示词、schema、解析与降级规则完全共用，差异只在 provider 参数（见
app/agent/providers.py），因此兜底结果与主结果同构，下游无感。

模块与类名沿用历史的 `DeepSeek*`，只是为了少动 import 面；实际已与"某一家
模型"解耦。
"""

import base64
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from openai import BadRequestError
from pydantic import ValidationError

from app.agent.circuit import ProviderCircuit
from app.agent.model_call import ProviderCall
from app.agent.providers import ModelProvider, build_providers
from app.config import Settings
from app.schemas.analysis import (
    Evidence,
    ExtractionEnvelope,
    FieldCandidate,
    PoOrderExtraction,
)
from app.schemas.po_order import PO_ORDER_KEYS

logger = logging.getLogger(__name__)

# 模型偶发给输出套一层包装（{"result": {...}} 等），解析前先尝试剥掉
PAYLOAD_WRAPPER_KEYS = ("result", "data", "fields", "output", "extraction")


class EmptyExtractionError(RuntimeError):
    """模型未产出任何有效字段候选。

    典型场景：模型返回空对象、或把结果包了一层（`{"result": {...}}`），此时
    `parse_flat` 会把它们静默解析成 13 个 missing 候选，历史上表现为"任务成功
    但结果全空"。抛出本异常后由调用方决定重试或显式标记失败，不再给用户一份
    看似正常的空结果。
    """


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
STRUCTURED_HUMAN_TEMPLATE = """请仅依据下面的视觉理解文档，为下列 13 个订单字段逐一填写提取值。

订单上下文：
{order_context}

视觉理解文档（唯一字段来源）：
{vlm_image_content}

要求：
- 输出结构已由系统强制约定（共 13 个字段），每个字段都必须出现，禁止遗漏；
- 文档没有明确文字依据的字段：value 填 null、status 填 "missing"；
- 文档中标注“无法确认”的段落一律不要生成值；
- 每个字段的 evidence 必须是逐字引用视觉理解文档的原文片段字符串列表；
- 直接按结构填充，不要输出 JSON 信封或任何额外文字。"""

VISION_HUMAN_TEMPLATE = (
    "图片顺序：{image_names}\n"
    "第一张是托书整页图，用于理解版面关系；其后是同一页按"
    "左上/右上/左下/右下切分的四块局部放大图，用于逐字转写"
    "小字号文本。请先描述整页版面，再逐张转写放大图中的"
    "全部可见文字（公司名、地址、电话、邮箱、单号、数值等必须与图片像素"
    "完全一致，禁止按语言习惯补全；无法辨认的字符标记“无法确认”）。"
)


@dataclass(frozen=True)
class ImageInput:
    """提供给多模态模型的图片字节数据。"""

    content: bytes
    media_type: str
    name: str


def build_format_instructions() -> str:
    """扁平键模板：明确要求模型逐个字段填写，缺一不可。"""

    return (
        f"输出必须是合法 JSON 对象，顶层键为以下 {len(PO_ORDER_KEYS)} 个字段名（一个都不能少）："
        + ", ".join(PO_ORDER_KEYS)
        + "。"
        "每个键的值都是一个对象："
        '{"value": <提取值或 null>, "status": "normalized"|"missing"|"needs_review"|..., '
        '"confidence": 0.0~1.0, "evidence": [{"quote": "..."}]}。'
        "文档没有明确文字依据的字段：value 填 null、status 填 \"missing\"。"
        "不要包含 JSON 以外的任何文字或代码块标记。"
    )


class ProviderClient:
    """单个 provider 的三条链：结构化抽取、free-form 抽取、图片识别。"""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider
        self.model = provider.build_chat_model()
        # 图片识别单独一个客户端：该环节是逐字转写，不需要推理，默认关闭 thinking
        self.vision_model = provider.build_chat_model(vision=True)
        self._format_instructions = build_format_instructions()
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
        # 结构化抽取链：用 PoOrderExtraction 的 schema 约束 13 字段。
        # 1) DeepSeek 的 response_format 只支持 text / json_object，不支持 OpenAI 的
        #    json_schema，因此用 function_calling；千问两种都支持，兜底默认也用
        #    function_calling，使主/兜底走同一条解析与降级路径；
        # 2) function_calling 必须显式传 tool_choice="auto"：langchain 默认会绑定具名
        #    tool_choice，而 DeepSeek 思考模式会直接返回 400 "Thinking mode does not
        #    support this tool_choice"（kwargs 在 with_structured_output 内最后展开）；
        # 3) 不可用只能在调用期暴露（400 发生在 ainvoke），由 extract() 捕获后降级。
        self.structured_chain: Runnable[Any, PoOrderExtraction] | None = None
        if provider.structured_method is not None:
            structured_kwargs: dict[str, Any] = (
                {"tool_choice": "auto"}
                if provider.structured_method == "function_calling"
                else {}
            )
            self.structured_chain = cast(
                Runnable[Any, PoOrderExtraction],
                ChatPromptTemplate.from_messages(
                    [
                        ("system", "{system_prompt}"),
                        ("human", STRUCTURED_HUMAN_TEMPLATE),
                        MessagesPlaceholder("image_messages", optional=True),
                    ]
                )
                | self.model.with_structured_output(
                    PoOrderExtraction,
                    method=cast(Any, provider.structured_method),
                    **structured_kwargs,
                ),
            )
        self.vision_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", "{vision_prompt}"),
                    ("human", VISION_HUMAN_TEMPLATE),
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
        """使用本 provider 的视觉模型读取图片并生成可追溯的视觉内容描述。"""

        if not images:
            return ""
        image_content = build_image_content(images)
        response = await self.vision_chain.ainvoke(
            {
                "vision_prompt": vision_prompt,
                "image_names": "、".join(image.name for image in images[:6]),
                "image_messages": [HumanMessage(content=image_content)],
            }
        )
        return message_text(response)

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
        # 1) 优先结构化输出：schema 硬约束 13 字段必填，从根上杜绝漏字段。
        if self.structured_chain is not None:
            try:
                result = await self.structured_chain.ainvoke(variables)
                candidates = schema_to_candidates(result)
                logger.debug("结构化抽取得到候选数：%s", len(candidates))
                if has_any_value(candidates):
                    return ensure_all_fields(candidates)
                logger.warning("结构化抽取未得到任何字段值，回退 free-form")
            except Exception as exc:  # noqa: BLE001 - 结构化失败则回退
                # 只有「本模型不接受这种结构化方式」（400）才**记住**降级：那是稳定的
                # 能力缺失，记住可以把每份文档都白试一次的开销省掉。
                # 网络/5xx/超时只是暂时故障，绝不能记成"结构化不可用"——否则主模型
                # 恢复后也永远走不了结构化。这类异常照旧回退 free-form（与改造前一致），
                # free-form 同样失败时由上层重试并交给兜底 provider。
                if isinstance(exc, BadRequestError):
                    self.structured_chain = None
                    logger.warning(
                        "结构化抽取方式不被支持，本 provider 后续改走 free-form："
                        "provider=%s，%s",
                        self.provider.name,
                        exc,
                    )
                else:
                    logger.warning(
                        "结构化抽取异常（暂时性，不降级），回退 free-form：provider=%s，%s",
                        self.provider.name,
                        exc,
                    )
        # 2) 回退 free-form：解析模型文本输出（扁平 13 键或旧式信封）。
        #    空结果（合法 JSON 但无任何字段）与解析失败同等对待：最多重试一次，
        #    仍无字段则抛 EmptyExtractionError，避免静默产出全空结果。
        last_error: Exception | None = None
        for attempt in range(2):
            text = await self.extraction_chain.ainvoke(variables)
            logger.debug(
                "%s 抽取原始输出（第 %s 次，长度 %s）：%s",
                self.provider.name,
                attempt + 1,
                len(text),
                text[:4000],
            )
            try:
                candidates = parse_candidates(text)
            except ValueError as exc:
                last_error = exc
                logger.warning(
                    "%s 输出解析失败（第 %s 次）：%s",
                    self.provider.name,
                    attempt + 1,
                    exc,
                )
                continue
            if not has_any_value(candidates):
                last_error = EmptyExtractionError(
                    f"模型输出不含任何字段（输出长度 {len(text)}）"
                )
                logger.warning(
                    "%s 抽取结果为空（第 %s 次，输出长度 %s），准备重试",
                    self.provider.name,
                    attempt + 1,
                    len(text),
                )
                continue
            logger.debug("%s 抽取解析得到候选数：%s", self.provider.name, len(candidates))
            return ensure_all_fields(candidates)
        raise EmptyExtractionError(
            f"EMPTY_EXTRACTION: 模型输出无法产生有效字段候选：{last_error}"
        ) from last_error


class ExtractionAgent:
    """按 provider 顺序调用模型：主 provider 整轮失败后由兜底 provider 接手。

    对外只暴露两件事：
    - `vision_calls` / `extract_calls`：给 `call_with_failover` 的按序调用列表；
    - `describe_images` / `extract`：只走主 provider，供直接调用与测试替换。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.clients = [
            ProviderClient(provider) for provider in build_providers(settings)
        ]
        self.circuit = ProviderCircuit(
            failure_threshold=settings.model_failover_failure_threshold,
            cooldown_seconds=settings.model_failover_cooldown_seconds,
        )
        logger.info(
            "订单抽取 provider 链：%s（兜底%s）",
            " -> ".join(
                f"{client.provider.name}/{client.provider.model}" for client in self.clients
            ),
            "已启用" if len(self.clients) > 1 else "未配置",
        )

    @property
    def provider_names(self) -> list[str]:
        """当前 provider 顺序（第一个是主 provider）。"""

        return [client.provider.name for client in self.clients]

    def vision_calls(
        self, vision_prompt: str, images: list[ImageInput]
    ) -> list[ProviderCall]:
        """图片识别的按序调用列表：主 provider 在前，兜底在后。"""

        return [
            ProviderCall(
                provider=client.provider.name,
                label=(
                    f"VLM 图片识别[{client.provider.name}/{client.provider.vision_model}]"
                ),
                factory=self._vision_factory(index, client, vision_prompt, images),
                timeout_seconds=self.settings.model_vision_timeout_seconds,
                retries=client.provider.call_retries,
            )
            for index, client in enumerate(self.clients)
        ]

    def extract_calls(
        self,
        system_prompt: str,
        vlm_image_content: str,
        context: dict[str, Any],
    ) -> list[ProviderCall]:
        """字段抽取的按序调用列表：主 provider 在前，兜底在后。"""

        return [
            ProviderCall(
                provider=client.provider.name,
                label=f"字段抽取[{client.provider.name}/{client.provider.model}]",
                factory=self._extract_factory(
                    index, client, system_prompt, vlm_image_content, context
                ),
                timeout_seconds=self.settings.model_extract_timeout_seconds,
                retries=client.provider.call_retries,
            )
            for index, client in enumerate(self.clients)
        ]

    def _vision_factory(
        self,
        index: int,
        client: ProviderClient,
        vision_prompt: str,
        images: list[ImageInput],
    ) -> Callable[[], Awaitable[str]]:
        """构造某个 provider 的图片识别工厂。

        主 provider 走 agent 自己的 `describe_images`：测试与调用方替换该方法时
        仍然生效（兜底 provider 只能走各自 client，替换主入口不该影响它）。
        client 走参数传入，避免闭包捕获循环变量。
        """

        if index == 0:

            async def factory() -> str:
                return await self.describe_images(vision_prompt, images)

        else:

            async def factory() -> str:
                return await client.describe_images(vision_prompt, images)

        return factory

    def _extract_factory(
        self,
        index: int,
        client: ProviderClient,
        system_prompt: str,
        vlm_image_content: str,
        context: dict[str, Any],
    ) -> Callable[[], Awaitable[list[FieldCandidate]]]:
        """构造某个 provider 的字段抽取工厂（主 provider 走 agent 自己的入口）。"""

        if index == 0:

            async def factory() -> list[FieldCandidate]:
                return await self.extract(
                    system_prompt=system_prompt,
                    vlm_image_content=vlm_image_content,
                    context=context,
                )

        else:

            async def factory() -> list[FieldCandidate]:
                return await client.extract(
                    system_prompt=system_prompt,
                    vlm_image_content=vlm_image_content,
                    context=context,
                )

        return factory

    async def describe_images(
        self,
        vision_prompt: str,
        images: list[ImageInput],
    ) -> str:
        """用主 provider 识别图片（兜底链路见 `vision_calls`）。"""

        return await self.clients[0].describe_images(vision_prompt, images)

    async def extract(
        self,
        system_prompt: str,
        vlm_image_content: str,
        context: dict[str, Any],
    ) -> list[FieldCandidate]:
        """用主 provider 抽取字段（兜底链路见 `extract_calls`）。"""

        return await self.clients[0].extract(system_prompt, vlm_image_content, context)


def schema_to_candidates(result: PoOrderExtraction) -> list[FieldCandidate]:
    """把结构化抽取结果（13 字段）转换为下游通用的 list[FieldCandidate]。"""

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


def build_image_content(images: list[ImageInput]) -> list[str | dict[Any, Any]]:
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


def message_text(response: Any) -> str:
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


def has_any_value(candidates: list[FieldCandidate]) -> bool:
    """是否至少有一个带值的候选（全 missing 视为模型没产出内容）。"""

    return any(candidate.value is not None for candidate in candidates)


def unwrap_payload(payload: Any) -> Any:
    """剥掉模型偶发添加的包装层（如 {"result": {...}}）。"""

    if not isinstance(payload, dict) or len(payload) != 1:
        return payload
    (only_key, only_value), = payload.items()
    if isinstance(only_value, dict) and str(only_key).lower() in PAYLOAD_WRAPPER_KEYS:
        return only_value
    return payload


def parse_candidates(text: str) -> list[FieldCandidate]:
    """将模型文本输出容错解析为字段候选（容忍代码块与前后缀文字）。

    支持两种结构：扁平 13 键对象（推荐）与旧式 {"candidates": [...]} 信封。
    输出里连一个订单字段都没有时（空对象、包装层未识别）直接判为解析失败，
    交由调用方重试——否则 parse_flat 会静默产出 13 个 missing 候选。
    """

    payload = unwrap_payload(extract_json_payload(text))
    if isinstance(payload, dict) and "candidates" not in payload:
        if not any(key in payload for key in PO_ORDER_KEYS):
            raise ValueError(f"输出中没有任何订单字段（顶层键：{list(payload)[:10]}）")
        return parse_flat(payload)
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
            logger.warning("Ignoring one invalid candidate")
    return candidates


def parse_flat(payload: dict[str, Any]) -> list[FieldCandidate]:
    """解析扁平 13 键对象：每个键的值是一个字段提取值对象。"""

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


def ensure_all_fields(candidates: list[FieldCandidate]) -> list[FieldCandidate]:
    """保证 13 个字段全部出现在候选列表中（模型漏输出的按 missing 补齐）。

    与模型能力无关：即使模型漏字段，下游 build_result 也能拿到完整结构。
    """

    present = {candidate.field_key for candidate in candidates}
    for key in PO_ORDER_KEYS:
        if key not in present:
            candidates.append(FieldCandidate(field_key=key, value=None, status="missing"))
    return candidates


def extract_json_payload(text: str) -> dict[str, Any]:
    """从模型文本中提取 JSON 对象：剥掉代码块标记和 JSON 前后的文字。"""

    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("输出中没有找到 JSON 对象")
    return json.loads(cleaned[start : end + 1])
