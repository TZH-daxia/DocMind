from typing import Any, TypedDict


class AnalysisState(TypedDict, total=False):
    """单份文档分析过程中传递的状态。"""

    task_id: str
    uploaded_path: str
    source_name: str
    schema_version: str
    context: dict[str, Any]
    # 恢复执行用：已完成节点的名字（由服务层按磁盘产物推断），图的入口据此
    # 从断点节点开始，避免重跑已产出结果的节点
    completed_nodes: list[str]
    image_paths: list[str]
    vlm_image_content: str
    parsed: dict[str, Any]
    candidates: list[dict[str, Any]]
    overall_confidence: float
    result: dict[str, Any]
