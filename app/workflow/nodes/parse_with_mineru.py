from typing import Any

from app.workflow.contracts import ParseHandler
from app.workflow.events import WorkflowEventPublisher
from app.workflow.node_runner import run_node
from app.workflow.state import AnalysisState


def build_parse_with_mineru_node(
    handler: ParseHandler,
    publisher: WorkflowEventPublisher | None = None,
):
    """创建 MinerU 解析节点。"""

    async def parse_with_mineru(state: AnalysisState) -> dict[str, Any]:
        parsed = await run_node(
            node_name="parse_with_mineru",
            state=state,
            handler=lambda: handler(state),
            start_progress=20,
            success_progress=45,
            publisher=publisher,
        )
        return {
            "parsed": parsed,
            "image_paths": parsed.get("image_paths", []),
        }

    return parse_with_mineru
