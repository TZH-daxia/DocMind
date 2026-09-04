from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph

from app.workflow.contracts import (
    ConfidenceHandler,
    ExtractHandler,
    FinalizeHandler,
    NormalizeHandler,
    ParseHandler,
    ResolveHandler,
    ValidateHandler,
    VlmHandler,
)
from app.workflow.events import WorkflowEventPublisher
from app.workflow.nodes.calculate_confidence import build_calculate_confidence_node
from app.workflow.nodes.extract_candidates import build_extract_candidates_node
from app.workflow.nodes.finalize_result import build_finalize_result_node
from app.workflow.nodes.normalize_candidates import build_normalize_candidates_node
from app.workflow.nodes.parse_with_mineru import build_parse_with_mineru_node
from app.workflow.nodes.read_images_with_vlm import build_read_images_with_vlm_node
from app.workflow.nodes.resolve_conflicts import build_resolve_conflicts_node
from app.workflow.nodes.route_image_step import route_image_step
from app.workflow.nodes.validate_candidates import build_validate_candidates_node
from app.workflow.state import AnalysisState


@dataclass(frozen=True)
class WorkflowHandlers:
    """工作流节点所需的业务回调。"""

    parse_with_mineru: ParseHandler
    read_images_with_vlm: VlmHandler
    extract_candidates: ExtractHandler
    normalize_candidates: NormalizeHandler
    validate_candidates: ValidateHandler
    resolve_conflicts: ResolveHandler
    calculate_confidence: ConfidenceHandler
    finalize_result: FinalizeHandler


class AnalysisGraph:
    """只负责创建和运行分析图。"""

    def __init__(
        self,
        handlers: WorkflowHandlers,
        publisher: WorkflowEventPublisher | None = None,
    ) -> None:
        graph = StateGraph(AnalysisState)
        graph.add_node(
            "parse_with_mineru",
            build_parse_with_mineru_node(handlers.parse_with_mineru, publisher),
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
            "normalize_candidates",
            build_normalize_candidates_node(handlers.normalize_candidates, publisher),
        )
        graph.add_node(
            "validate_candidates",
            build_validate_candidates_node(handlers.validate_candidates, publisher),
        )
        graph.add_node(
            "resolve_conflicts",
            build_resolve_conflicts_node(handlers.resolve_conflicts, publisher),
        )
        graph.add_node(
            "calculate_confidence",
            build_calculate_confidence_node(handlers.calculate_confidence, publisher),
        )
        graph.add_node(
            "finalize_result",
            build_finalize_result_node(handlers.finalize_result, publisher),
        )
        graph.set_entry_point("parse_with_mineru")
        graph.add_conditional_edges(
            "parse_with_mineru",
            route_image_step,
            {
                "with_images": "read_images_with_vlm",
                "without_images": "extract_candidates",
            },
        )
        graph.add_edge("read_images_with_vlm", "extract_candidates")
        graph.add_edge("extract_candidates", "normalize_candidates")
        graph.add_edge("normalize_candidates", "validate_candidates")
        graph.add_edge("validate_candidates", "resolve_conflicts")
        graph.add_edge("resolve_conflicts", "calculate_confidence")
        graph.add_edge("calculate_confidence", "finalize_result")
        graph.add_edge("finalize_result", END)
        self.graph = graph.compile()

    async def ainvoke(self, state: AnalysisState) -> dict[str, Any]:
        """运行已编译的分析图。"""

        return await self.graph.ainvoke(state)
