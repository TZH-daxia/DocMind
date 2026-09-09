"""数据保留期清理（cleanup_expired）测试。"""

import os
import time
from pathlib import Path

from app.config import Settings
from app.storage.file_store import FileStore


def make_store(tmp_path: Path) -> FileStore:
    return FileStore(Settings(DOCMIND_DATA_ROOT=tmp_path, deepseek_api_key="test-key"))


def age_path(path: Path, days: float) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def write_task(store: FileStore, task_id: str, status: str = "completed") -> Path:
    task_dir = store.task_dir("parsed_documents", task_id)
    store.write_json_atomic(
        store.task_status_path(task_id), {"task_id": task_id, "status": status}
    )
    (task_dir / "page_001.png").write_bytes(b"x")
    return task_dir


def test_expired_task_dir_removed(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    task_dir = write_task(store, "task_old")
    age_path(task_dir, 2)

    removed = store.cleanup_expired(24.0)

    assert not task_dir.exists()
    assert any("task_old" in item for item in removed)


def test_running_task_kept(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    task_dir = write_task(store, "task_running", status="running")
    age_path(task_dir, 2)

    removed = store.cleanup_expired(24.0)

    assert task_dir.exists()
    assert removed == []


def test_recent_task_kept(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    task_dir = write_task(store, "task_fresh")

    removed = store.cleanup_expired(24.0)

    assert task_dir.exists()
    assert removed == []


def test_upload_and_result_files_removed(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    upload = tmp_path / "uploaded_documents" / "a.pdf"
    upload.write_bytes(b"x")
    result = tmp_path / "analysis_results" / "task_old.json"
    result.write_text("{}", encoding="utf-8")
    age_path(upload, 2)
    age_path(result, 2)

    removed = store.cleanup_expired(24.0)

    assert not upload.exists() and not result.exists()
    assert len(removed) == 2


def test_reference_cache_untouched(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    cache = tmp_path / "reference_cache" / "hbinfo.json"
    cache.write_text("{}", encoding="utf-8")
    age_path(cache, 30)

    removed = store.cleanup_expired(24.0)

    assert cache.exists()
    assert removed == []


def test_zero_retention_disables_cleanup(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    task_dir = write_task(store, "task_old")
    age_path(task_dir, 30)

    removed = store.cleanup_expired(0)

    assert task_dir.exists()
    assert removed == []
