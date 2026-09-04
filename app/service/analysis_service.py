import asyncio
import json
import logging
import mimetypes
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.deepseek_extractor import DeepSeekExtractionAgent, ImageInput
from app.collector.mineru_client import MinerUClient
from app.config import Settings
from app.prompts.loader import (
    load_po_order_extraction_prompt,
    load_po_order_vision_prompt,
)
from app.schemas.analysis import (
    AnalysisContext,
    FieldCandidate,
    FieldMetadata,
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
from app.service.confidence.confidence_service import ConfidenceService
from app.service.conflict.conflict_resolver import ConflictResolver
from app.service.context_builder import ParsedContextFilesLoader
from app.service.deterministic_extractor import extract_deterministic_candidates
from app.service.normalization.candidate_normalizer import CandidateNormalizer
from app.service.requirements.po_order_requirements import PoOrderRequirementService
from app.service.result_builder.result_builder import ResultBuilder
from app.service.validation.candidate_validator import CandidateValidator
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
        self.mineru = MinerUClient(settings, self.file_store)
        self.deepseek = DeepSeekExtractionAgent(settings)
        self.system_prompt = load_po_order_extraction_prompt()
        self.vision_prompt = load_po_order_vision_prompt()
        self.context_loader = ParsedContextFilesLoader(self.file_store)
        self.candidate_normalizer = CandidateNormalizer()
        self.candidate_validator = CandidateValidator()
        self.conflict_resolver = ConflictResolver()
        self.confidence_service = ConfidenceService()
        self.result_builder = ResultBuilder()
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
        uploaded_path, _ = self.file_store.save_upload(upload, task_id, document_id)
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

        task_root = self.file_store.root / "analysis_tasks"
        tasks: list[dict[str, Any]] = []
        for status_path in task_root.glob("*/*_task_status.json"):
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
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )

    def enqueue_task(self, task_id: str) -> dict[str, Any]:
        """将任务置为待运行状态并返回任务快照。"""

        status = self.get_task_status(task_id)
        if status.get("status") == "running":
            raise ValueError("TASK_ALREADY_RUNNING")
        self._update_status(
            task_id,
            status="queued",
            progress=0,
            current_stage="queued",
            error=None,
        )
        return self.get_task_status(task_id)

    async def iter_task_events(self, task_id: str) -> AsyncIterator[str]:
        """持续读取任务事件并转换为 SSE 消息。"""

        self.get_task_status(task_id)
        log_path = self.file_store.process_log_path(task_id)
        line_index = 0
        last_status = ""
        while True:
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
            "document_id": status["document_id"],
            "uploaded_path": str(self.file_store.root / status["uploaded_path"]),
            "source_name": status.get("original_name") or Path(status["uploaded_path"]).name,
            "schema_version": status.get("schema_version", "po_order.v1"),
            "context": status.get("context") or {},
        }
        workflow = AnalysisGraph(
            handlers=WorkflowHandlers(
                parse_with_mineru=self.parse_with_mineru,
                read_images_with_vlm=self.read_images_with_vlm,
                extract_candidates=self.extract_candidates,
                normalize_candidates=self.normalize_candidates,
                validate_candidates=self.validate_candidates,
                resolve_conflicts=self.resolve_conflicts,
                calculate_confidence=self.calculate_confidence,
                finalize_result=self.finalize_result,
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

    async def read_images_with_vlm(self, state: AnalysisState) -> str:
        """读取解析图片并生成视觉理解内容。"""

        task_id = state["task_id"]
        images = self._load_images(state.get("image_paths", []))
        vlm_content = await self.deepseek.describe_images(
            self.vision_prompt,
            images,
        )
        parsed_directory = Path(state["parsed"]["parsed_directory"])
        source_stem = Path(self.file_store.safe_filename(state["source_name"])).stem
        vlm_path = parsed_directory / (
            f"{state['document_id']}_{source_stem}_vlm_image_content.md"
        )
        self.file_store.write_text_atomic(vlm_path, vlm_content)
        self._update_status(
            task_id,
            vlm_image_content_path=self.file_store.relative_path(vlm_path),
        )
        return vlm_content

    async def parse_with_mineru(self, state: AnalysisState) -> dict[str, Any]:
        """为一份文件调用一次 MinerU 并持久化解析结果。"""

        task_id = state["task_id"]
        source_path = Path(state["uploaded_path"])
        result = await self.mineru.parse_single_file(
            source_path=source_path,
            source_name=state["source_name"],
            task_id=task_id,
            document_id=state["document_id"],
        )
        return {
            "parsed_directory": str(result.parsed_directory),
            "markdown_path": str(result.markdown_path) if result.markdown_path else None,
            "metadata_path": str(result.result_metadata_path),
            "batch_id": result.batch_id,
            "image_paths": [
                str(path)
                for path in self.file_store.list_files(result.parsed_directory)
                if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ],
        }

    async def extract_candidates(self, state: AnalysisState) -> list[dict[str, Any]]:
        """合并确定性规则候选和 DeepSeek 多模态候选。"""

        task_id = state["task_id"]
        parsed = state["parsed"]
        context_files = self.context_loader.load(
            Path(parsed["parsed_directory"]),
        )
        self._update_status(
            task_id,
            full_markdown_path=(
                self.file_store.relative_path(context_files.full_markdown_path)
                if context_files.full_markdown_path
                else None
            ),
            structured_json_path=(
                self.file_store.relative_path(context_files.structured_json_path)
                if context_files.structured_json_path
                else None
            ),
        )
        deterministic = extract_deterministic_candidates(
            context_files.full_markdown,
            state["document_id"],
        )
        model_candidates = await self.deepseek.extract(
            system_prompt=self.system_prompt,
            full_markdown=context_files.full_markdown,
            structured_json=context_files.structured_json,
            full_markdown_name=(
                context_files.full_markdown_path.name
                if context_files.full_markdown_path
                else "full.md（缺失）"
            ),
            structured_json_name=(
                context_files.structured_json_path.name
                if context_files.structured_json_path
                else "content_list_v2.json（缺失）"
            ),
            deterministic_candidates=deterministic,
            context=state["context"],
            vlm_image_content=state.get("vlm_image_content", ""),
        )
        allowed_keys = set(PO_ORDER_KEYS)
        all_candidates = [
            candidate
            for candidate in deterministic + model_candidates
            if candidate.field_key in allowed_keys
        ]
        self.file_store.write_json_atomic(
            self.file_store.candidates_path(task_id),
            {"task_id": task_id, "candidates": [candidate.model_dump(mode="json") for candidate in all_candidates]},
        )
        return [candidate.model_dump(mode="json") for candidate in all_candidates]

    async def normalize_candidates(self, state: AnalysisState) -> list[dict[str, Any]]:
        """标准化候选值并保存中间结果。"""

        candidates = [
            FieldCandidate.model_validate(item)
            for item in state.get("candidates", [])
        ]
        normalized = self.candidate_normalizer.normalize(candidates)
        self.file_store.write_json_atomic(
            self.file_store.normalized_candidates_path(state["task_id"]),
            {
                "task_id": state["task_id"],
                "candidates": [item.model_dump(mode="json") for item in normalized],
            },
        )
        return [item.model_dump(mode="json") for item in normalized]

    async def validate_candidates(self, state: AnalysisState) -> list[dict[str, Any]]:
        """校验候选值并保存中间结果。"""

        candidates = [
            FieldCandidate.model_validate(item)
            for item in state.get("normalized_candidates", [])
        ]
        validated = self.candidate_validator.validate(candidates)
        self.file_store.write_json_atomic(
            self.file_store.validated_candidates_path(state["task_id"]),
            {
                "task_id": state["task_id"],
                "candidates": [item.model_dump(mode="json") for item in validated],
            },
        )
        return [item.model_dump(mode="json") for item in validated]

    async def resolve_conflicts(self, state: AnalysisState) -> dict[str, dict[str, Any]]:
        """处理候选冲突并保存字段决议。"""

        candidates = [
            FieldCandidate.model_validate(item)
            for item in state.get("validated_candidates", [])
        ]
        resolved = self.conflict_resolver.resolve(candidates)
        self.file_store.write_json_atomic(
            self.file_store.resolved_fields_path(state["task_id"]),
            {
                "task_id": state["task_id"],
                "fields": {
                    key: value.model_dump(mode="json")
                    for key, value in resolved.items()
                },
            },
        )
        return {
            key: value.model_dump(mode="json")
            for key, value in resolved.items()
        }

    async def calculate_confidence(self, state: AnalysisState) -> float:
        """计算任务整体置信度。"""

        field_meta = {
            key: FieldMetadata.model_validate(value)
            for key, value in state.get("resolved_fields", {}).items()
        }
        return self.confidence_service.calculate_overall(field_meta)

    async def finalize_result(self, state: AnalysisState) -> dict[str, Any]:
        """选择有证据支撑的值，完成校验并持久化 JSON 文件。"""

        task_id = state["task_id"]
        field_meta = {
            key: FieldMetadata.model_validate(value)
            for key, value in state.get("resolved_fields", {}).items()
        }
        analysis_result = self.result_builder.build(
            task_id=task_id,
            schema_version=state.get("schema_version", "po_order.v1"),
            field_meta=field_meta,
            context=state["context"],
            overall_confidence=state.get("overall_confidence", 0.0),
            requirement_service=self.requirement_service,
        )
        result_path = self.file_store.result_path(task_id)
        self.file_store.write_json_atomic(result_path, analysis_result.model_dump(mode="json"))
        self._update_status(
            task_id,
            status=analysis_result.overall_status,
            progress=100,
            current_stage="completed",
            result_path=self.file_store.relative_path(result_path),
        )
        return analysis_result.model_dump(mode="json")

    def _load_images(self, paths: list[str]) -> list[ImageInput]:
        """加载数量受限的 MinerU 页面图片供 VLM 复核。"""

        images: list[ImageInput] = []
        for raw_path in paths:
            path = Path(raw_path)
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            media_type = mimetypes.guess_type(path.name)[0] or "image/png"
            images.append(
                ImageInput(
                    content=self.file_store.read_bytes(path),
                    media_type=media_type,
                    name=path.name,
                )
            )
            if len(images) >= 6:
                break
        return images

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
