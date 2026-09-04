from pathlib import Path

from app.config import Settings
from app.storage.file_store import FileStore


def test_file_store_creates_named_runtime_directories(tmp_path: Path) -> None:
    settings = Settings(DOCMIND_DATA_ROOT=tmp_path)
    store = FileStore(settings)

    assert store.root == tmp_path.resolve()
    for directory_name in FileStore.DIRECTORY_NAMES:
        assert (tmp_path / directory_name).is_dir()


def test_safe_filename_removes_path_components_and_unsafe_characters() -> None:
    safe_name = FileStore.safe_filename(r"..\客户 托书?.PDF")
    assert safe_name.endswith(".pdf")
    assert "\\" not in safe_name
    assert "?" not in safe_name
    assert FileStore.safe_filename("booking-request.xls") == "booking-request.xls"


def test_task_id_contains_the_source_filename(tmp_path: Path) -> None:
    store = FileStore(Settings(DOCMIND_DATA_ROOT=tmp_path))

    task_id = store.new_task_id("托书20260826 R3 TEC.xls")

    assert "托书20260826_R3_TEC" in task_id
