import asyncio
import json
import logging
import mimetypes
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
from app.config import Settings
from app.prompts.loader import (
    load_po_order_extraction_prompt,
    load_po_order_vision_prompt,
)
from app.schemas.analysis import (
    AnalysisContext,
    AnalysisResult,
    Evidence,
    FieldCandidate,
    FieldMetadata,
    ValidationResult,
)
from app.schemas.file import UploadedDocument
from app.schemas.po_order import (
    CONTEXT_ONLY_KEYS,
    PO_ORDER_KEYS,
    PORT_FIELD_KEYS,
)
from app.service.port_normalization_service import PortNormalizationService
from app.service.requirements.po_order_requirements import PoOrderRequirementService
from app.storage.file_store import FileStore
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
        # 并发闸门按名字惰性创建：信号量必须在事件循环内构造，而服务是进程级
        # 单例（启动阶段与首次请求都会取用），因此延迟到真正执行任务时创建
        self._gates: dict[str, asyncio.Semaphore] = {}

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
                status.get("status") in {"ready", "needs_review", "failed"}
                and line_index >= self._line_count(log_path)
            ):
                break
            await asyncio.sleep(0.6)

    def _line_count(self, path: Path) -> int:
        """返回处理日志当前行数。"""

        if not path.exists():
            return 0
        return len(self.file_store.read_text(path, errors="replace").splitlines())

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
        except EmptyExtractionError as exc:
            # 模型没抽出任何字段：单独错误码，便于与渲染/网络类失败区分
            logger.error("Analysis task empty extraction: %s（%s）", task_id, exc)
            self._fail_task(task_id, exc, "EMPTY_EXTRACTION")
        except Exception as exc:
            logger.exception("Analysis task failed: %s", task_id)
            self._fail_task(task_id, exc, "ANALYSIS_FAILED")

    def _fail_task(self, task_id: str, exc: BaseException, error_code: str) -> None:
        """把任务标记为失败并写入统一结构的错误信息。"""

        self._update_status(
            task_id,
            status="failed",
            progress=100,
            current_stage="failed",
            completed_at=now_iso(),
            error={"code": error_code, "message": str(exc)},
        )
        self._append_process_event(
            task_id,
            event_type="task_failed",
            message="分析任务执行失败",
            stage="failed",
            details={"error_code": error_code},
        )

    def publish(self, event: WorkflowEvent) -> None:
        """更新任务状态快照并写入任务日志，预留前端推送扩展点。"""

        status = self.get_task_status(event.task_id)
        status["last_node_event"] = event.to_dict()
        if event.event_type == "started":
            status.update(
                {
                    "status": "running",
                    "progress": event.progress,
                    "current_stage": event.node_name,
                }
            )
        elif event.event_type == "succeeded":
            if status.get("status") not in {"ready", "needs_review", "failed"}:
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

        抽取为空时：VLM 内容本身就极短（空白件/未识别出文字）保留空结果交人工
        审核；VLM 内容正常却抽不出字段，判定为抽取失败（EMPTY_EXTRACTION），
        不再伪装成"执行成功但结果全空"。
        """

        task_id = state["task_id"]
        vlm_image_content = state.get("vlm_image_content", "")
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
                evidence.append(
                    Evidence(
                        quote=(
                            f"港口主数据：{outcome.three_code} "
                            f"{outcome.english_name or ''}（原文：{outcome.raw_value}）"
                        ).strip()
                    )
                )
                result[key] = outcome.assembled
                field_meta[key] = meta.model_copy(
                    update={
                        "value": outcome.assembled,
                        "status": "normalized",
                        "evidence": evidence,
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
                        },
                    )
            elif outcome.status == "skipped":
                logger.info(
                    "港口原文无法唯一确定三字码（%s：%s），字段降级为待人工审核",
                    outcome.reason,
                    outcome.raw_value,
                )
                field_meta[key] = meta.model_copy(update={"status": "needs_review"})
                review_fields.add(key)
                if task_id:
                    self._append_process_event(
                        task_id,
                        event_type="port_review_required",
                        message="港口字段无法唯一确定三字码",
                        stage="build_result",
                        details={
                            "field_key": key,
                            "reason": outcome.reason,
                        },
                    )
            else:
                logger.info(
                    "港口归一化未通过（%s：%s），字段 %s 降级为待人工审核",
                    outcome.reason,
                    outcome.raw_value,
                    key,
                )
                field_meta[key] = meta.model_copy(update={"status": "needs_review"})
                review_fields.add(key)
                if task_id:
                    self._append_process_event(
                        task_id,
                        event_type="port_review_required",
                        message="港口字段三字码校验未通过",
                        stage="build_result",
                        details={
                            "field_key": key,
                            "reason": outcome.reason,
                            "proposed_code": outcome.three_code,
                        },
                    )

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
