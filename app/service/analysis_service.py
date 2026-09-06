import asyncio
import json
import logging
import mimetypes
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from app.agent.deepseek_extractor import DeepSeekExtractionAgent, ImageInput
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
    FIELD_TITLES,
    NUMBER_KEYS,
    OBJECT_KEYS,
    PO_ORDER_KEYS,
    PO_ORDER_REQUIRED_KEYS,
)
from app.service.requirements.po_order_requirements import PoOrderRequirementService
from app.storage.file_store import FileStore
from app.workflow.events import WorkflowEvent, WorkflowEventPublisher
from app.workflow.graph import AnalysisGraph, WorkflowHandlers
from app.workflow.state import AnalysisState

logger = logging.getLogger(__name__)

class AnalysisService(WorkflowEventPublisher):
    """协调单份文档分析和本地结果持久化。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.file_store = FileStore(settings)
        self.renderer = LocalDocumentRenderer(self.file_store)
        self.deepseek = DeepSeekExtractionAgent(settings)
        # 提示词在每次抽取时按需读取，便于直接改 .md 即时生效，无需重启服务
        self.requirement_service = PoOrderRequirementService()

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
        uploaded_path, _ = self.file_store.save_upload(upload)
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
            "created_at": datetime.now().astimezone().isoformat(),
            "updated_at": datetime.now().astimezone().isoformat(),
            "error": None,
        }
        self.file_store.write_json_atomic(self.file_store.task_status_path(task_id), status)
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
        """读取任务已落盘的全部节点事件（用于回看已完成任务）。"""

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
                    payload = json.dumps(
                        {"kind": "workflow", "event": event},
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
        """运行一份已保存文档的 LangGraph 工作流。"""

        status = self.get_task_status(task_id)
        self._update_status(task_id, status="running", progress=5, current_stage="starting")
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
        except Exception as exc:
            logger.exception("Analysis task failed: %s", task_id)
            self._update_status(
                task_id,
                status="failed",
                progress=100,
                current_stage="failed",
                error={"code": "ANALYSIS_FAILED", "message": str(exc)},
            )

    def publish(self, event: WorkflowEvent) -> None:
        """记录节点事件并更新任务状态，预留前端推送扩展点。"""

        logger.info(
            "工作流节点事件：task_id=%s node=%s event=%s",
            event.task_id,
            event.node_name,
            event.event_type,
        )
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
        self.file_store.append_text(
            self.file_store.process_log_path(event.task_id),
            json.dumps(event.to_dict(), ensure_ascii=False) + "\n",
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

    def get_target_schema(self, schema_version: str) -> dict[str, Any]:
        """返回订单新增目标字段 schema（12 字段，必填在前、选填在后）。"""

        if schema_version != "po_order.v1":
            raise FileNotFoundError(schema_version)
        required = set(PO_ORDER_REQUIRED_KEYS)
        return {
            "schema": "po_order",
            "version": schema_version,
            "fields": [
                {
                    "key": field_key,
                    "title": FIELD_TITLES[field_key],
                    "required": field_key in required,
                    "type": (
                        "object"
                        if field_key in OBJECT_KEYS
                        else "number"
                        if field_key in NUMBER_KEYS
                        else "string"
                    ),
                    "context_only": field_key in CONTEXT_ONLY_KEYS,
                }
                for field_key in PO_ORDER_KEYS
            ],
        }

    async def render_document(self, state: AnalysisState) -> dict[str, Any]:
        """本地渲染文档为页面图片与文本层（不经 MinerU）。"""

        task_id = state["task_id"]
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

    async def read_images_with_vlm(self, state: AnalysisState) -> str:
        """读取解析图片并生成视觉理解内容。"""

        task_id = state["task_id"]
        images = self._load_images(state.get("image_paths", []))
        vlm_content = await self.deepseek.describe_images(
            load_po_order_vision_prompt(),
            images,
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
        """仅以 VLM 视觉理解文档为来源抽取字段候选。"""

        task_id = state["task_id"]
        model_candidates = await self.deepseek.extract(
            system_prompt=load_po_order_extraction_prompt(),
            vlm_image_content=state.get("vlm_image_content", ""),
            context=state["context"],
        )
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
                extraction_method="context",
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
            if status in review_statuses:
                review_fields.add(field_key)
            field_meta[field_key] = FieldMetadata(
                value=best.value,
                status=status,
                confidence=best.confidence,
                evidence=best.evidence,
                extraction_method=best.extraction_method,
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
                        extraction_method="none",
                    ),
                )

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
            field_meta=field_meta,
            validation=ValidationResult(is_valid=True),
        )
        result_path = self.file_store.result_path(task_id)
        self.file_store.write_json_atomic(result_path, analysis_result.model_dump(mode="json"))
        self._update_status(
            task_id,
            status=overall_status,
            progress=100,
            current_stage="completed",
            result_path=self.file_store.relative_path(result_path),
        )
        return analysis_result.model_dump(mode="json")

    def _load_images(self, paths: list[str]) -> list[ImageInput]:
        """加载页面图片：第一页给整页图 + 上下两半放大图，保证小字号文本可辨认。"""

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
                images.extend(self._split_half_images(content, path.name))
        return images[:budget]

    @staticmethod
    def _split_half_images(content: bytes, name: str) -> list[ImageInput]:
        """把整页图切成带垂直重叠的上下两半，提升小字辨认率且减少图片数量。"""

        import io

        from PIL import Image  # type: ignore[import-not-found]

        image = Image.open(io.BytesIO(content))
        width, height = image.size
        overlap_y = int(height * 0.12)
        mid_y = height // 2
        boxes = (
            (0, 0, width, mid_y + overlap_y),
            (0, mid_y - overlap_y, width, height),
        )
        stem = Path(name).stem
        halves: list[ImageInput] = []
        for index, box in enumerate(boxes, start=1):
            buffer = io.BytesIO()
            image.crop(box).save(buffer, format="PNG")
            halves.append(
                ImageInput(
                    content=buffer.getvalue(),
                    media_type="image/png",
                    name=f"{stem}_half_{index}.png",
                )
            )
        return halves

    def _update_status(self, task_id: str, **updates: Any) -> None:
        """原子更新一个任务状态快照。"""

        status = self.get_task_status(task_id)
        status.update(updates)
        status["updated_at"] = datetime.now().astimezone().isoformat()
        self.file_store.write_json_atomic(self.file_store.task_status_path(task_id), status)

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
