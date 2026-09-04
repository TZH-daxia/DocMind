import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from app.workflow.events import (
    NullWorkflowEventPublisher,
    WorkflowEvent,
    WorkflowEventPublisher,
    now_iso,
)
from app.workflow.state import AnalysisState

logger = logging.getLogger(__name__)


async def run_node[ResultT](
    node_name: str,
    state: AnalysisState,
    handler: Callable[[], Awaitable[ResultT]],
    start_progress: int,
    success_progress: int,
    publisher: WorkflowEventPublisher | None = None,
) -> ResultT:
    """统一执行节点并记录开始、成功、失败和耗时事件。"""

    event_publisher = publisher or NullWorkflowEventPublisher()
    task_id = state["task_id"]
    started_at = now_iso()
    started_clock = perf_counter()
    event_publisher.publish(
        WorkflowEvent(
            task_id=task_id,
            node_name=node_name,
            event_type="started",
            progress=start_progress,
            message=f"{node_name} 开始执行",
            started_at=started_at,
        )
    )
    logger.info("工作流节点开始执行：task_id=%s node=%s", task_id, node_name)
    try:
        result = await handler()
    except Exception:
        duration_ms = int((perf_counter() - started_clock) * 1000)
        logger.exception("工作流节点执行失败：task_id=%s node=%s", task_id, node_name)
        event_publisher.publish(
            WorkflowEvent(
                task_id=task_id,
                node_name=node_name,
                event_type="failed",
                progress=100,
                message=f"{node_name} 执行失败",
                started_at=started_at,
                finished_at=now_iso(),
                duration_ms=duration_ms,
            )
        )
        raise
    duration_ms = int((perf_counter() - started_clock) * 1000)
    event_publisher.publish(
        WorkflowEvent(
            task_id=task_id,
            node_name=node_name,
            event_type="succeeded",
            progress=success_progress,
            message=f"{node_name} 执行成功",
            started_at=started_at,
            finished_at=now_iso(),
            duration_ms=duration_ms,
        )
    )
    logger.info(
        "工作流节点执行成功：task_id=%s node=%s duration_ms=%s",
        task_id,
        node_name,
        duration_ms,
    )
    return result
