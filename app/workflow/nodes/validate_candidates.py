from typing import Any

from app.workflow.contracts import ValidateHandler
from app.workflow.events import WorkflowEventPublisher
from app.workflow.node_runner import run_node
from app.workflow.state import AnalysisState


def build_validate_candidates_node(
    handler: ValidateHandler,
    publisher: WorkflowEventPublisher | None = None,
):
    """创建候选值校验节点。"""

    async def validate_candidates(state: AnalysisState) -> dict[str, Any]:
        candidates = await run_node(
            node_name="validate_candidates",
            state=state,
            handler=lambda: handler(state),
            start_progress=70,
            success_progress=75,
            publisher=publisher,
        )
        return {"validated_candidates": candidates}

    return validate_candidates
