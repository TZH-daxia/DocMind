import logging

from app.workflow.state import AnalysisState

logger = logging.getLogger(__name__)


def route_image_step(state: AnalysisState) -> str:
    """根据 MinerU 解析结果决定是否进入图片 VLM 节点。"""

    task_id = state["task_id"]
    decision = "with_images" if state.get("image_paths") else "without_images"
    logger.info(
        "路由节点执行：task_id=%s image_count=%s -> %s",
        task_id,
        len(state.get("image_paths") or []),
        decision,
    )
    return decision
