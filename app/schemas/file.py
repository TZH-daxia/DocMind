from dataclasses import dataclass


@dataclass(frozen=True)
class UploadedDocument:
    """上传文档的基础数据。"""

    filename: str
    content_type: str | None
    content: bytes
