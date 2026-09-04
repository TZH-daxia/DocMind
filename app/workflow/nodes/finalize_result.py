from typing import Any

from app.workflow.contracts import FinalizeHandler
from app.workflow.events import WorkflowEventPublisher
from app.workflow.node_runner import run_node
from app.workflow.state import AnalysisState


def build_finalize_result_node(
    handler: FinalizeHandler,
    publisher: WorkflowEventPublisher | None = None,
):
    """创建最终结果生成节点。"""

    async def finalize_result(state: AnalysisState) -> dict[str, Any]:
        result = await run_node(
            node_name="finalize_result",
            state=state,
            handler=lambda: handler(state),
            start_progress=80,
            success_progress=100,
            publisher=publisher,
        )
        return {"result": result}

    return finalize_result
