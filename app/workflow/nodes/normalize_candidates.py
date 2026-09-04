from typing import Any

from app.workflow.contracts import NormalizeHandler
from app.workflow.events import WorkflowEventPublisher
from app.workflow.node_runner import run_node
from app.workflow.state import AnalysisState


def build_normalize_candidates_node(
    handler: NormalizeHandler,
    publisher: WorkflowEventPublisher | None = None,
):
    """创建候选值标准化节点。"""

    async def normalize_candidates(state: AnalysisState) -> dict[str, Any]:
        candidates = await run_node(
            node_name="normalize_candidates",
            state=state,
            handler=lambda: handler(state),
            start_progress=65,
            success_progress=70,
            publisher=publisher,
        )
        return {"normalized_candidates": candidates}

    return normalize_candidates
