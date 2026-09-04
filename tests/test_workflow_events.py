from pathlib import Path

from app.config import Settings
from app.service.analysis_service import AnalysisService
from app.storage.file_store import FileStore
from app.workflow.events import WorkflowEvent


def test_service_publishes_node_event_to_status_and_process_log(tmp_path: Path) -> None:
    service = object.__new__(AnalysisService)
    service.file_store = FileStore(Settings(DOCMIND_DATA_ROOT=tmp_path))
    task_id = "task_event"
    service.file_store.write_json_atomic(
        service.file_store.task_status_path(task_id),
        {"task_id": task_id, "status": "queued"},
    )

    service.publish(
        WorkflowEvent(
            task_id=task_id,
            node_name="parse_with_mineru",
            event_type="started",
            progress=20,
            message="开始执行",
            started_at="2026-09-03T12:00:00+08:00",
        )
    )

    status = service.get_task_status(task_id)
    process_log = service.file_store.process_log_path(task_id).read_text(encoding="utf-8")
    assert status["current_stage"] == "parse_with_mineru"
    assert status["progress"] == 20
    assert status["last_node_event"]["event_type"] == "started"
    assert '"node_name": "parse_with_mineru"' in process_log
