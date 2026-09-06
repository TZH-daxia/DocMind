from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph

from app.workflow.contracts import (
    BuildResultHandler,
    ExtractHandler,
    RenderHandler,
    VlmHandler,
)
from app.workflow.events import WorkflowEventPublisher
from app.workflow.nodes.build_result import build_build_result_node
from app.workflow.nodes.extract_candidates import build_extract_candidates_node
from app.workflow.nodes.read_images_with_vlm import build_read_images_with_vlm_node
from app.workflow.nodes.render_document import build_render_document_node
from app.workflow.state import AnalysisState


@dataclass(frozen=True)
class WorkflowHandlers:
    """工作流节点所需的业务回调。"""

    render_document: RenderHandler
    read_images_with_vlm: VlmHandler
    extract_candidates: ExtractHandler
    build_result: BuildResultHandler


class AnalysisGraph:
    """本地渲染后端分析图：render → vlm → extract → build_result。

    统一后的核心流程只保留当前项目实际使用的 local 渲染链路，不再支持 MinerU。
    """

    def __init__(
        self,
        handlers: WorkflowHandlers,
        publisher: WorkflowEventPublisher | None = None,
    ) -> None:
        graph = StateGraph(AnalysisState)
        graph.add_node(
            "render_document",
            build_render_document_node(handlers.render_document, publisher),
        )
        graph.add_node(
            "read_images_with_vlm",
            build_read_images_with_vlm_node(handlers.read_images_with_vlm, publisher),
        )
        graph.add_node(
            "extract_candidates",
            build_extract_candidates_node(handlers.extract_candidates, publisher),
        )
        graph.add_node(
            "build_result",
            build_build_result_node(handlers.build_result, publisher),
        )
        graph.set_entry_point("render_document")
        graph.add_edge("render_document", "read_images_with_vlm")
        graph.add_edge("read_images_with_vlm", "extract_candidates")
        graph.add_edge("extract_candidates", "build_result")
        graph.add_edge("build_result", END)
        self.graph = graph.compile()

    async def ainvoke(self, state: AnalysisState) -> dict[str, Any]:
        """运行已编译的分析图。"""

        return await self.graph.ainvoke(state)
