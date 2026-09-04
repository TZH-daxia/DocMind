from typing import Any

from app.workflow.contracts import ExtractHandler
from app.workflow.events import WorkflowEventPublisher
from app.workflow.node_runner import run_node
from app.workflow.state import AnalysisState


def build_extract_candidates_node(
    handler: ExtractHandler,
    publisher: WorkflowEventPublisher | None = None,
):
    """创建字段候选抽取节点。"""

    async def extract_candidates(state: AnalysisState) -> dict[str, Any]:
        candidates = await run_node(
            node_name="extract_candidates",
            state=state,
            handler=lambda: handler(state),
            start_progress=55,
            success_progress=75,
            publisher=publisher,
        )
        return {"candidates": candidates}

    return extract_candidates
