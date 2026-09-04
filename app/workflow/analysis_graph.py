"""旧工作流模块的兼容导出。"""

from app.workflow.contracts import (
    ExtractHandler,
    FinalizeHandler,
    ParseHandler,
    VlmHandler,
)
from app.workflow.events import WorkflowEventPublisher
from app.workflow.graph import AnalysisGraph, WorkflowHandlers
from app.workflow.state import AnalysisState


class AnalysisWorkflow(AnalysisGraph):
    """兼容旧构造方式的工作流入口。"""

    def __init__(
        self,
        parse_fn: ParseHandler,
        vlm_fn: VlmHandler,
        extract_fn: ExtractHandler,
        finalize_fn: FinalizeHandler,
        publisher: WorkflowEventPublisher | None = None,
    ) -> None:
        async def normalize_candidates(state: AnalysisState) -> list[dict]:
            return state.get("candidates", [])

        async def validate_candidates(state: AnalysisState) -> list[dict]:
            return state.get("normalized_candidates", state.get("candidates", []))

        async def resolve_conflicts(state: AnalysisState) -> dict[str, dict]:
            return {}

        async def calculate_confidence(state: AnalysisState) -> float:
            return 0.0

        super().__init__(
            handlers=WorkflowHandlers(
                parse_with_mineru=parse_fn,
                read_images_with_vlm=vlm_fn,
                extract_candidates=extract_fn,
                normalize_candidates=normalize_candidates,
                validate_candidates=validate_candidates,
                resolve_conflicts=resolve_conflicts,
                calculate_confidence=calculate_confidence,
                finalize_result=finalize_fn,
            ),
            publisher=publisher,
        )


__all__ = ["AnalysisGraph", "AnalysisState", "AnalysisWorkflow", "WorkflowHandlers"]
