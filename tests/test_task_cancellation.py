"""任务取消（中断）能力测试。

覆盖：取消落盘与事件、幂等（不覆盖已完成结果）、节点入口检查点、
asyncio 句柄中断，以及取消后 SSE 事件流能正常结束。
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.service.analysis_service import AnalysisService
from app.workflow.errors import TaskCancelledError


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


async def test_cancel_task_marks_running_task_cancelled(tmp_path: Path) -> None:
    """取消在途任务：状态收敛为 cancelled，且写入取消事件。"""

    service = make_service(tmp_path)
    seed_task(service, "task_a", "running")

    result = service.cancel_task("task_a")

    assert result["status"] == "cancelled"
    status = service.get_task_status("task_a")
    assert status["status"] == "cancelled"
    assert status["current_stage"] == "cancelled"
    assert status["completed_at"] is not None
    # 取消是用户主动行为，不写 error 字段（前端按"已取消"展示而非失败卡片）
    assert not status.get("error")
    assert any(
        event.get("event_type") == "task_cancelled"
        for event in service.read_task_events("task_a")
    )


async def test_cancel_task_keeps_completed_result(tmp_path: Path) -> None:
    """幂等：任务已完成时取消让位于结果，不改写状态也不追加事件。"""

    service = make_service(tmp_path)
    seed_task(service, "task_done", "needs_review")

    result = service.cancel_task("task_done")

    assert result["status"] == "needs_review"
    assert service.get_task_status("task_done")["status"] == "needs_review"
    assert service.read_task_events("task_done") == []


def test_cancel_unknown_task_raises(tmp_path: Path) -> None:
    """任务不存在时抛 FileNotFoundError，由路由转换为 404。"""

    service = make_service(tmp_path)
    with pytest.raises(FileNotFoundError):
        service.cancel_task("task_missing")


def test_raise_if_cancelled_hits_flag(tmp_path: Path) -> None:
    """cancel_requested 落盘后，节点入口检查点立即终止工作流。"""

    service = make_service(tmp_path)
    seed_task(service, "task_b", "running", cancel_requested=True)

    with pytest.raises(TaskCancelledError):
        service._raise_if_cancelled("task_b")


def test_raise_if_cancelled_passes_normal_task(tmp_path: Path) -> None:
    """未取消的任务正常通过检查点。"""

    service = make_service(tmp_path)
    seed_task(service, "task_c", "running")

    service._raise_if_cancelled("task_c")


def test_raise_if_cancelled_ignores_missing_task(tmp_path: Path) -> None:
    """状态文件缺失时不误判为取消，交由后续节点自行报错。"""

    service = make_service(tmp_path)

    service._raise_if_cancelled("task_none")


async def test_node_entry_stops_cancelled_task(tmp_path: Path) -> None:
    """取消标志存在时，节点在入口即抛出，不进入 handler 主体。"""

    service = make_service(tmp_path)
    seed_task(service, "task_d", "cancelled", cancel_requested=True)

    with pytest.raises(TaskCancelledError):
        await service.build_result({"task_id": "task_d"})


async def test_start_task_registers_and_cancel_interrupts(tmp_path: Path) -> None:
    """start_task 登记句柄后，cancel_task 能真正中断在途协程。"""

    service = make_service(tmp_path)
    seed_task(service, "task_e", "queued")
    started = asyncio.Event()

    async def slow_run(task_id: str) -> None:
        # 模拟卡在长耗时节点上，等取消信号
        started.set()
        await asyncio.Event().wait()

    service._run_task = slow_run  # type: ignore[assignment]
    service.start_task("task_e")
    await started.wait()
    assert "task_e" in service._running

    service.cancel_task("task_e")
    await asyncio.sleep(0.05)

    assert service.get_task_status("task_e")["status"] == "cancelled"
    # 任务结束后句柄被回收，避免长期占用内存
    assert "task_e" not in service._running


async def test_iter_events_finishes_for_cancelled_task(tmp_path: Path) -> None:
    """cancelled 属于终态：SSE 事件流据此结束，不会一直挂起。"""

    service = make_service(tmp_path)
    seed_task(service, "task_f", "cancelled")

    chunks = [chunk async for chunk in service.iter_task_events("task_f")]

    assert any('"status": "cancelled"' in chunk for chunk in chunks)
