"""工作流生命周期相关的异常。

两类中断的语义差别：
- 暂停：已完成的节点产物保留在磁盘上，恢复时从断点节点继续，不重跑；
- 取消：终止且不可恢复，重新分析必须重新上传。

两者在节点边界把控制权交回服务层，由 `AnalysisService` 统一落盘状态与提示。
"""


class TaskInterruptedError(Exception):
    """任务被中断（暂停或取消），工作流停止继续推进。"""


class TaskPausedError(TaskInterruptedError):
    """任务被暂停：产物保留，可由 `resume_task` 从断点恢复。"""


class TaskCancelledError(TaskInterruptedError):
    """任务被取消：不可恢复。

    取消不是失败：任务状态里不写入 error 字段。
    """
