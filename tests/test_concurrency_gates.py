"""多用户并发闸门与 LibreOffice UNO 端口竞态的测试。"""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.collector.document_renderer import LocalDocumentRenderer
from app.config import Settings
from app.schemas.analysis import FieldCandidate
from app.service.analysis_service import AnalysisService
from app.storage.file_store import FileStore

# 带托书特征的视觉内容：否则会被文档类型守卫提前拦下，测不到抽取闸门
BOOKING_LIKE_CONTENT = (
    "Shipper: ACME TRADING LTD\nAirport of Departure: SHANGHAI\n"
    "Consignee: BASEL LOGISTICS GMBH\n件数 No of Packages: 12\n"
) * 3


def make_service(tmp_path: Path, **settings_kwargs: Any) -> AnalysisService:
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path,
        deepseek_api_key="test-key",
        **settings_kwargs,
    )
    return AnalysisService(settings)


def seed_task(service: AnalysisService, task_id: str) -> None:
    service.file_store.write_json_atomic(
        service.file_store.task_status_path(task_id),
        {"task_id": task_id, "status": "queued"},
    )


async def test_task_gate_caps_concurrent_tasks(tmp_path: Path) -> None:
    """同时执行的任务数不超过配置上限，其余任务排队等待。"""

    service = make_service(tmp_path, DOCMIND_MAX_CONCURRENT_TASKS=2)
    running = 0
    peak = 0

    async def fake_run(task_id: str) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1

    service._run_task = fake_run  # type: ignore[assignment]
    await asyncio.gather(*(service.process_task(f"task_{i}") for i in range(6)))
    assert peak == 2
    assert running == 0


async def test_task_gate_reuses_one_semaphore(tmp_path: Path) -> None:
    """同名闸门复用同一信号量，避免每次任务新建导致限流失效。"""

    service = make_service(tmp_path, DOCMIND_MAX_CONCURRENT_TASKS=1)
    assert service._gate("task", 1) is service._gate("task", 1)


async def test_libreoffice_gate_caps_concurrent_renders(tmp_path: Path) -> None:
    """LibreOffice 转换并发单独限流，避免同时拉起过多 soffice 进程。"""

    service = make_service(tmp_path, DOCMIND_LO_MAX_CONCURRENT=2)
    running = 0
    peak = 0

    def fake_render(source: Path, source_name: str, task_id: str) -> SimpleNamespace:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        # 用 sleep 模拟转换耗时，使并发窗口可被观测
        time.sleep(0.02)
        running -= 1
        return SimpleNamespace(
            converter="libreoffice",
            page_count=1,
            parsed_directory=tmp_path,
            metadata_path=tmp_path / "meta.json",
            image_paths=[],
        )

    service.renderer.render = fake_render  # type: ignore[assignment]
    for index in range(4):
        task_id = f"render_{index}"
        seed_task(service, task_id)
    await asyncio.gather(
        *(
            service.render_document(
                {
                    "task_id": f"render_{index}",
                    "uploaded_path": str(tmp_path / "a.doc"),
                    "source_name": "a.doc",
                    "schema_version": "po_order.v1",
                    "context": {},
                }
            )
            for index in range(4)
        )
    )
    assert peak <= 2


async def test_model_gate_caps_concurrent_vlm_calls(tmp_path: Path) -> None:
    """视觉识别调用受模型闸门限制，与渲染闸门互不占用。"""

    service = make_service(tmp_path, DOCMIND_MODEL_MAX_CONCURRENT=2)
    running = 0
    peak = 0

    async def fake_describe(prompt: str, images: list[Any]) -> str:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return "视觉内容" * 100

    service.deepseek.describe_images = fake_describe  # type: ignore[assignment]
    states = []
    for index in range(4):
        task_id = f"vlm_{index}"
        seed_task(service, task_id)
        parsed_directory = service.file_store.task_dir("parsed_documents", task_id)
        states.append(
            {
                "task_id": task_id,
                "image_paths": [],
                "source_name": f"a{index}.doc",
                "parsed": {"parsed_directory": str(parsed_directory)},
                "context": {},
            }
        )
    await asyncio.gather(*(service.read_images_with_vlm(item) for item in states))
    assert peak == 2


async def test_model_gate_shared_with_extraction(tmp_path: Path) -> None:
    """字段抽取与视觉识别共用同一模型闸门，上游并发总量可控。"""

    service = make_service(tmp_path, DOCMIND_MODEL_MAX_CONCURRENT=1)
    running = 0
    peak = 0
    candidate = FieldCandidate(
        field_key="sfg", value="SHANGHAI", status="confirmed", confidence=0.9
    )

    async def fake_extract(**kwargs: Any) -> list[FieldCandidate]:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return [candidate]

    service.deepseek.extract = fake_extract  # type: ignore[assignment]
    states = []
    for index in range(3):
        task_id = f"extract_{index}"
        seed_task(service, task_id)
        states.append(
            {
                "task_id": task_id,
                "source_name": f"a{index}.doc",
                "schema_version": "po_order.v1",
                "context": {},
                "vlm_image_content": BOOKING_LIKE_CONTENT,
            }
        )
    await asyncio.gather(*(service.extract_candidates(item) for item in states))
    assert peak == 1


def build_renderer(tmp_path: Path) -> LocalDocumentRenderer:
    soffice = tmp_path / "soffice.exe"
    soffice.write_bytes(b"")
    (tmp_path / "python.exe").write_bytes(b"")
    settings = Settings(
        DOCMIND_DATA_ROOT=tmp_path / "data", DOCMIND_SOFFICE_PATH=str(soffice)
    )
    return LocalDocumentRenderer(FileStore(settings), settings)


def test_xls_uno_retries_when_port_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取到的端口已被占用时换端口重来，不连到别人的 UNO 服务。"""

    renderer = build_renderer(tmp_path)
    out_dir = tmp_path / "task"
    out_dir.mkdir()
    source = tmp_path / "a.xls"
    source.write_bytes(b"fake xls")
    ports: list[int] = []
    checked: list[int] = []

    def fake_free_port() -> int:
        return 45000 + len(checked)

    def fake_port_in_use(port: int) -> bool:
        checked.append(port)
        return len(checked) == 1  # 首个端口被占用，第二个可用

    def fake_export(self: LocalDocumentRenderer, **kwargs: Any) -> None:
        ports.append(kwargs["port"])
        kwargs["pdf_path"].write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(LocalDocumentRenderer, "_free_port", staticmethod(fake_free_port))
    monkeypatch.setattr(
        LocalDocumentRenderer, "_port_in_use", staticmethod(fake_port_in_use)
    )
    monkeypatch.setattr(LocalDocumentRenderer, "_uno_export_once", fake_export)

    pdf_path, converter = renderer._xls_to_pdf_libreoffice(source, out_dir, "a")
    assert converter == "libreoffice_uno"
    assert pdf_path.exists()
    assert ports == [45001]
    assert checked == [45000, 45001]


def test_xls_uno_fails_after_exhausted_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连续失败后明确报错，交由上层回退普通转换。"""

    renderer = build_renderer(tmp_path)
    out_dir = tmp_path / "task"
    out_dir.mkdir()
    source = tmp_path / "a.xls"
    source.write_bytes(b"fake xls")
    attempts: list[int] = []

    monkeypatch.setattr(LocalDocumentRenderer, "_free_port", staticmethod(lambda: 46000))
    monkeypatch.setattr(
        LocalDocumentRenderer, "_port_in_use", staticmethod(lambda port: False)
    )

    def failing_export(self: LocalDocumentRenderer, **kwargs: Any) -> None:
        attempts.append(kwargs["port"])
        raise RuntimeError("LIBREOFFICE_UNO_TIMEOUT")

    monkeypatch.setattr(LocalDocumentRenderer, "_uno_export_once", failing_export)

    with pytest.raises(RuntimeError, match="LIBREOFFICE_UNO_FAILED"):
        renderer._xls_to_pdf_libreoffice(source, out_dir, "a")
    assert len(attempts) == 2
