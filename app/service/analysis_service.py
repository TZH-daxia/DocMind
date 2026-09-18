import asyncio
import json
import logging
import mimetypes
import re
from collections import Counter
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from app.agent.deepseek_extractor import (
    DeepSeekExtractionAgent,
    EmptyExtractionError,
    ImageInput,
)
from app.collector.document_renderer import LocalDocumentRenderer
from app.collector.document_type_guard import (
    MISMATCH_MESSAGE,
    DocumentTypeMismatchError,
    build_content_hint,
    detect_document_type,
)
from app.collector.evidence_locator import (
    DocumentTextIndex,
    LocatedBox,
    context_needles,
    normalize_needle,
    quote_segments,
    resolve_source_pdf,
)
from app.config import Settings
from app.prompts.loader import (
    load_po_order_extraction_prompt,
    load_po_order_vision_prompt,
)
from app.schemas.analysis import (
    AnalysisContext,
    AnalysisResult,
    Evidence,
    EvidenceLocation,
    FieldCandidate,
    FieldMetadata,
    ValidationResult,
)
from app.schemas.file import UploadedDocument
from app.schemas.po_order import (
    CONTEXT_ONLY_KEYS,
    PARTY_KEYS,
    PO_ORDER_KEYS,
    PORT_FIELD_KEYS,
)
from app.service.customer_service import CustomerService
from app.service.port_normalization_service import PortNormalizationService
from app.service.requirements.po_order_requirements import PoOrderRequirementService
from app.storage.file_store import FileStore
from app.workflow.errors import TaskCancelledError, TaskPausedError
from app.workflow.events import WorkflowEvent, WorkflowEventPublisher, now_iso
from app.workflow.graph import AnalysisGraph, WorkflowHandlers
from app.workflow.state import AnalysisState

logger = logging.getLogger(__name__)

# VLM 偶发拒答（声称"看不到图片"）的判定特征与重试策略：命中后重试，仍失败则让
# 任务以明确错误码失败，避免把全空结果当成 needs_review 交回用户
VLM_REFUSAL_MARKERS = (
    "无法看到",
    "看不到",
    "无法访问",
    "无法直接访问",
    "无法查看",
    "没有收到图片",
    "缺乏对图像",
    "cannot see",
    "unable to see",
    "no image",
)
VLM_MIN_CONTENT_CHARS = 200
VLM_MAX_ATTEMPTS = 3
# VLM 拒答重试前的退避基数（第 n 次重试等待 n × 基数秒）
VLM_RETRY_BACKOFF_SECONDS = 2.0
# 失败事件文案：让事件时间线也能说清原因，未列举的错误码用兜底文案。
# DOCUMENT_TYPE_MISMATCH（类型守卫提前拦下）与 EMPTY_EXTRACTION（守卫放过、模型又抽
# 不出字段）对用户是同一件事——上传的不是空运托书，因此两种码共用同一套文案，避免
# 同一原因在界面上出现两种说法；技术区分保留在 error.code 与 error.detail 里。
NOT_BOOKING_EVENT_MESSAGE = "文件不像空运托书，未识别到托书字段"
FAILURE_EVENT_MESSAGES: dict[str, str] = {
    "DOCUMENT_TYPE_MISMATCH": NOT_BOOKING_EVENT_MESSAGE,
    "EMPTY_EXTRACTION": NOT_BOOKING_EVENT_MESSAGE,
}
# 面向用户的失败说明：技术细节（异常原文）改放 error.detail，前端只展示这条
FAILURE_USER_MESSAGES: dict[str, str] = {
    "DOCUMENT_TYPE_MISMATCH": MISMATCH_MESSAGE,
    "EMPTY_EXTRACTION": MISMATCH_MESSAGE,
}
# 港口归一化未能定论时的事件文案（按归一化服务给出的状态区分）
PORT_REVIEW_MESSAGES: dict[str, str] = {
    "ambiguous": "港口原文对应多个候选，无法唯一确定三字码",
    "not_a_port": "港口字段内容不是地名，未做三字码归一化",
    "failed": "港口字段无法确定三字码",
}
# 终态：进入后不再被节点事件或中断请求改写，SSE 也据此结束推送
TERMINAL_STATUSES = frozenset({"ready", "needs_review", "failed", "cancelled"})
# 冻结态 = 终态 + 暂停：节点事件只追加时间线、不改写状态。暂停不进终态，
# 因为它是可恢复的（SSE 需要继续保持，继续后才能接着推进度）
FROZEN_STATUSES = TERMINAL_STATUSES | {"paused"}

def _collect_boxes(index: DocumentTextIndex, needles: list[str]) -> list[LocatedBox]:
    """收集一组检索词的全部命中位置（用于"引用框"范围判定）。"""

    boxes: list[LocatedBox] = []
    for needle in needles:
        if needle:
            boxes.extend(index.search(needle))
    return boxes


def _pick_box(
    index: DocumentTextIndex,
    needles: list[str],
    quote_boxes: list[LocatedBox],
) -> LocatedBox | None:
    """按可信度依次尝试检索词，返回第一个可确定的位置。

    调用方给定的顺序：带上下文的值片段（如 `74kg`）→ 裸值 → 引用片段。
    单个检索词命中多处时用证据引用框收窄；收窄后仍不唯一则改试下一个
    检索词，全部试完仍不确定返回 None——宁可不标，也不标错位置。
    每个检索词内部先词级精确匹配（避免 "1" 命中 "1PLT"），再子串匹配。
    """

    for needle in needles:
        if not needle:
            continue
        for searcher in (index.search_word, index.search):
            matches = searcher(needle)
            if not matches:
                continue
            if len(matches) == 1:
                return matches[0]
            if quote_boxes:
                narrowed = [
                    box
                    for box in matches
                    if any(box.near(quote) for quote in quote_boxes)
                ]
                if narrowed:
                    return narrowed[0]
            break
    return None


def _value_needles(value: str, quotes: list[str]) -> list[str]:
    """值类字段的检索词优先级：引用派生的带上下文片段 → 裸值。"""

    return [*context_needles(value, quotes), value]


def _match_boxes(index: DocumentTextIndex, needles: list[str]) -> list[LocatedBox]:
    """按可信度顺序返回检索词命中的全部候选框（供多命中时二次挑选）。

    与 _pick_box 的检索顺序一致：同一个检索词先做词级精确匹配，再做子串匹配；
    取第一个有命中的检索词的全部命中处。
    """

    for needle in needles:
        if not needle:
            continue
        for searcher in (index.search_word, index.search):
            matches = searcher(needle)
            if matches:
                return matches
    return []


def _fallback_segments(quotes: list[str], value_text: str) -> list[str]:
    """兜底定位用的引用片段顺序：先"能由值解释"的片段，再无数字的标签，最后其它。

    值被归一化改写后的字段（日期值 `2024-07-25` vs 原文 `7月25日`）只能靠引用片段
    定位，而引用里可能同时带着"年份来源"这类参考信息（如落款 `Date 日期：2024.7.19`）
    ——仅按长度取最长会把高亮打到那个参考日期上。这里要求片段里的数字全部出现在值的
    数字里（多重集包含）：`7月25日`(725) 能由 `2024-07-25` 解释而命中，`2024.7.19`
    （含值里没有的 1、9）落选。同级内长者优先，跨引用时靠前的引用（主证据）优先。
    """

    value_digits = Counter(char for char in value_text if char.isdigit())
    ranked: list[tuple[int, int, int, str]] = []
    for quote_index, quote in enumerate(quotes):
        for segment in quote_segments([quote]):
            digits = [char for char in segment if char.isdigit()]
            if digits and Counter(digits) <= value_digits:
                rank = 0
            elif not digits:
                rank = 1
            else:
                rank = 2
            ranked.append((rank, -len(segment), quote_index, segment))
    ordered: list[str] = []
    for _, _, _, segment in sorted(ranked):
        if segment not in ordered:
            ordered.append(segment)
    return ordered


# 参与人子项与同块其它子项的最大纵向偏差（归一化页高）：超过即判定为别处的同文
PARTY_BLOCK_TOLERANCE = 0.1

# 参与人公司名尾部的中文对照名：文档里常见"英文名（中文名）"写法，如
# "Xinchang Pace Bearing Parts Co., Ltd（新昌沛斯轴承配件有限公司）"，中文名是同一
# 主体的对照名、不属于公司名本身。括号内要求是纯中文，含拉丁字母或数字的括号内容
# 一律不动（可能是公司名的一部分），避免误删。
PARTY_NAME_ALIAS_PATTERN = re.compile(
    r"[\s,，、]*[（(]\s*([\u4e00-\u9fff·]{2,})\s*[）)]$"
)
LATIN_LETTER_PATTERN = re.compile(r"[A-Za-z]")


def _strip_party_name_alias(name: str) -> str:
    """剔除公司名尾部的中文对照名（括号包裹）。

    只在"含拉丁字母的公司名 + 纯中文括号"这种形式下剥离；纯中文公司名、括号内
    混有拉丁字母或数字的原样返回——宁可留下对照名，也不误删公司名本身。
    """

    text = str(name or "").strip()
    match = PARTY_NAME_ALIAS_PATTERN.search(text)
    if match is None:
        return text
    head = text[: match.start()].strip(" \t,，、")
    if not head or LATIN_LETTER_PATTERN.search(head) is None:
        return text
    return head


def _strip_party_name_aliases(
    result: dict[str, Any], field_meta: dict[str, FieldMetadata]
) -> None:
    """就地剔除参与人（发货人/收货人）公司名里的中文对照名。

    模型按"地址块第一行"取公司名，会把同一行里的中文对照名一起带上，这里统一清理
    结果值与字段元数据。evidence 引用保持原文整行不改动，人工仍能在原件预览里看到
    完整写法；引用未被改写，定位逻辑仍按清理后的名称计算高亮框。
    """

    for field_key, meta in field_meta.items():
        value = meta.value
        if not isinstance(value, dict):
            continue
        name = value.get("name")
        if not isinstance(name, str):
            continue
        cleaned = _strip_party_name_alias(name)
        if cleaned == name:
            continue
        updated = {**value, "name": cleaned}
        field_meta[field_key] = meta.model_copy(update={"value": updated})
        result[field_key] = updated


def _align_party_boxes(
    sub_boxes: dict[str, LocatedBox],
    sub_options: dict[str, list[LocatedBox]],
) -> None:
    """校正参与人子项位置：明显脱离整块区域的那一项，改取最靠近其它子项的一处。

    同一段文字在文档里常出现多次——通知人栏会重复收货人的邮箱、底部「委托代理」
    栏会重复发货人的公司名，而它们的分栏文字也互相邻近，仅靠"邻近证据锚点"挑选
    容易选到别处。这里以同块其它子项的位置为准做一次一致性校正：只改动明显离散
    的那一项，已经对齐的项不受影响。
    """

    for sub_key, box in list(sub_boxes.items()):
        others = [
            item
            for key, item in sub_boxes.items()
            if key != sub_key and item.page == box.page
        ]
        if not others:
            continue
        nearest = min(abs(item.bbox[1] - box.bbox[1]) for item in others)
        if nearest <= PARTY_BLOCK_TOLERANCE:
            continue
        options = [
            item for item in sub_options.get(sub_key, []) if item.page == others[0].page
        ]
        if not options:
            continue
        sub_boxes[sub_key] = min(
            options,
            key=lambda item: min(abs(other.bbox[1] - item.bbox[1]) for other in others),
        )


def _union_boxes(boxes: list[LocatedBox]) -> LocatedBox | None:
    """把同一区域的多个框合并为一个覆盖框。

    用于参与人字段：发货人/收货人的名称、地址、电话、邮箱在原文件中本就是
    同一块区域，任何一项命中都代表整块的位置。跨页时以框最多的那一页为准。
    """

    if not boxes:
        return None
    by_page: dict[int, list[LocatedBox]] = {}
    for box in boxes:
        by_page.setdefault(box.page, []).append(box)
    page = max(by_page, key=lambda item: len(by_page[item]))
    group = by_page[page]
    left = min(box.bbox[0] for box in group)
    top = min(box.bbox[1] for box in group)
    right = max(box.bbox[0] + box.bbox[2] for box in group)
    bottom = max(box.bbox[1] + box.bbox[3] for box in group)
    return LocatedBox(
        page=page,
        bbox=(
            round(left, 4),
            round(top, 4),
            round(right - left, 4),
            round(bottom - top, 4),
        ),
    )


# 港口归一化会往证据里追加"港口主数据：…"说明；它不是文档原文，参与定位只会
# 派生出发货人公司名里的同类词，因此定位时排除
MASTER_DATA_QUOTE_MARKER = "主数据"


def _document_quotes(meta: FieldMetadata) -> list[str]:
    """只取文档原文类证据引用（排除主数据说明等合成片段）。"""

    return [
        item.quote
        for item in meta.evidence
        if item.quote and MASTER_DATA_QUOTE_MARKER not in item.quote
    ]


class AnalysisService(WorkflowEventPublisher):
    """协调单份文档分析和本地结果持久化。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.file_store = FileStore(settings)
        self.renderer = LocalDocumentRenderer(self.file_store, settings)
        self.deepseek = DeepSeekExtractionAgent(settings)
        # 提示词在每次抽取时按需读取，便于直接改 .md 即时生效，无需重启服务
        self.requirement_service = PoOrderRequirementService()
        self.port_service = PortNormalizationService(settings, self.file_store)
        self.customer_service = CustomerService(settings, self.file_store)
        # 并发闸门按名字惰性创建：信号量必须在事件循环内构造，而服务是进程级
        # 单例（启动阶段与首次请求都会取用），因此延迟到真正执行任务时创建
        self._gates: dict[str, asyncio.Semaphore] = {}
        # 在途任务的 asyncio 句柄：取消接口据此中断任务，任务结束即移除。
        # 只覆盖本进程发起的任务，跨重启遗留的 running 由
        # recover_interrupted_tasks 兜底
        self._running: dict[str, asyncio.Task[None]] = {}

    def _gate(self, name: str, limit: int) -> asyncio.Semaphore:
        """返回指定用途的并发闸门（同名闸门复用同一实例）。"""

        gate = self._gates.get(name)
        if gate is None:
            gate = self._gates[name] = asyncio.Semaphore(max(1, limit))
        return gate

    async def create_task(
        self,
        upload: UploadedDocument,
        request_id: str,
        schema_version: str,
        context: AnalysisContext,
        auto_start: bool = True,
    ) -> dict[str, Any]:
        """在后台处理前校验并持久化一份输入文档。"""

        existing_task = self.file_store.find_task_by_request_id(request_id)
        if existing_task:
            return {
                "task_id": existing_task["task_id"],
                "request_id": request_id,
                "status": existing_task["status"],
                "idempotent_reuse": True,
            }
        self._validate_upload_metadata(upload)
        self._validate_content(Path(upload.filename), upload.content)
        task_id = self.file_store.new_task_id(upload.filename)
        document_id = self.file_store.new_document_id()
        # 原件存进该任务自己的目录：并发上传同名文件时不会互相覆盖
        uploaded_path, _ = self.file_store.save_task_upload(task_id, upload)
        created_at = now_iso()
        status = {
            "task_id": task_id,
            "request_id": request_id,
            "schema": "po_order",
            "schema_version": schema_version,
            "status": "queued",
            "progress": 0,
            "current_stage": "queued",
            "document_id": document_id,
            "original_name": upload.filename,
            "uploaded_path": self.file_store.relative_path(uploaded_path),
            "process_log_path": self.file_store.relative_path(
                self.file_store.process_log_path(task_id)
            ),
            "context": context.model_dump(by_alias=True, mode="json"),
            "attempt": 1,
            "created_at": created_at,
            "started_at": None,
            "completed_at": None,
            "updated_at": created_at,
            "result_path": None,
            "review_fields": [],
            "overall_confidence": None,
            "error": None,
            "last_node_event": None,
            # 中断意图（pause/cancel）：仅用于把 asyncio 中断信号映射回用户的操作
            "interrupt_requested": None,
        }
        self.file_store.write_json_atomic(self.file_store.task_status_path(task_id), status)
        self._append_process_event(
            task_id,
            event_type="task_created",
            message="分析任务已创建",
            details={
                "original_name": upload.filename,
                "schema_version": schema_version,
                "auto_start": auto_start,
            },
        )
        return {
            "task_id": task_id,
            "request_id": request_id,
            "status": "queued",
            "auto_start": auto_start,
        }

    def list_tasks(self) -> list[dict[str, Any]]:
        """返回可供前端选择的任务文件列表。"""

        task_root = self.file_store.root / "parsed_documents"
        tasks: list[dict[str, Any]] = []
        for status_path in task_root.glob("*/task_status.json"):
            try:
                status = self.file_store.read_json(status_path)
            except (OSError, json.JSONDecodeError):
                continue
            task_id = str(status.get("task_id") or status_path.parent.name)
            result_path = self.file_store.result_path(task_id)
            error = status.get("error")
            error = error if isinstance(error, dict) else {}
            tasks.append(
                {
                    "task_id": task_id,
                    "document_id": status.get("document_id"),
                    "original_name": status.get("original_name") or "未命名文件",
                    "status": status.get("status", "unknown"),
                    "progress": status.get("progress", 0),
                    "current_stage": status.get("current_stage", ""),
                    "created_at": status.get("created_at"),
                    "updated_at": status.get("updated_at"),
                    "result_available": result_path.exists(),
                    # 失败原因随列表下发：历史失败任务（没有 SSE）也能显示准确提示
                    "error_code": error.get("code"),
                    "error_message": error.get("message"),
                    "uploaded_path": status.get("uploaded_path"),
                    "result_path": (
                        self.file_store.relative_path(result_path)
                        if result_path.exists()
                        else None
                    ),
                }
            )
        return sorted(
            tasks,
            # 按上传时间（created_at）倒序，使最近上传的文件排在最上；
            # 同秒上传时再用最后更新时间（updated_at）兜底。
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("updated_at") or ""),
            ),
            reverse=True,
        )

    def read_task_events(self, task_id: str) -> list[dict[str, Any]]:
        """读取任务已落盘的生命周期、业务与节点事件。"""

        self.get_task_status(task_id)
        log_path = self.file_store.process_log_path(task_id)
        if not log_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.file_store.read_text(log_path, errors="replace").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    async def iter_task_events(self, task_id: str) -> AsyncIterator[str]:
        """持续读取任务事件并转换为 SSE 消息。"""

        self.get_task_status(task_id)
        log_path = self.file_store.process_log_path(task_id)
        line_index = 0
        last_status = ""
        while True:
            # 先推送已落盘的节点事件，再推送状态，避免最终状态先于节点事件到达
            if log_path.exists():
                lines = self.file_store.read_text(log_path, errors="replace").splitlines()
                for line in lines[line_index:]:
                    line_index += 1
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message_kind = "workflow" if event.get("node_name") else "task_event"
                    payload = json.dumps(
                        {"kind": message_kind, "event": event},
                        ensure_ascii=False,
                    )
                    yield f"data: {payload}\n\n"

            status = self.get_task_status(task_id)
            status_snapshot = json.dumps(
                {
                    "kind": "status",
                    "task_id": task_id,
                    "status": status.get("status"),
                    "progress": status.get("progress", 0),
                    "current_stage": status.get("current_stage", ""),
                    "review_fields": status.get("review_fields", []),
                    "overall_confidence": status.get("overall_confidence"),
                    "completed_at": status.get("completed_at"),
                    "error": status.get("error"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if status_snapshot != last_status:
                last_status = status_snapshot
                yield f"data: {status_snapshot}\n\n"

            if (
                status.get("status") in TERMINAL_STATUSES
                and line_index >= self._line_count(log_path)
            ):
                break
            await asyncio.sleep(0.6)

    def _line_count(self, path: Path) -> int:
        """返回处理日志当前行数。"""

        if not path.exists():
            return 0
        return len(self.file_store.read_text(path, errors="replace").splitlines())

    def start_task(self, task_id: str) -> None:
        """把任务作为独立 asyncio 任务启动，并登记句柄供取消使用。

        用 `asyncio.create_task` 而不是 FastAPI 的 BackgroundTasks：后者是直接
        await 协程、不创建独立任务，登记到的会是整个 HTTP 请求的 Task，取消它
        会波及请求处理链。这里只创建、不 await，立即返回。
        """

        task = asyncio.create_task(self.process_task(task_id))
        self._running[task_id] = task
        task.add_done_callback(lambda _finished: self._running.pop(task_id, None))

    async def process_task(self, task_id: str) -> None:
        """运行一份已保存文档的 LangGraph 工作流。

        同时存活的任务数达到上限时在此排队等待；等待期间任务快照仍是 queued，
        拿到闸门后才置为 running 并开始推进进度。这里只是兜底限流：重资源阶段
        （LibreOffice 渲染、模型调用）各自有独立闸门，任务走完某阶段就释放该
        阶段闸门，后续任务可以流水线式补位，不必等前面的任务整体跑完。
        """

        async with self._gate("task", self.settings.max_concurrent_tasks):
            await self._run_task(task_id)

    async def _run_task(self, task_id: str) -> None:
        """执行单个任务的工作流（调用方需已持有任务并发闸门）。"""

        status = self.get_task_status(task_id)
        started_at = now_iso()
        self._update_status(
            task_id,
            status="running",
            progress=5,
            current_stage="starting",
            started_at=started_at,
            completed_at=None,
            error=None,
        )
        self._append_process_event(
            task_id,
            event_type="task_started",
            message="分析任务开始执行",
            stage="starting",
        )
        state: AnalysisState = {
            "task_id": task_id,
            "uploaded_path": str(self.file_store.root / status["uploaded_path"]),
            "source_name": status.get("original_name") or Path(status["uploaded_path"]).name,
            "schema_version": status.get("schema_version", "po_order.v1"),
            "context": status.get("context") or {},
        }
        # 恢复执行：产物已在磁盘上的节点会被跳过，只跑剩下的那部分
        state["completed_nodes"] = self._restore_state_from_disk(state)
        workflow = AnalysisGraph(
            handlers=WorkflowHandlers(
                render_document=self.render_document,
                read_images_with_vlm=self.read_images_with_vlm,
                extract_candidates=self.extract_candidates,
                build_result=self.build_result,
            ),
            publisher=self,
        )
        try:
            await workflow.ainvoke(state)
        except TaskPausedError:
            # 节点入口命中暂停：状态通常已由 pause_task 落盘，这里幂等收尾
            logger.info("Analysis task paused at node boundary: %s", task_id)
            self._park_task(task_id, "pause")
        except TaskCancelledError:
            # 节点入口命中取消：状态通常已由 cancel_task 落盘，这里幂等收尾
            logger.info("Analysis task cancelled at node boundary: %s", task_id)
            self._park_task(task_id, "cancel")
        except asyncio.CancelledError:
            # 中断信号打断了模型调用/线程等待：按落盘的中断意图区分暂停与取消。
            # 不重抛：本任务由 start_task 独立持有、没有等待方，重抛只会让事件
            # 循环记录一条无意义的 ASGI 异常堆栈
            kind = self._interrupt_kind(task_id)
            logger.info("Analysis task interrupted (%s) while awaiting: %s", kind, task_id)
            self._park_task(task_id, kind)
        except DocumentTypeMismatchError as exc:
            # 传错文件：不是抽取问题，单独错误码，前端据此给"文件类型不符"提示
            logger.warning("Analysis task document mismatch: %s（%s）", task_id, exc)
            self._fail_task(task_id, exc, "DOCUMENT_TYPE_MISMATCH", hint=exc.hint)
        except EmptyExtractionError as exc:
            # 模型没抽出任何字段：单独错误码，便于与渲染/网络类失败区分
            logger.error("Analysis task empty extraction: %s（%s）", task_id, exc)
            self._fail_task(task_id, exc, "EMPTY_EXTRACTION")
        except Exception as exc:
            logger.exception("Analysis task failed: %s", task_id)
            self._fail_task(task_id, exc, "ANALYSIS_FAILED")

    def _restore_state_from_disk(self, state: AnalysisState) -> list[str]:
        """按磁盘产物补齐 state，并返回已完成节点列表。

        判定依据是"产物是否存在"而不是状态记录：即使记录说某节点已完成、
        文件却已被清理，也只会重跑该节点，而不会带着半截 state 往下走。
        节点产物的命名规则与各 handler 落盘时保持一致。
        """

        completed: list[str] = []
        task_id = state["task_id"]
        stem = self.file_store.source_stem(state.get("source_name"))
        task_dir = self.file_store.root / "parsed_documents" / task_id

        meta_path = task_dir / f"{stem}_render_meta.json"
        if meta_path.exists():
            try:
                meta = self.file_store.read_json(meta_path)
            except (OSError, ValueError):
                meta = {}
            images = [
                str(task_dir / str(name)) for name in (meta.get("images") or [])
            ]
            if images:
                state["parsed"] = {
                    "parsed_directory": str(task_dir),
                    "metadata_path": str(meta_path),
                    "batch_id": None,
                    "image_paths": images,
                }
                state["image_paths"] = images
                completed.append("render_document")

        vlm_path = task_dir / f"{stem}_vlm_image_content.md"
        if vlm_path.exists():
            try:
                vlm_content = self.file_store.read_text(vlm_path, errors="replace")
            except OSError:
                vlm_content = ""
            if vlm_content.strip():
                state["vlm_image_content"] = vlm_content
                completed.append("read_images_with_vlm")

        candidates_path = self.file_store.candidates_path(task_id, stem)
        if candidates_path.exists():
            try:
                payload = self.file_store.read_json(candidates_path)
            except (OSError, ValueError):
                payload = {}
            # 候选允许为空列表（模型确实没抽出字段），文件存在即代表节点跑过
            state["candidates"] = payload.get("candidates") or []
            completed.append("extract_candidates")

        if self.file_store.result_path(task_id).exists():
            completed.append("build_result")

        return completed

    def _fail_task(
        self,
        task_id: str,
        exc: BaseException,
        error_code: str,
        hint: str = "",
    ) -> None:
        """把任务标记为失败并写入统一结构的错误信息。

        `error.message` 优先取面向用户的文案（FAILURE_USER_MESSAGES），异常原文
        改放 `error.detail`，避免把 "EMPTY_EXTRACTION: 模型输出…" 这类技术细节
        直接展示给用户。

        `hint` 用于"文件类型不符"这类需要告诉用户"识别到了什么"的场景，
        随 error 一起下发，前端错误卡片直接展示。
        """

        plain_message = str(exc)
        message = FAILURE_USER_MESSAGES.get(error_code, plain_message)
        error: dict[str, Any] = {"code": error_code, "message": message}
        if hint:
            error["hint"] = hint
        if message != plain_message:
            error["detail"] = plain_message
        self._update_status(
            task_id,
            status="failed",
            progress=100,
            current_stage="failed",
            completed_at=now_iso(),
            error=error,
        )
        details: dict[str, Any] = {"error_code": error_code}
        if hint:
            details["content_hint"] = hint
        self._append_process_event(
            task_id,
            event_type="task_failed",
            message=FAILURE_EVENT_MESSAGES.get(error_code, "分析任务执行失败"),
            stage="failed",
            details=details,
        )

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        """取消在途任务：立即落 cancelled 并中断 asyncio 任务，不可恢复。

        幂等：任务已是终态时原样返回当前状态，不覆盖既有结果——任务可能刚好
        跑完，此时取消应当让位于结果。

        注意 paused 不在"冻结"之列：暂停只是挂起、本来就可以继续，用户完全可能
        在暂停后决定不再继续，因此取消必须能把 paused 推进到 cancelled，
        否则界面上的"取消"会毫无反应。
        """

        status = self.get_task_status(task_id)
        if status.get("status") in TERMINAL_STATUSES:
            return status
        parked = self._park_task(task_id, "cancel")
        handle = self._running.get(task_id)
        if handle is not None:
            handle.cancel()
        return parked

    def pause_task(self, task_id: str) -> dict[str, Any]:
        """暂停在途任务：落 paused 并中断当前 await，已产出的节点保留可恢复。

        与取消的区别：paused 不是终态，`resume_task` 会按磁盘产物从断点继续，
        已产出结果的节点不会重跑。
        """

        status = self.get_task_status(task_id)
        if status.get("status") in FROZEN_STATUSES:
            return status
        parked = self._park_task(task_id, "pause")
        handle = self._running.get(task_id)
        if handle is not None:
            handle.cancel()
        return parked

    def resume_task(self, task_id: str) -> dict[str, Any]:
        """从暂停处继续：只执行没有落盘产物的节点。

        仅 `paused` 可恢复，其他状态原样返回，避免对运行中/已完成的任务误触发。
        """

        status = self.get_task_status(task_id)
        if status.get("status") != "paused":
            return status
        self._update_status(
            task_id,
            status="queued",
            current_stage="queued",
            interrupt_requested=None,
        )
        self._append_process_event(
            task_id,
            event_type="task_resumed",
            message="任务已恢复，将从断点继续",
            stage="resumed",
        )
        self.start_task(task_id)
        return self.get_task_status(task_id)

    def _park_task(
        self,
        task_id: str,
        kind: Literal["pause", "cancel"],
    ) -> dict[str, Any]:
        """按中断类型落盘（幂等：冻结态直接返回）。

        暂停保留进度、可恢复；取消置终态、不可恢复。中断意图同时写进
        `interrupt_requested`，供 `asyncio.CancelledError` 分支区分用户点了哪个按钮。
        两种情况都不写 error 字段：这是用户主动行为，不是失败。
        """

        status = self.get_task_status(task_id)
        # 取消可以作用在 paused 上（挂起不等于冻结，见 cancel_task），只有终态才无需处理；
        # 暂停对同一状态保持幂等，原样返回
        frozen = TERMINAL_STATUSES if kind == "cancel" else FROZEN_STATUSES
        if status.get("status") in frozen:
            return status

        if kind == "pause":
            self._update_status(
                task_id,
                status="paused",
                current_stage="paused",
                interrupt_requested="pause",
            )
            event_type = "task_paused"
            message = "任务已暂停，可从断点继续"
        else:
            # 不置 progress=100：取消时流程并没有跑完，进度条应停在中断的位置，
            # 与暂停保持一致；100% 只留给真正跑完或走完全程收尾的任务
            self._update_status(
                task_id,
                status="cancelled",
                current_stage="cancelled",
                completed_at=now_iso(),
                interrupt_requested="cancel",
            )
            event_type = "task_cancelled"
            message = "任务已取消，不再继续处理"

        self._append_process_event(
            task_id,
            event_type=event_type,
            message=message,
            stage=kind,
            details={"error_code": f"TASK_{kind.upper()}"},
        )
        return self.get_task_status(task_id)

    def _interrupt_kind(self, task_id: str) -> Literal["pause", "cancel"]:
        """读取落盘的中断意图；没有标记时按取消处理（外部取消的兜底语义）。"""

        try:
            status = self.get_task_status(task_id)
        except FileNotFoundError:
            return "cancel"
        return "pause" if status.get("interrupt_requested") == "pause" else "cancel"

    def _raise_if_interrupted(self, task_id: str) -> None:
        """节点入口的中断检查点：暂停/取消各自抛出对应异常。

        中断标志落在任务状态文件里，因此即使 asyncio 中断信号没赶上（任务正卡在
        不可取消的线程操作里），工作流也会在下一个节点边界停下，不再继续往后跑。
        """

        try:
            status = self.get_task_status(task_id)
        except FileNotFoundError:
            return
        requested = status.get("interrupt_requested")
        if requested == "pause" or status.get("status") == "paused":
            raise TaskPausedError(f"任务已暂停：{task_id}")
        if requested == "cancel" or status.get("status") == "cancelled":
            raise TaskCancelledError(f"任务已取消：{task_id}")

    def publish(self, event: WorkflowEvent) -> None:
        """更新任务状态快照并写入任务日志，预留前端推送扩展点。"""

        status = self.get_task_status(event.task_id)
        status["last_node_event"] = event.to_dict()
        # 已进入冻结态（终态或暂停）后，节点事件只追加时间线、不再改写状态：
        # 否则正在收尾的节点会把 cancelled/paused 覆盖回 running
        if status.get("status") not in FROZEN_STATUSES:
            if event.event_type in {"started", "succeeded"}:
                status.update(
                    {
                        "status": "running",
                        "progress": event.progress,
                        "current_stage": event.node_name,
                    }
                )
            elif event.event_type == "skipped":
                pass
            else:
                status.update(
                    {
                        "status": "failed",
                        "progress": 100,
                        "current_stage": event.node_name,
                    }
                )
        status["updated_at"] = datetime.now().astimezone().isoformat()
        self.file_store.write_json_atomic(self.file_store.task_status_path(event.task_id), status)
        self._append_process_payload(event.task_id, event.to_dict())
        if (
            event.node_name == "build_result"
            and event.event_type == "succeeded"
            and status.get("status") in {"ready", "needs_review"}
        ):
            self._append_process_event(
                event.task_id,
                event_type="task_completed",
                message="分析任务执行完成",
                stage="completed",
                details={
                    "status": status["status"],
                    "review_fields": status.get("review_fields", []),
                    "overall_confidence": status.get("overall_confidence"),
                },
            )

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        """读取一个任务的状态 JSON。"""

        path = self.file_store.task_status_path(task_id)
        if not path.exists():
            raise FileNotFoundError(task_id)
        return self.file_store.read_json(path)

    def get_result(self, task_id: str) -> dict[str, Any]:
        """读取一个已完成的分析结果 JSON。"""

        path = self.file_store.result_path(task_id)
        if not path.exists():
            raise FileNotFoundError(task_id)
        return self.file_store.read_json(path)

    def get_page_image_path(self, task_id: str, page_no: int) -> Path:
        """返回任务渲染图片的本地路径，供前端预览区展示原件。

        `page_no` 从 1 开始，与渲染元信息中的图片顺序一致；任务不存在、
        渲染元信息缺失或页码越界时抛 FileNotFoundError，由调用方转换为 404。
        """

        status = self.get_task_status(task_id)
        source_stem = self.file_store.source_stem(status.get("original_name"))
        task_directory = self.file_store.root / "parsed_documents" / task_id
        metadata_path = task_directory / f"{source_stem}_render_meta.json"
        if not metadata_path.exists():
            raise FileNotFoundError(task_id)
        images = self.file_store.read_json(metadata_path).get("images") or []
        if page_no < 1 or page_no > len(images):
            raise FileNotFoundError(f"{task_id}#page_{page_no}")
        image_path = task_directory / str(images[page_no - 1])
        if not image_path.exists():
            raise FileNotFoundError(f"{task_id}#page_{page_no}")
        return image_path

    async def render_document(self, state: AnalysisState) -> dict[str, Any]:
        """本地渲染文档为页面图片与文本层（不经 MinerU）。"""

        task_id = state["task_id"]
        self._raise_if_interrupted(task_id)
        # LibreOffice 转换单独限流：每次转换都会拉起一个独立 soffice 进程，
        # 并发过高会拖垮机器并触发转换超时，因此与任务并发上限分开控制
        async with self._gate(
            "libreoffice", self.settings.libreoffice_max_concurrent
        ):
            rendered = await asyncio.to_thread(
                self.renderer.render,
                Path(state["uploaded_path"]),
                state["source_name"],
                task_id,
            )
        self._update_status(
            task_id,
            rendered_converter=rendered.converter,
            rendered_page_count=rendered.page_count,
        )
        return {
            "parsed_directory": str(rendered.parsed_directory),
            "metadata_path": str(rendered.metadata_path),
            "batch_id": None,
            "image_paths": [str(path) for path in rendered.image_paths],
        }

    @staticmethod
    def _is_vlm_refusal(content: str) -> bool:
        """判断 VLM 是否拒答（声称看不到图片），避免把拒答文本当成视觉内容。"""

        text = content.strip()
        if len(text) < VLM_MIN_CONTENT_CHARS:
            return True
        return any(marker in text for marker in VLM_REFUSAL_MARKERS)

    async def read_images_with_vlm(self, state: AnalysisState) -> str:
        """读取解析图片并生成视觉理解内容（对 VLM 偶发拒答做重试）。"""

        task_id = state["task_id"]
        self._raise_if_interrupted(task_id)
        # 读图 + 四象限裁剪是同步 CPU/IO 操作，放进线程避免阻塞事件循环
        images = await asyncio.to_thread(
            self._load_images, state.get("image_paths", [])
        )
        for attempt in range(1, VLM_MAX_ATTEMPTS + 1):
            # 闸门只包住单次调用：退避等待时释放槽位，别让重试占着并发额度
            async with self._gate("model", self.settings.model_max_concurrent):
                vlm_content = await self.deepseek.describe_images(
                    load_po_order_vision_prompt(),
                    images,
                )
            if not self._is_vlm_refusal(vlm_content):
                break
            logger.warning(
                "VLM 未返回有效视觉内容（第 %s/%s 次，长度 %s），准备重试",
                attempt,
                VLM_MAX_ATTEMPTS,
                len(vlm_content),
            )
            if attempt < VLM_MAX_ATTEMPTS:
                # 退避：并发拉高后上游更容易限流，重试前让出一点时间
                await asyncio.sleep(VLM_RETRY_BACKOFF_SECONDS * attempt)
        else:
            raise RuntimeError(
                f"VLM_VISION_UNAVAILABLE: 连续 {VLM_MAX_ATTEMPTS} 次未能获取图片视觉内容"
            )
        parsed_directory = Path(state["parsed"]["parsed_directory"])
        source_stem = self.file_store.source_stem(state["source_name"])
        vlm_path = parsed_directory / f"{source_stem}_vlm_image_content.md"
        self.file_store.write_text_atomic(vlm_path, vlm_content)
        self._update_status(
            task_id,
            vlm_image_content_path=self.file_store.relative_path(vlm_path),
        )
        return vlm_content

    async def extract_candidates(self, state: AnalysisState) -> list[dict[str, Any]]:
        """仅以 VLM 视觉理解文档为来源抽取字段候选。

        调用模型之前先判定文档类型：内容够长却完全没有托书特征时判定为"传错
        文件"并直接终止——既省掉一次模型调用，也避免它被归因成抽取异常。

        抽取为空时：VLM 内容本身就极短（空白件/未识别出文字）保留空结果交人工
        审核；VLM 内容正常却抽不出字段，判定为抽取失败（EMPTY_EXTRACTION），
        不再伪装成"执行成功但结果全空"。
        """

        task_id = state["task_id"]
        self._raise_if_interrupted(task_id)
        vlm_image_content = state.get("vlm_image_content", "")
        verdict = detect_document_type(vlm_image_content)
        if verdict.blocks_extraction:
            content_hint = build_content_hint(vlm_image_content)
            logger.warning(
                "文档内容不像空运托书，停止字段抽取：task_id=%s（识别内容：%s）",
                task_id,
                content_hint,
            )
            self._append_process_event(
                task_id,
                event_type="document_type_mismatch",
                message="文档内容不像空运托书，已停止字段抽取",
                stage="extract_candidates",
                details={"hit_groups": [], "content_hint": content_hint},
            )
            raise DocumentTypeMismatchError(MISMATCH_MESSAGE, hint=content_hint)
        async with self._gate("model", self.settings.model_max_concurrent):
            try:
                model_candidates = await self.deepseek.extract(
                    system_prompt=load_po_order_extraction_prompt(),
                    vlm_image_content=vlm_image_content,
                    context=state["context"],
                )
            except EmptyExtractionError:
                if len(vlm_image_content.strip()) < VLM_MIN_CONTENT_CHARS:
                    logger.warning(
                        "VLM 视觉内容为空，抽取结果按空处理：task_id=%s", task_id
                    )
                    model_candidates = [
                        FieldCandidate(field_key=key, value=None, status="missing")
                        for key in PO_ORDER_KEYS
                    ]
                else:
                    raise
        allowed_keys = set(PO_ORDER_KEYS)
        all_candidates = [
            candidate for candidate in model_candidates if candidate.field_key in allowed_keys
        ]
        self.file_store.write_json_atomic(
            self.file_store.candidates_path(task_id, self.file_store.source_stem(state["source_name"])),
            {"task_id": task_id, "candidates": [candidate.model_dump(mode="json") for candidate in all_candidates]},
        )
        return [candidate.model_dump(mode="json") for candidate in all_candidates]

    async def build_result(self, state: AnalysisState) -> dict[str, Any]:
        """按候选置信度生成最终结果：保留模型原值，低于阈值标记待人工审核。"""

        task_id = state["task_id"]
        self._raise_if_interrupted(task_id)
        schema_version = state.get("schema_version", "po_order.v1")
        context = state.get("context") or {}
        threshold = self.settings.review_confidence_threshold

        candidates = [
            FieldCandidate.model_validate(item) for item in state.get("candidates", [])
        ]
        required_keys = set(self.requirement_service.required_field_keys(context))

        result: dict[str, Any] = {key: None for key in PO_ORDER_KEYS}
        field_meta: dict[str, FieldMetadata] = {}
        review_fields: set[str] = set()

        # 委托客户（fid）仅由调用方 context 提供，不走模型抽取
        client_value = self.requirement_service.context_client_value(context)
        if client_value not in (None, ""):
            result["fid"] = client_value
            field_meta["fid"] = FieldMetadata(
                value=client_value,
                status="confirmed",
                confidence=1.0,
                evidence=[Evidence(quote=f"fid={client_value}")],
            )

        # 同一字段聚合，取置信度最高的候选；值原样保留，不修改模型输出
        grouped: dict[str, list[FieldCandidate]] = {}
        for candidate in candidates:
            if candidate.value is None or candidate.field_key in CONTEXT_ONLY_KEYS:
                continue
            grouped.setdefault(candidate.field_key, []).append(candidate)

        # 只要状态属于需要人工介入的范畴，就纳入待审核集合
        # （低置信度、模型主动标记的 needs_review/conflict、必填缺失的 missing、非法 invalid）
        review_statuses = {"needs_review", "conflict", "missing", "invalid"}

        for field_key, options in grouped.items():
            if field_key not in result:
                continue
            best = max(options, key=lambda item: item.confidence)
            below = best.confidence < threshold
            status = "needs_review" if below else best.status
            # 港口原文仅代表抽取已确认；normalized 只应由后续三字码归一化成功后设置。
            if field_key in PORT_FIELD_KEYS and status == "normalized":
                status = "confirmed"
            if status in review_statuses:
                review_fields.add(field_key)
            field_meta[field_key] = FieldMetadata(
                value=best.value,
                status=status,
                confidence=best.confidence,
                evidence=best.evidence,
            )
            result[field_key] = best.value

        # 必填字段缺失 → 标记待人工审核，绝不伪造值
        for required_key in required_keys:
            meta = field_meta.get(required_key)
            if meta is None or meta.value is None:
                review_fields.add(required_key)
                field_meta.setdefault(
                    required_key,
                    FieldMetadata(
                        value=None,
                        status="missing",
                        confidence=0.0,
                        evidence=[],
                    ),
                )

        # 始发港/到达港三字码归一化：仅对通过质量审核的字段执行，
        # 待人工审核字段质量不高，不做转换，保留原值
        await self._normalize_port_fields(
            result,
            field_meta,
            review_fields,
            task_id=task_id,
        )

        # 参与人公司名剔除中文对照名（详见 _strip_party_name_aliases）：
        # 放在定位之前，使高亮框按清理后的名称计算
        _strip_party_name_aliases(result, field_meta)

        # 把字段值映射回源 PDF 坐标（供前端原件预览高亮），失败不影响结果产出
        await self._locate_field_locations(
            field_meta,
            task_id,
            state.get("source_name") or "",
        )

        ordered_field_meta = {
            key: field_meta[key] for key in PO_ORDER_KEYS if key in field_meta
        }
        ordered_review_fields = [
            key for key in PO_ORDER_KEYS if key in review_fields
        ]
        confidences = [meta.confidence for meta in field_meta.values() if meta.value is not None]
        overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        overall_status: Literal["ready", "needs_review", "failed"] = (
            "needs_review" if review_fields else "ready"
        )
        analysis_result = AnalysisResult(
            task_id=task_id,
            schema_version=schema_version,
            result=result,
            overall_status=overall_status,
            overall_confidence=overall_confidence,
            field_meta=ordered_field_meta,
            review_fields=ordered_review_fields,
            validation=ValidationResult(is_valid=True),
        )
        result_path = self.file_store.result_path(task_id)
        self.file_store.write_json_atomic(result_path, analysis_result.model_dump(mode="json"))
        completed_at = now_iso()
        self._update_status(
            task_id,
            status=overall_status,
            progress=100,
            current_stage="completed",
            completed_at=completed_at,
            result_path=self.file_store.relative_path(result_path),
            review_fields=ordered_review_fields,
            overall_confidence=overall_confidence,
            error=None,
        )
        self._append_process_event(
            task_id,
            event_type="result_built",
            message="最终分析结果已生成",
            stage="build_result",
            details={
                "status": overall_status,
                "review_fields": ordered_review_fields,
                "normalized_fields": [
                    key
                    for key, meta in ordered_field_meta.items()
                    if meta.status == "normalized"
                ],
            },
        )
        return analysis_result.model_dump(mode="json")

    async def _normalize_port_fields(
        self,
        result: dict[str, Any],
        field_meta: dict[str, FieldMetadata],
        review_fields: set[str],
        task_id: str | None = None,
    ) -> None:
        """对非待审核的始发港/到达港执行三字码归一化。

        - 归一化成功：result 值替换为三字码，字段状态置为 normalized，
          原文与主数据出处追加进 evidence；
        - 查表/校验不通过：保留原值，字段状态降级为 needs_review；
        - 港口服务未启用或执行失败：整体跳过，不影响主流程。
        """

        review_statuses = {"needs_review", "conflict", "missing", "invalid"}
        candidates = {
            key: str(meta.value).strip()
            for key, meta in field_meta.items()
            if key in PORT_FIELD_KEYS
            and isinstance(meta.value, str)
            and meta.value.strip()
            and meta.status not in review_statuses
        }
        if not candidates:
            return
        try:
            outcomes = await self.port_service.normalize(candidates)
        except Exception:
            logger.exception("港口三字码归一化执行失败，保留原值")
            return
        for key, outcome in outcomes.items():
            meta = field_meta.get(key)
            if meta is None:
                continue
            if outcome.status == "normalized" and outcome.assembled:
                evidence = list(meta.evidence)
                matched_by = f"，来源：{outcome.matched_by}" if outcome.matched_by else ""
                evidence.append(
                    Evidence(
                        quote=(
                            f"港口主数据：{outcome.three_code} "
                            f"{outcome.english_name or ''}（原文：{outcome.raw_value}"
                            f"{matched_by}）"
                        ).strip()
                    )
                )
                result[key] = outcome.assembled
                field_meta[key] = meta.model_copy(
                    update={
                        "value": outcome.assembled,
                        "status": "normalized",
                        "evidence": evidence,
                        "raw_value": outcome.raw_value,
                        "candidates": list(outcome.candidates),
                    }
                )
                if task_id:
                    self._append_process_event(
                        task_id,
                        event_type="port_normalized",
                        message="港口字段三字码归一化成功",
                        stage="build_result",
                        details={
                            "field_key": key,
                            "three_code": outcome.three_code,
                            "matched_by": outcome.matched_by,
                        },
                    )
                continue
            # ambiguous / not_a_port / failed：一律转人工审核，
            # 并把候选与原因带进事件，人工核对时能直接看到可选值
            reason = outcome.reason or outcome.status
            logger.info(
                "港口归一化未定论（%s／%s：%s），字段 %s 降级为待人工审核",
                outcome.status,
                reason,
                outcome.raw_value,
                key,
            )
            # 表单留空（提交接口要求三字码），原文与候选留在元数据里：
            # 前端在空字段下方展示候选供人工直接选用
            field_meta[key] = meta.model_copy(
                update={
                    "value": None,
                    "status": "needs_review",
                    "raw_value": outcome.raw_value,
                    "candidates": list(outcome.candidates),
                }
            )
            result[key] = None
            review_fields.add(key)
            if task_id:
                self._append_process_event(
                    task_id,
                    event_type="port_review_required",
                    message=PORT_REVIEW_MESSAGES.get(
                        outcome.status, "港口字段无法唯一确定三字码"
                    ),
                    stage="build_result",
                    details={
                        "field_key": key,
                        "status": outcome.status,
                        "reason": reason,
                        "candidates": [
                            candidate.model_dump(mode="json")
                            for candidate in outcome.candidates
                        ],
                    },
                )

    async def search_customers(self, keyword: str) -> dict[str, Any]:
        """按关键字搜索委托客户主数据，供前端「输入即下拉」挑选。

        返回 items（候选客户）与 enabled（客户主数据是否可用）；未配置主数据
        接口时 enabled 为 False，前端退化为手工填写。
        """

        candidates = await self.customer_service.search(keyword)
        return {
            "enabled": self.customer_service.enabled,
            "items": [item.model_dump(mode="json") for item in candidates],
        }

    async def search_ports(self, keyword: str) -> dict[str, Any]:
        """按关键字搜索港口主数据，供前端「输入即下拉」挑三字码。

        纯本地主数据匹配，不调用模型，因此响应快；返回 items（候选港口）与
        enabled（港口主数据是否可用）。
        """

        candidates = await self.port_service.search(keyword)
        return {
            "enabled": self.port_service.enabled,
            "items": [item.model_dump(mode="json") for item in candidates],
        }

    async def _locate_field_locations(
        self,
        field_meta: dict[str, FieldMetadata],
        task_id: str,
        source_name: str,
    ) -> None:
        """把字段值与证据引文映射回源 PDF 坐标，写入 field_meta[*].locations。

        定位失败（没有 PDF、扫描件无文本层、匹配不上）时保持空列表，由前端按
        "未定位"展示；任何异常只记日志，绝不影响结果产出。
        """

        targets = [
            key
            for key, meta in field_meta.items()
            if key not in CONTEXT_ONLY_KEYS
            # 归一化失败的港口字段 value 已置空，用原文同样要参与定位
            and (
                meta.value not in (None, "", {})
                or meta.raw_value not in (None, "", {})
            )
        ]
        if not targets:
            return
        try:
            status = self.get_task_status(task_id)
        except FileNotFoundError:
            return
        pdf_path = resolve_source_pdf(
            self.file_store.root,
            task_id,
            status.get("uploaded_path"),
            self.file_store.source_stem(source_name),
        )
        if pdf_path is None:
            logger.info("任务没有可定位的源 PDF，跳过字段定位：%s", task_id)
            return
        try:
            located = await asyncio.to_thread(
                self._locate_in_document, pdf_path, field_meta, targets
            )
        except Exception:
            logger.exception("字段坐标定位失败，结果保持未定位：%s", task_id)
            return
        for key, items in located.items():
            meta = field_meta.get(key)
            if meta is None or not items:
                continue
            field_meta[key] = meta.model_copy(update={"locations": items})

    @staticmethod
    def _locate_in_document(
        pdf_path: Path,
        field_meta: dict[str, FieldMetadata],
        targets: list[str],
    ) -> dict[str, list[EvidenceLocation]]:
        """在 PDF 文本层中为每个字段找出值的位置（同步实现，由调用方放入线程）。"""

        index = DocumentTextIndex(pdf_path)
        if not index.has_text_layer:
            return {}
        located: dict[str, list[EvidenceLocation]] = {}
        for key in targets:
            meta = field_meta[key]
            quotes = _document_quotes(meta)
            quote_boxes = _collect_boxes(index, quotes)
            # 整条引用匹配不上（标签与值跨文本块、同行其它栏打断阅读顺序）时，
            # 用它的词元当区域锚点，把值的多个命中收窄到正确那一处；
            # 与值本身相同的词元要排除，否则等于拿值给自己当锚点，失去收窄意义
            # 归一化失败的港口字段 value 已置空，定位回落到归一化前的原文
            effective = (
                meta.value if meta.value not in (None, "", {}) else meta.raw_value
            )
            values = (
                [str(item) for item in effective.values() if item]
                if isinstance(effective, dict)
                else [str(effective)]
            )
            value_keys = {normalize_needle(item) for item in values}
            anchor_boxes = [
                *quote_boxes,
                *_collect_boxes(
                    index,
                    [item for item in quote_segments(quotes) if item not in value_keys],
                ),
            ]
            items: list[EvidenceLocation] = []
            if isinstance(effective, dict):
                # 参与人字段（发货人/收货人）整体定位：四个子项在同一块区域，
                # 逐项先取精确框；没匹配上的子项沿用整块区域框，避免出现
                # "名称定位到了、地址却未定位"这种同块内自相矛盾的状态。
                # 空值子项不产出定位（前端也不会有标记）。
                sub_boxes: dict[str, LocatedBox] = {}
                sub_options: dict[str, list[LocatedBox]] = {}
                for sub_key in PARTY_KEYS:
                    sub_value = effective.get(sub_key)
                    if not sub_value:
                        continue
                    needles = _value_needles(str(sub_value), quotes)
                    sub_options[sub_key] = _match_boxes(index, needles)
                    box = _pick_box(index, needles, anchor_boxes)
                    if box is not None:
                        sub_boxes[sub_key] = box
                # 同文在别处重复出现（通知人栏重写收货人邮箱、底部委托代理栏重写
                # 发货人公司名）时，按同块其它子项的位置做一次一致性校正
                _align_party_boxes(sub_boxes, sub_options)
                region_box = _union_boxes(
                    [*sub_boxes.values(), *quote_boxes]
                )
                for sub_key in PARTY_KEYS:
                    if not effective.get(sub_key):
                        continue
                    box = sub_boxes.get(sub_key) or region_box
                    if box is None:
                        continue
                    items.append(
                        EvidenceLocation(
                            target=f"{key}.{sub_key}",
                            page=box.page,
                            bbox=list(box.bbox),
                        )
                    )
            else:
                box = _pick_box(
                    index, _value_needles(str(effective), quotes), anchor_boxes
                )
                if box is not None:
                    items.append(
                        EvidenceLocation(target=key, page=box.page, bbox=list(box.bbox))
                    )
            if items:
                located[key] = items
            elif quote_boxes:
                # 值本身定位不到（被改写/换行异常）时，退化为引用片段的位置
                located[key] = [
                    EvidenceLocation(
                        target=key,
                        page=quote_boxes[0].page,
                        bbox=list(quote_boxes[0].bbox),
                    )
                ]
            else:
                # 连整条引用都匹配不上时（标签与值被其它单元格/换行打断），退到
                # 引用切出的片段：日期字段的值会被归一化成 YYYY-MM-DD，文档里往往
                # 只有"7月25日"这种写法，而它正是引用片段，此前只用于收窄多命中。
                # 片段排序见 _fallback_segments（要能由值解释，避免打到年份参考日期）；
                # 片段必须能唯一定位才采纳（不传锚点），宁可不标也不标错。
                box = _pick_box(
                    index, _fallback_segments(quotes, " ".join(values)), []
                )
                if box is not None:
                    located[key] = [
                        EvidenceLocation(target=key, page=box.page, bbox=list(box.bbox))
                    ]
        return located

    def _load_images(self, paths: list[str]) -> list[ImageInput]:
        """加载页面图片：第一页给整页图 + 四象限切分图，保证小字号文本可辨认。"""

        page_paths = [
            path
            for path in paths
            if Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
        budget = 6
        images: list[ImageInput] = []
        for index, raw_path in enumerate(page_paths):
            if len(images) >= budget:
                break
            path = Path(raw_path)
            media_type = mimetypes.guess_type(path.name)[0] or "image/png"
            content = self.file_store.read_bytes(path)
            images.append(
                ImageInput(
                    content=content,
                    media_type=media_type,
                    name=path.name,
                )
            )
            if index == 0:
                images.extend(self._split_page_images(content, path.name))
        return images[:budget]

    @staticmethod
    def _split_page_images(content: bytes, name: str) -> list[ImageInput]:
        """把整页图按左上/右上/左下/右下切成带重叠的四块，提升小字辨认率。

        每块约占整页 38% 面积，横纵各留 12% 重叠，避免落在分界线上的
        文字被切成两半后两侧都看不全。
        """

        import io

        from PIL import Image  # type: ignore[import-not-found]

        image = Image.open(io.BytesIO(content))
        width, height = image.size
        overlap_x = int(width * 0.12)
        overlap_y = int(height * 0.12)
        mid_x = width // 2
        mid_y = height // 2
        boxes = (
            (0, 0, mid_x + overlap_x, mid_y + overlap_y),  # 左上
            (mid_x - overlap_x, 0, width, mid_y + overlap_y),  # 右上
            (0, mid_y - overlap_y, mid_x + overlap_x, height),  # 左下
            (mid_x - overlap_x, mid_y - overlap_y, width, height),  # 右下
        )
        stem = Path(name).stem
        parts: list[ImageInput] = []
        for index, box in enumerate(boxes, start=1):
            buffer = io.BytesIO()
            image.crop(box).save(buffer, format="PNG")
            parts.append(
                ImageInput(
                    content=buffer.getvalue(),
                    media_type="image/png",
                    name=f"{stem}_part_{index}.png",
                )
            )
        return parts

    def run_data_retention_cleanup(self) -> list[str]:
        """清理超过保留期的任务产物，返回被删除条目的相对路径。

        服务启动时与每小时各执行一次；running 任务由 FileStore 跳过，
        不会误删在途任务。
        """

        removed = self.file_store.cleanup_expired(self.settings.data_retention_hours)
        if removed:
            logger.info("数据保留期清理完成：%s", removed)
        return removed

    def recover_interrupted_tasks(self) -> list[str]:
        """服务启动时将上次中断遗留的 running 任务标记为失败。

        worker 被重启/热重载/异常终止时，任务快照会永远停留在 running；
        启动阶段不存在任何在途任务，因此 running 状态必为残留。
        """

        recovered: list[str] = []
        for status_path in self.file_store.root.glob("parsed_documents/*/task_status.json"):
            try:
                status = self.file_store.read_json(status_path)
            except (OSError, ValueError):
                logger.warning("任务状态文件损坏，跳过恢复：%s", status_path)
                continue
            if status.get("status") != "running":
                continue
            task_id = status.get("task_id")
            if not task_id:
                continue
            self._update_status(
                task_id,
                status="failed",
                progress=100,
                current_stage="failed",
                completed_at=now_iso(),
                error={
                    "code": "TASK_INTERRUPTED",
                    "message": "服务重启导致任务中断，请重新发起分析",
                },
            )
            self._append_process_event(
                task_id,
                event_type="task_interrupted",
                message="服务重启，任务未完成已标记为失败",
                stage="failed",
                details={"error_code": "TASK_INTERRUPTED"},
            )
            recovered.append(str(task_id))
        if recovered:
            logger.warning("已将 %s 个中断任务标记为失败：%s", len(recovered), recovered)
        return recovered

    def _update_status(self, task_id: str, **updates: Any) -> None:
        """原子更新一个任务状态快照。"""

        status = self.get_task_status(task_id)
        status.update(updates)
        status["updated_at"] = datetime.now().astimezone().isoformat()
        self.file_store.write_json_atomic(self.file_store.task_status_path(task_id), status)

    def _append_process_event(
        self,
        task_id: str,
        event_type: str,
        message: str,
        stage: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """向单任务 JSONL 日志追加一条业务事件。"""

        payload: dict[str, Any] = {
            "kind": "task_lifecycle",
            "task_id": task_id,
            "event_type": event_type,
            "message": message,
            "timestamp": now_iso(),
        }
        if stage:
            payload["stage"] = stage
        if details:
            payload["details"] = details
        self._append_process_payload(task_id, payload)

    def _append_process_payload(self, task_id: str, payload: dict[str, Any]) -> None:
        """将结构化事件追加到任务 process.log。"""

        self.file_store.append_text(
            self.file_store.process_log_path(task_id),
            json.dumps(payload, ensure_ascii=False) + "\n",
        )

    def _validate_upload_metadata(self, upload: UploadedDocument) -> None:
        """校验上传文件名和声明的内容类型。"""

        filename = upload.filename or ""
        suffix = Path(filename).suffix.lower()
        if suffix not in self.settings.allowed_extensions:
            raise ValueError("FILE_TYPE_NOT_SUPPORTED")
        if upload.content_type and upload.content_type not in {
            "application/pdf",
            "application/msword",
            "application/vnd.ms-excel",
            "application/octet-stream",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }:
            raise ValueError("FILE_CONTENT_INVALID")

    def _validate_content(self, path: Path, content: bytes) -> None:
        """校验文件非空以及配置的文件大小限制。"""

        if not content:
            raise ValueError("FILE_EMPTY")
        if len(content) > self.settings.max_file_size_bytes:
            raise ValueError("FILE_TOO_LARGE")
        if path.suffix.lower() == ".pdf" and not content.startswith(b"%PDF-"):
            raise ValueError("FILE_CONTENT_INVALID")
        if path.suffix.lower() in {".doc", ".xls"} and not content.startswith(
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        ):
            raise ValueError("FILE_CONTENT_INVALID")
        # docx/xlsx 是 ZIP 容器，魔数固定为 PK\x03\x04
        if path.suffix.lower() in {".docx", ".xlsx"} and not content.startswith(b"PK\x03\x04"):
            raise ValueError("FILE_CONTENT_INVALID")
