import pytest

from app.workflow.events import WorkflowEvent
from app.workflow.graph import AnalysisGraph, WorkflowHandlers
from app.workflow.state import AnalysisState


class EventCollector:
    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    def publish(self, event: WorkflowEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_workflow_skips_vlm_when_mineru_returns_no_images() -> None:
    calls: list[str] = []

    async def parse_fn(state: AnalysisState) -> dict[str, object]:
        return {"image_paths": [], "parsed_directory": "parsed"}

    async def vlm_fn(state: AnalysisState) -> str:
        calls.append("vlm")
        return "视觉内容"

    async def extract_fn(state: AnalysisState) -> list[dict[str, object]]:
        calls.append(f"extract:{state.get('vlm_image_content', '')}")
        return []

    async def normalize_fn(state: AnalysisState) -> list[dict[str, object]]:
        return state.get("candidates", [])

    async def validate_fn(state: AnalysisState) -> list[dict[str, object]]:
        return state.get("normalized_candidates", [])

    async def resolve_fn(state: AnalysisState) -> dict[str, dict[str, object]]:
        return {}

    async def confidence_fn(state: AnalysisState) -> float:
        return 0.0

    async def finalize_fn(state: AnalysisState) -> dict[str, object]:
        return {"status": "done"}

    publisher = EventCollector()
    workflow = AnalysisGraph(
        WorkflowHandlers(
            parse_with_mineru=parse_fn,
            read_images_with_vlm=vlm_fn,
            extract_candidates=extract_fn,
            normalize_candidates=normalize_fn,
            validate_candidates=validate_fn,
            resolve_conflicts=resolve_fn,
            calculate_confidence=confidence_fn,
            finalize_result=finalize_fn,
        ),
        publisher=publisher,
    )
    await workflow.ainvoke({"task_id": "task_without_images"})

    assert calls == ["extract:"]
    assert [event.node_name for event in publisher.events if event.event_type == "started"] == [
        "parse_with_mineru",
        "extract_candidates",
        "normalize_candidates",
        "validate_candidates",
        "resolve_conflicts",
        "calculate_confidence",
        "finalize_result",
    ]


@pytest.mark.asyncio
async def test_workflow_runs_vlm_when_mineru_returns_images() -> None:
    calls: list[str] = []

    async def parse_fn(state: AnalysisState) -> dict[str, object]:
        return {
            "image_paths": ["data/parsed_documents/task/page_001.png"],
            "parsed_directory": "parsed",
        }

    async def vlm_fn(state: AnalysisState) -> str:
        calls.append("vlm")
        return "视觉内容"

    async def extract_fn(state: AnalysisState) -> list[dict[str, object]]:
        calls.append(f"extract:{state.get('vlm_image_content', '')}")
        return []

    async def normalize_fn(state: AnalysisState) -> list[dict[str, object]]:
        return state.get("candidates", [])

    async def validate_fn(state: AnalysisState) -> list[dict[str, object]]:
        return state.get("normalized_candidates", [])

    async def resolve_fn(state: AnalysisState) -> dict[str, dict[str, object]]:
        return {}

    async def confidence_fn(state: AnalysisState) -> float:
        return 0.0

    async def finalize_fn(state: AnalysisState) -> dict[str, object]:
        return {"status": "done"}

    publisher = EventCollector()
    workflow = AnalysisGraph(
        WorkflowHandlers(
            parse_with_mineru=parse_fn,
            read_images_with_vlm=vlm_fn,
            extract_candidates=extract_fn,
            normalize_candidates=normalize_fn,
            validate_candidates=validate_fn,
            resolve_conflicts=resolve_fn,
            calculate_confidence=confidence_fn,
            finalize_result=finalize_fn,
        ),
        publisher=publisher,
    )
    await workflow.ainvoke({"task_id": "task_with_images"})

    assert calls == ["vlm", "extract:视觉内容"]
    assert [event.node_name for event in publisher.events if event.event_type == "started"] == [
        "parse_with_mineru",
        "read_images_with_vlm",
        "extract_candidates",
        "normalize_candidates",
        "validate_candidates",
        "resolve_conflicts",
        "calculate_confidence",
        "finalize_result",
    ]
