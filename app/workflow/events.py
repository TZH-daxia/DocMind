from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal, Protocol

NodeEventType = Literal["started", "succeeded", "failed", "skipped"]


@dataclass(frozen=True)
class WorkflowEvent:
    """一个工作流节点的执行事件。"""

    task_id: str
    node_name: str
    event_type: NodeEventType
    progress: int
    message: str
    started_at: str
    finished_at: str | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, object]:
        """将事件转换为可写入 JSON 的字典。"""

        return {"kind": "workflow_node", **asdict(self)}


class WorkflowEventPublisher(Protocol):
    """工作流事件发布协议。"""

    def publish(self, event: WorkflowEvent) -> None:
        """发布一个工作流节点事件。"""


class NullWorkflowEventPublisher:
    """不产生外部副作用的空事件发布器。"""

    def publish(self, event: WorkflowEvent) -> None:
        """忽略一个工作流节点事件。"""


def now_iso() -> str:
    """返回带时区的当前时间。"""

    return datetime.now().astimezone().isoformat()
