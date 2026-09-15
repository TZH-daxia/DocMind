"""任务暂停与恢复测试。

覆盖：暂停落盘与事件、暂停/恢复的幂等与状态约束、按磁盘产物推断断点，
以及恢复执行时已完成节点不被重跑（不重复调用 LibreOffice 与模型）。
"""

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.config import Settings
from app.schemas.analysis import FieldCandidate
from app.schemas.po_order import PO_ORDER_KEYS
from app.service.analysis_service import AnalysisService
from app.workflow.errors import TaskPausedError

# 带托书特征的视觉内容：否则会被文档类型守卫提前拦下
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


def seed_task(service: AnalysisService, task_id: str, status: str, **extra: Any) -> None:
    service.file_store.write_json_atomic(
        service.file_store.task_status_path(task_id),
        {"task_id": task_id, "status": status, **extra},
    )


def write_render_artifacts(
    service: AnalysisService, task_id: str, stem: str = "booking"
) -> Path:
    """造出 render 节点的产物：页面 PNG + render_meta.json。"""

    task_dir = service.file_store.task_dir("parsed_documents", task_id)
    names = [f"{stem}_page_001.png"]
    # 真实 PNG：read_images_with_vlm 会读取并按四象限裁剪，假字节会打不开
    Image.new("RGB", (24, 24), "white").save(task_dir / names[0])
    service.file_store.write_json_atomic(
        task_dir / f"{stem}_render_meta.json",
        {"converter": "pymupdf", "page_count": 1, "images": names},
    )
    return task_dir


def write_vlm_artifact(service: AnalysisService, task_id: str, stem: str = "booking") -> None:
    task_dir = service.file_store.task_dir("parsed_documents", task_id)
    service.file_store.write_text_atomic(
        task_dir / f"{stem}_vlm_image_content.md", BOOKING_LIKE_CONTENT
    )


def write_candidates_artifact(
    service: AnalysisService, task_id: str, stem: str = "booking"
) -> None:
    service.file_store.write_json_atomic(
        service.file_store.candidates_path(task_id, stem),
        {"task_id": task_id, "candidates": []},
    )


# ---------- 暂停 / 恢复的状态机 ----------


async def test_pause_task_marks_task_paused(tmp_path: Path) -> None:
    """暂停在途任务：状态收敛为 paused，且不是终态（可恢复）。"""

    service = make_service(tmp_path)
    seed_task(service, "task_pause_1", "running", progress=45)

    result = service.pause_task("task_pause_1")

    assert result["status"] == "paused"
    assert result["current_stage"] == "paused"
    assert result["interrupt_requested"] == "pause"
    # 暂停不是失败：不写 error；也不该置 completed_at（任务还没结束）
    assert not result.get("error")
    assert not result.get("completed_at")
    # 进度保留，恢复时前端能接着显示
    assert result["progress"] == 45
    assert any(
        event.get("event_type") == "task_paused"
        for event in service.read_task_events("task_pause_1")
    )


async def test_pause_task_is_idempotent(tmp_path: Path) -> None:
    """已暂停或已到终态的任务再次暂停：原样返回，不重复写事件。"""

    service = make_service(tmp_path)
    seed_task(service, "task_pause_2", "needs_review")

    assert service.pause_task("task_pause_2")["status"] == "needs_review"
    assert service.read_task_events("task_pause_2") == []

    seed_task(service, "task_pause_3", "paused")
    before = service.read_task_events("task_pause_3")
    assert service.pause_task("task_pause_3")["status"] == "paused"
    assert service.read_task_events("task_pause_3") == before


def test_resume_task_requeues_paused_task(tmp_path: Path) -> None:
    """恢复：paused → queued，清掉中断意图并重新启动工作流。"""

    service = make_service(tmp_path)
    seed_task(service, "task_resume_1", "paused", interrupt_requested="pause")
    started: list[str] = []
    service.start_task = lambda task_id: started.append(task_id)  # type: ignore[assignment]

    result = service.resume_task("task_resume_1")

    assert result["status"] == "queued"
    assert result["interrupt_requested"] is None
    assert started == ["task_resume_1"]
    assert any(
        event.get("event_type") == "task_resumed"
        for event in service.read_task_events("task_resume_1")
    )


def test_resume_task_ignores_non_paused_status(tmp_path: Path) -> None:
    """只有 paused 可恢复：运行中/已完成的任务不会被误启动。"""

    service = make_service(tmp_path)
    seed_task(service, "task_resume_2", "running")
    started: list[str] = []
    service.start_task = lambda task_id: started.append(task_id)  # type: ignore[assignment]

    assert service.resume_task("task_resume_2")["status"] == "running"
    assert started == []


def test_raise_if_interrupted_hits_pause_flag(tmp_path: Path) -> None:
    """pause 中断意图落盘后，节点入口抛出 TaskPausedError。"""

    service = make_service(tmp_path)
    seed_task(service, "task_pause_4", "running", interrupt_requested="pause")

    with pytest.raises(TaskPausedError):
        service._raise_if_interrupted("task_pause_4")


# ---------- 断点推断：以磁盘产物为准 ----------


async def test_restore_state_reports_nothing_for_fresh_task(tmp_path: Path) -> None:
    """全新任务没有任何产物：断点为空，工作流从头开始。"""

    service = make_service(tmp_path)
    state: Any = {"task_id": "task_fresh", "source_name": "booking.pdf"}

    assert service._restore_state_from_disk(state) == []


async def test_restore_state_collects_completed_nodes_from_disk(tmp_path: Path) -> None:
    """产物存在即该节点已完成，且 state 被补齐供后续节点使用。"""

    service = make_service(tmp_path)
    task_id = "task_restore_1"
    write_render_artifacts(service, task_id)
    state: Any = {"task_id": task_id, "source_name": "booking.pdf"}

    completed = service._restore_state_from_disk(state)

    assert completed == ["render_document"]
    # render 的产物被回填，后续 VLM 节点才能直接读到图片
    assert state["image_paths"]
    assert state["parsed"]["parsed_directory"]


async def test_restore_state_collects_all_nodes(tmp_path: Path) -> None:
    """四类产物齐全时，四个节点全部判定为已完成。"""

    service = make_service(tmp_path)
    task_id = "task_restore_2"
    write_render_artifacts(service, task_id)
    write_vlm_artifact(service, task_id)
    write_candidates_artifact(service, task_id)
    service.file_store.write_json_atomic(
        service.file_store.result_path(task_id), {"task_id": task_id}
    )
    state: Any = {"task_id": task_id, "source_name": "booking.pdf"}

    assert service._restore_state_from_disk(state) == [
        "render_document",
        "read_images_with_vlm",
        "extract_candidates",
        "build_result",
    ]


# ---------- 恢复执行不重跑已完成节点 ----------


async def test_resume_run_skips_completed_render_node(tmp_path: Path) -> None:
    """render 产物已存在时，恢复执行不会再调用渲染器（不重跑 LibreOffice）。"""

    service = make_service(tmp_path)
    task_id = "task_skip_render"
    write_render_artifacts(service, task_id)
    seed_task(
        service,
        task_id,
        "queued",
        original_name="booking.pdf",
        uploaded_path=f"parsed_documents/{task_id}/booking.pdf",
        schema_version="po_order.v1",
        context={},
    )

    calls: list[str] = []

    def fake_render(source: Path, source_name: str, tid: str) -> Any:
        calls.append("render")
        raise AssertionError("render_document 产物已存在，不应被重跑")

    async def fake_describe(prompt: str, images: list[Any]) -> str:
        calls.append("vlm")
        return BOOKING_LIKE_CONTENT

    async def fake_extract(**kwargs: Any) -> list[FieldCandidate]:
        calls.append("extract")
        return [
            FieldCandidate(field_key=key, value=None, status="missing")
            for key in PO_ORDER_KEYS
        ]

    service.renderer.render = fake_render  # type: ignore[assignment]
    service.deepseek.describe_images = fake_describe  # type: ignore[assignment]
    service.deepseek.extract = fake_extract  # type: ignore[assignment]

    await service.process_task(task_id)

    # 从 VLM 节点开始，渲染节点被整体跳过
    assert calls[:2] == ["vlm", "extract"]
    assert "render" not in calls


async def test_resume_run_skips_render_and_vlm(tmp_path: Path) -> None:
    """render 与 VLM 产物都在时，恢复只跑抽取与收尾，不再调用视觉模型。"""

    service = make_service(tmp_path)
    task_id = "task_skip_vlm"
    write_render_artifacts(service, task_id)
    write_vlm_artifact(service, task_id)
    seed_task(
        service,
        task_id,
        "queued",
        original_name="booking.pdf",
        uploaded_path=f"parsed_documents/{task_id}/booking.pdf",
        schema_version="po_order.v1",
        context={},
    )

    calls: list[str] = []

    async def fake_describe(prompt: str, images: list[Any]) -> str:
        calls.append("vlm")
        raise AssertionError("VLM 产物已存在，不应重新调用视觉模型")

    async def fake_extract(**kwargs: Any) -> list[FieldCandidate]:
        calls.append("extract")
        return [
            FieldCandidate(field_key=key, value=None, status="missing")
            for key in PO_ORDER_KEYS
        ]

    service.deepseek.describe_images = fake_describe  # type: ignore[assignment]
    service.deepseek.extract = fake_extract  # type: ignore[assignment]

    await service.process_task(task_id)

    assert calls == ["extract"]
