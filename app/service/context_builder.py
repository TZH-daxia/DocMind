from dataclasses import dataclass
from pathlib import Path

from app.storage.file_store import FileStore


@dataclass(frozen=True)
class ParsedContextFiles:
    """MinerU 解析文件的独立模型输入。"""

    full_markdown_path: Path | None
    structured_json_path: Path | None
    full_markdown: str
    structured_json: str


class ParsedContextFilesLoader:
    """读取适合模型使用的 MinerU 文件，不生成合并文件。"""

    def __init__(self, file_store: FileStore) -> None:
        self.file_store = file_store

    def load(self, parsed_directory: Path) -> ParsedContextFiles:
        """读取主 Markdown 和结构化 JSON 两个独立文件。"""

        full_markdown_path = self._find_file(parsed_directory, "full.md")
        structured_json_path = self._find_file(parsed_directory, "content_list_v2.json")
        if structured_json_path is None:
            structured_json_path = self._find_file(parsed_directory, "content_list.json")
        return ParsedContextFiles(
            full_markdown_path=full_markdown_path,
            structured_json_path=structured_json_path,
            full_markdown=(
                self.file_store.read_text(full_markdown_path, errors="replace")
                if full_markdown_path
                else ""
            ),
            structured_json=(
                self.file_store.read_text(structured_json_path, errors="replace")
                if structured_json_path
                else ""
            ),
        )

    def _find_file(self, directory: Path, filename: str) -> Path | None:
        """在解析目录中查找指定文件或带 UUID 前缀的文件。"""

        expected_name = filename.lower()
        for path in self.file_store.list_files(directory):
            current_name = path.name.lower()
            if path.is_file() and (
                current_name == expected_name
                or current_name.endswith(f"_{expected_name}")
            ):
                return path
        return None
