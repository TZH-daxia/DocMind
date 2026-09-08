"""启动时中断任务恢复（孤儿 running 状态清理）测试。"""

import json
from pathlib import Path

from app.config import Settings
from app.service.analysis_service import AnalysisService


def make_service(tmp_path: Path) -> AnalysisService:
    settings = Settings(DOCMIND_DATA_ROOT=tmp_path, deepseek_api_key="test-key")
    return AnalysisService(settings)


def write_status(root: Path, task_id: str, status: str) -> None:
    task_dir = root / "parsed_documents" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    payload = {"task_id": task_id, "status": status}
    (task_dir / "task_status.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


async def test_recover_marks_running_tasks_failed(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    write_status(tmp_path, "task_running_a", "running")
    write_status(tmp_path, "task_done", "needs_review")
    write_status(tmp_path, "task_running_b", "running")

    recovered = service.recover_interrupted_tasks()

    assert sorted(recovered) == ["task_running_a", "task_running_b"]
    for task_id in ("task_running_a", "task_running_b"):
        status = service.get_task_status(task_id)
        assert status["status"] == "failed"
        assert status["error"]["code"] == "TASK_INTERRUPTED"
        assert status["completed_at"] is not None
    # 非 running 状态不受影响
    assert service.get_task_status("task_done")["status"] == "needs_review"


async def test_recover_skips_corrupted_status_file(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    task_dir = tmp_path / "parsed_documents" / "task_broken"
    task_dir.mkdir(parents=True)
    (task_dir / "task_status.json").write_text("{broken json", encoding="utf-8")

    assert service.recover_interrupted_tasks() == []
