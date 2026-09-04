from collections.abc import Awaitable, Callable
from typing import Any

from app.workflow.state import AnalysisState

ParseHandler = Callable[[AnalysisState], Awaitable[dict[str, Any]]]
VlmHandler = Callable[[AnalysisState], Awaitable[str]]
ExtractHandler = Callable[[AnalysisState], Awaitable[list[dict[str, Any]]]]
NormalizeHandler = Callable[[AnalysisState], Awaitable[list[dict[str, Any]]]]
ValidateHandler = Callable[[AnalysisState], Awaitable[list[dict[str, Any]]]]
ResolveHandler = Callable[[AnalysisState], Awaitable[dict[str, dict[str, Any]]]]
ConfidenceHandler = Callable[[AnalysisState], Awaitable[float]]
FinalizeHandler = Callable[[AnalysisState], Awaitable[dict[str, Any]]]
