"""工作流生命周期相关的异常。"""


class TaskCancelledError(Exception):
    """任务被主动取消，工作流不再继续。

    由节点入口的取消检查点抛出，`AnalysisService._run_task` 捕获后把任务
    标记为 cancelled。取消不是失败：任务状态里不写入 error 字段。
    """
