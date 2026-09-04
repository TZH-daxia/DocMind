from app.workflow.contracts import VlmHandler
from app.workflow.events import WorkflowEventPublisher
from app.workflow.node_runner import run_node
from app.workflow.state import AnalysisState


def build_read_images_with_vlm_node(
    handler: VlmHandler,
    publisher: WorkflowEventPublisher | None = None,
):
    """创建图片 VLM 读取节点。"""

    async def read_images_with_vlm(state: AnalysisState) -> dict[str, str]:
        content = await run_node(
            node_name="read_images_with_vlm",
            state=state,
            handler=lambda: handler(state),
            start_progress=50,
            success_progress=50,
            publisher=publisher,
        )
        return {"vlm_image_content": content}

    return read_images_with_vlm
