from typing import Any

from app.workflow.contracts import ResolveHandler
from app.workflow.events import WorkflowEventPublisher
from app.workflow.node_runner import run_node
from app.workflow.state import AnalysisState


def build_resolve_conflicts_node(
    handler: ResolveHandler,
    publisher: WorkflowEventPublisher | None = None,
):
    """创建候选值冲突处理节点。"""

    async def resolve_conflicts(state: AnalysisState) -> dict[str, Any]:
        resolved = await run_node(
            node_name="resolve_conflicts",
            state=state,
            handler=lambda: handler(state),
            start_progress=75,
            success_progress=80,
            publisher=publisher,
        )
        return {"resolved_fields": resolved}

    return resolve_conflicts
