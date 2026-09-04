from typing import Any

from app.workflow.contracts import RenderHandler
from app.workflow.events import WorkflowEventPublisher
from app.workflow.node_runner import run_node
from app.workflow.state import AnalysisState


def build_render_document_node(
    handler: RenderHandler,
    publisher: WorkflowEventPublisher | None = None,
):
    """创建本地文档渲染节点：把 .pdf/.doc/.xls 转成页面图片与文本层。"""

    async def render_document(state: AnalysisState) -> dict[str, Any]:
        rendered = await run_node(
            node_name="render_document",
            state=state,
            handler=lambda: handler(state),
            start_progress=20,
            success_progress=45,
            publisher=publisher,
        )
        return {
            "parsed": rendered,
            "image_paths": rendered.get("image_paths", []),
        }

    return render_document
