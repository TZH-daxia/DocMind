from pathlib import Path

from app.config import Settings
from app.service.context_builder import ParsedContextFilesLoader
from app.storage.file_store import FileStore


def test_context_loader_returns_two_independent_files(tmp_path: Path) -> None:
    settings = Settings(DOCMIND_DATA_ROOT=tmp_path)
    store = FileStore(settings)
    parsed_directory = store.task_dir("parsed_documents", "task_context")
    store.write_text_atomic(parsed_directory / "full.md", "# 托书\nGross Weight: 74kg")
    store.write_json_atomic(
        parsed_directory / "content_list_v2.json",
        [[
            {
                "type": "table",
                "page_idx": 0,
                "content": {"html": "<table><tr><td>74kg</td></tr></table>"},
            }
        ]],
    )

    context_files = ParsedContextFilesLoader(store).load(parsed_directory)

    assert context_files.full_markdown == "# 托书\nGross Weight: 74kg"
    assert '"page_idx": 0' in context_files.structured_json
    assert context_files.full_markdown_path is not None
    assert context_files.structured_json_path is not None
    assert not list(parsed_directory.glob("*model_context.md"))
