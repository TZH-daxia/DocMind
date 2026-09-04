from app.workflow.contracts import ConfidenceHandler
from app.workflow.events import WorkflowEventPublisher
from app.workflow.node_runner import run_node
from app.workflow.state import AnalysisState


def build_calculate_confidence_node(
    handler: ConfidenceHandler,
    publisher: WorkflowEventPublisher | None = None,
):
    """创建任务整体置信度计算节点。"""

    async def calculate_confidence(state: AnalysisState) -> dict[str, float]:
        confidence = await run_node(
            node_name="calculate_confidence",
            state=state,
            handler=lambda: handler(state),
            start_progress=80,
            success_progress=85,
            publisher=publisher,
        )
        return {"overall_confidence": confidence}

    return calculate_confidence
