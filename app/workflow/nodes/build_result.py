from typing import Any

from app.workflow.contracts import BuildResultHandler
from app.workflow.events import WorkflowEventPublisher
from app.workflow.node_runner import run_node
from app.workflow.state import AnalysisState


def build_build_result_node(
    handler: BuildResultHandler,
    publisher: WorkflowEventPublisher | None = None,
):
    """创建候选 → 置信度判断 + 输出节点。"""

    async def build_result(state: AnalysisState) -> dict[str, Any]:
        result = await run_node(
            node_name="build_result",
            state=state,
            handler=lambda: handler(state),
            start_progress=80,
            success_progress=100,
            publisher=publisher,
        )
        return {"result": result}

    return build_result
