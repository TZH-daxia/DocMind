from collections.abc import Awaitable, Callable
from typing import Any

from app.workflow.state import AnalysisState

RenderHandler = Callable[[AnalysisState], Awaitable[dict[str, Any]]]
VlmHandler = Callable[[AnalysisState], Awaitable[str]]
ExtractHandler = Callable[[AnalysisState], Awaitable[list[dict[str, Any]]]]
BuildResultHandler = Callable[[AnalysisState], Awaitable[dict[str, Any]]]
