from typing import Any, TypedDict


class AnalysisState(TypedDict, total=False):
    """单份文档分析过程中传递的状态。"""

    task_id: str
    uploaded_path: str
    source_name: str
    schema_version: str
    context: dict[str, Any]
    image_paths: list[str]
    vlm_image_content: str
    parsed: dict[str, Any]
    candidates: list[dict[str, Any]]
    overall_confidence: float
    result: dict[str, Any]
