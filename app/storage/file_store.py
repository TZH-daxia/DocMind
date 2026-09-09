import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.schemas.file import UploadedDocument

logger = logging.getLogger(__name__)


class FileStore:
    """管理配置数据根目录下的所有运行文件。

    目录契约：
    - uploaded_documents/<原始文件名>          用户上传的原件；
    - parsed_documents/<task_id>/             单个任务的全部工作文件
      （渲染图片、文本层、VLM 文档、任务状态、事件日志、中间候选 JSON）；
    - analysis_results/<task_id>.json         最终业务结果；
    - reference_cache/                        外部主数据本地缓存（港口 hbinfo）。
    """

    DIRECTORY_NAMES = (
        "uploaded_documents",
        "parsed_documents",
        "analysis_results",
        "reference_cache",
    )

    def __init__(self, settings: Settings) -> None:
        self.root = settings.docmind_data_root.resolve()
        self.ensure_directories()

    def ensure_directories(self) -> None:
        """创建运行时目录结构。"""

        for directory_name in self.DIRECTORY_NAMES:
            (self.root / directory_name).mkdir(parents=True, exist_ok=True)

    def new_task_id(self, source_filename: str | None = None) -> str:
        """创建可读的任务标识。"""

        timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        source_label = ""
        if source_filename:
            source_label = f"_{Path(self.safe_filename(source_filename)).stem}"
        return f"task_{timestamp}{source_label}_{uuid.uuid4().hex[:8]}"

    def new_document_id(self) -> str:
        """创建文档标识。"""

        return f"doc_{uuid.uuid4().hex[:12]}"

    def task_dir(self, category: str, task_id: str) -> Path:
        """返回并创建一个任务的分类目录。"""

        directory = self.root / category / task_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def safe_filename(filename: str) -> str:
        """移除文件名中的路径片段和不安全字符。"""

        name = Path(filename).name
        stem = Path(name).stem
        suffix = Path(name).suffix.lower()
        safe_stem = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE).strip("._") or "document"
        return f"{safe_stem[:120]}{suffix}"

    def save_upload(self, upload: UploadedDocument) -> tuple[Path, bytes]:
        """以原始文件名保存上传文档（同名覆盖）。"""

        content = upload.content
        filename = self.safe_filename(upload.filename or "document")
        destination = self.root / "uploaded_documents" / filename
        self.write_bytes_atomic(destination, content)
        return destination, content

    def write_bytes_atomic(self, destination: Path, content: bytes) -> None:
        """使用同目录临时文件和原子替换写入字节内容。"""

        destination.parent.mkdir(parents=True, exist_ok=True)
        # 临时文件名只保留 uuid，避免把超长目标文件名再拼一遍导致 Windows
        # 260 字符路径上限（MAX_PATH）触发 FileNotFoundError: [Errno 2]。
        temporary = destination.with_name(f".{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)

    def write_text_atomic(self, destination: Path, content: str) -> None:
        """使用原子替换写入文本。"""

        self.write_bytes_atomic(destination, content.encode("utf-8"))

    def append_text(self, destination: Path, content: str) -> None:
        """向文本文件追加内容。"""

        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as file:
            file.write(content)

    def write_json_atomic(self, destination: Path, payload: Any) -> None:
        """序列化并原子写入 JSON。"""

        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        self.write_text_atomic(destination, content)

    def read_json(self, path: Path) -> Any:
        """读取 JSON 文件。"""

        return json.loads(path.read_text(encoding="utf-8"))

    def read_text(self, path: Path, errors: str = "strict") -> str:
        """读取文本文件。"""

        return path.read_text(encoding="utf-8", errors=errors)

    def read_bytes(self, path: Path) -> bytes:
        """读取二进制文件。"""

        return path.read_bytes()

    def source_stem(self, source_name: str | None) -> str:
        """返回源文件的安全文件名主干，用于任务内产物命名。"""

        safe_name = self.safe_filename(source_name or "document")
        return Path(safe_name).stem or "document"

    def find_task_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        """根据幂等 key 查找已有任务快照。"""

        for status_path in self.root.glob("parsed_documents/*/task_status.json"):
            try:
                status = self.read_json(status_path)
            except (OSError, json.JSONDecodeError):
                continue
            if status.get("request_id") == request_id:
                return status
        return None

    def relative_path(self, path: Path) -> str:
        """返回使用 POSIX 分隔符的数据根目录相对路径。"""

        return path.resolve().relative_to(self.root).as_posix()

    def task_status_path(self, task_id: str) -> Path:
        """返回任务状态文件路径。"""

        return self.task_dir("parsed_documents", task_id) / "task_status.json"

    def result_path(self, task_id: str) -> Path:
        """返回最终业务结果文件路径。"""

        return self.root / "analysis_results" / f"{task_id}.json"

    def candidates_path(self, task_id: str, source_stem: str) -> Path:
        """返回候选值文件路径。"""

        return self.task_dir("parsed_documents", task_id) / f"{source_stem}_candidates.json"

    def process_log_path(self, task_id: str) -> Path:
        """返回处理日志文件路径。"""

        return self.task_dir("parsed_documents", task_id) / "process.log"

    def cleanup_expired(self, retention_hours: float) -> list[str]:
        """删除超过保留期的运行产物，返回被清理条目的相对路径。

        清理范围：parsed_documents/task_*（整目录，running 状态跳过，防止误删
        在途任务）、uploaded_documents 与 analysis_results 下的文件；
        reference_cache 是带自身 TTL 的主数据缓存，不参与清理。
        retention_hours <= 0 表示永久保留（禁用清理）。
        """

        if retention_hours <= 0:
            return []
        cutoff = time.time() - retention_hours * 3600
        removed: list[str] = []
        for task_dir in sorted((self.root / "parsed_documents").glob("task_*")):
            if not task_dir.is_dir():
                continue
            try:
                status = self.read_json(task_dir / "task_status.json")
            except (OSError, ValueError):
                status = None
            if isinstance(status, dict) and status.get("status") == "running":
                logger.info("任务仍在运行，跳过清理：%s", task_dir.name)
                continue
            removed.extend(self._remove_if_expired(task_dir, cutoff))
        for category in ("uploaded_documents", "analysis_results"):
            directory = self.root / category
            for path in sorted(directory.iterdir()):
                removed.extend(self._remove_if_expired(path, cutoff))
        if removed:
            logger.info("已清理 %s 个超过保留期的运行产物", len(removed))
        return removed

    def _remove_if_expired(self, path: Path, cutoff: float) -> list[str]:
        """单个文件/目录超过保留期则删除，返回被删除的相对路径。"""

        try:
            if path.stat().st_mtime > cutoff:
                return []
        except OSError:
            return []
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            logger.warning("清理过期产物失败（可能被占用）：%s（%s）", path, exc)
            return []
        return [self.relative_path(path)]
