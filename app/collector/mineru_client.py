import asyncio
import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)


class MinerUApiError(RuntimeError):
    """MinerU 请求或结果失败时抛出的异常。"""


@dataclass(frozen=True)
class MinerUParseResult:
    """一次 MinerU 任务生成的路径和元数据。"""

    batch_id: str
    parsed_directory: Path
    markdown_path: Path | None
    result_metadata_path: Path


class MinerUClient:
    """通过 MinerU 官方本地文件上传接口处理单份文件。"""

    def __init__(self, settings: Settings, file_store: FileStore) -> None:
        self.settings = settings
        self.file_store = file_store
        if not settings.mineru_api_key:
            raise ValueError("MINERU_API_KEY is required")

    async def parse_single_file(
        self,
        source_path: Path,
        source_name: str,
        task_id: str,
        document_id: str,
    ) -> MinerUParseResult:
        """申请一个上传地址，上传一份文件并获取解析结果。"""

        parsed_directory = self.file_store.task_dir("parsed_documents", task_id)
        async with httpx.AsyncClient(
            base_url=self.settings.mineru_base_url.rstrip("/"),
            timeout=httpx.Timeout(self.settings.mineru_timeout_seconds),
            follow_redirects=True,
        ) as client:
            upload_info = await self._request_upload_url(client, source_name, document_id)
            await self._upload_file(client, upload_info["upload_url"], source_path)
            result = await self._poll_result(client, upload_info["batch_id"], source_name)
            zip_bytes = await self._download_result(client, result["full_zip_url"])

        source_label = self.file_store.safe_filename(source_name)
        source_stem = Path(source_label).stem
        zip_path = parsed_directory / f"{document_id}_{source_stem}_mineru_result.zip"
        self.file_store.write_bytes_atomic(zip_path, zip_bytes)
        self._extract_zip_safely(zip_path, parsed_directory)

        metadata_path = parsed_directory / f"{document_id}_{source_stem}_mineru_result.json"
        self.file_store.write_json_atomic(
            metadata_path,
            {
                "batch_id": upload_info["batch_id"],
                "document_id": document_id,
                "source_file": source_name,
                "result": result,
            },
        )
        markdown_path = self._find_markdown(parsed_directory)
        return MinerUParseResult(
            batch_id=upload_info["batch_id"],
            parsed_directory=parsed_directory,
            markdown_path=markdown_path,
            result_metadata_path=metadata_path,
        )

    async def _request_upload_url(
        self,
        client: httpx.AsyncClient,
        source_name: str,
        document_id: str,
    ) -> dict[str, str]:
        """申请一份本地文件的 MinerU 签名上传地址。"""

        payload = {
            "files": [
                {
                    "name": source_name,
                    "data_id": document_id,
                    "is_ocr": True,
                }
            ],
            "model_version": self.settings.mineru_model_version,
            "language": self.settings.mineru_language,
            "enable_formula": True,
            "enable_table": True,
        }
        response = await client.post(
            "/file-urls/batch",
            headers=self._auth_headers(),
            json=payload,
        )
        data = self._parse_response(response, "申请上传地址")
        body = data.get("data") or {}
        batch_id = str(body.get("batch_id") or "")
        file_urls = body.get("file_urls") or []
        if not batch_id or not file_urls:
            raise MinerUApiError("MinerU 未返回单文件上传地址")
        first = file_urls[0]
        upload_url = first if isinstance(first, str) else first.get("url") or first.get("upload_url")
        if not upload_url:
            raise MinerUApiError("MinerU 返回的上传地址无效")
        return {"batch_id": batch_id, "upload_url": upload_url}

    async def _upload_file(
        self,
        client: httpx.AsyncClient,
        upload_url: str,
        source_path: Path,
    ) -> None:
        """将本地文件上传到 MinerU 返回的签名地址。"""

        response = await client.put(
            upload_url,
            content=source_path.read_bytes(),
            timeout=self.settings.mineru_timeout_seconds,
        )
        if response.status_code >= 400:
            raise MinerUApiError(f"MinerU 文件上传失败，HTTP 状态码：{response.status_code}")

    async def _poll_result(
        self,
        client: httpx.AsyncClient,
        batch_id: str,
        source_name: str,
    ) -> dict[str, Any]:
        """轮询单文件解析结果，直到任务完成或失败。"""

        deadline = asyncio.get_running_loop().time() + self.settings.mineru_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            response = await client.get(
                f"/extract-results/batch/{batch_id}",
                headers=self._auth_headers(),
            )
            data = self._parse_response(response, "查询解析结果")
            body = data.get("data") or {}
            results = body.get("extract_result") or body.get("results") or []
            if isinstance(results, dict):
                results = [results]
            current = self._select_result(results, source_name)
            state = str(current.get("state") or current.get("status") or "").lower()
            if state in {"done", "success", "succeeded", "completed"}:
                if not current.get("full_zip_url"):
                    raise MinerUApiError("MinerU 已完成但未返回 full_zip_url")
                return current
            if state in {"failed", "error", "cancelled"}:
                error_message = current.get("err_msg") or current.get("error") or "未知错误"
                raise MinerUApiError(f"MinerU 解析失败：{error_message}")
            await asyncio.sleep(self.settings.mineru_poll_interval_seconds)
        raise MinerUApiError("MinerU 解析超时")

    async def _download_result(
        self,
        client: httpx.AsyncClient,
        result_url: str,
    ) -> bytes:
        """下载 MinerU 已完成的解析结果压缩包。"""

        try:
            response = await client.get(
                result_url,
                timeout=self.settings.mineru_timeout_seconds,
            )
        except httpx.RequestError as exc:
            logger.warning("通过默认网络下载 MinerU 结果失败，尝试直连：%s", exc.__class__.__name__)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.mineru_timeout_seconds),
                follow_redirects=True,
                trust_env=False,
            ) as direct_client:
                response = await direct_client.get(
                    result_url,
                    timeout=self.settings.mineru_timeout_seconds,
                )
        if response.status_code >= 400:
            raise MinerUApiError(f"MinerU 结果下载失败，HTTP 状态码：{response.status_code}")
        return response.content

    def _auth_headers(self) -> dict[str, str]:
        """构造 MinerU 鉴权请求头，不记录密钥。"""

        return {"Authorization": f"Bearer {self.settings.mineru_api_key}"}

    @staticmethod
    def _parse_response(response: httpx.Response, operation: str) -> dict[str, Any]:
        """校验 MinerU JSON 响应。"""

        if response.status_code >= 400:
            raise MinerUApiError(f"MinerU {operation}失败，HTTP 状态码：{response.status_code}")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise MinerUApiError(f"MinerU {operation}返回了无效 JSON") from exc
        if data.get("code") not in (0, "0", None):
            raise MinerUApiError(f"MinerU {operation}失败：{data.get('msg') or data.get('message')}")
        return data

    @staticmethod
    def _select_result(results: list[dict[str, Any]], source_name: str) -> dict[str, Any]:
        """从结果列表中选择当前单文件对应的结果。"""

        if not results:
            return {}
        for result in results:
            if result.get("file_name") == source_name or result.get("name") == source_name:
                return result
        return results[0]

    @staticmethod
    def _extract_zip_safely(zip_path: Path, destination: Path) -> None:
        """在防止路径穿越的前提下解压结果压缩包。"""

        destination_root = destination.resolve()
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                target = (destination / member.filename).resolve()
                if target != destination_root and destination_root not in target.parents:
                    raise MinerUApiError("MinerU 压缩包包含不安全路径")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as output:
                    output.write(source.read())

    @staticmethod
    def _find_markdown(directory: Path) -> Path | None:
        """在解压目录中查找主要 Markdown 输出文件。"""

        markdown_files = sorted(directory.rglob("*.md"))
        if not markdown_files:
            logger.warning("MinerU 结果中没有 Markdown 文件：%s", directory)
            return None
        return next(
            (path for path in markdown_files if path.name.lower() in {"full.md", "content.md"}),
            markdown_files[0],
        )
