import os

import uvicorn

from app.config import get_settings


def _disable_console_quick_edit() -> None:
    """禁用 Windows 控制台快速编辑模式。

    控制台被鼠标选中文本时输出会暂停，服务进程随后任何一次日志写入都会
    永久阻塞并冻结事件循环（表现为任务卡死、/health 无响应），因此启动时
    关闭该模式；stdout 不是控制台（重定向/管道）时不做任何处理。
    """

    if os.name != "nt":
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return  # stdout 非控制台，无需处理
    enable_quick_edit = 0x0040
    enable_extended_flags = 0x0080
    new_mode = (int(mode.value) & ~enable_quick_edit) | enable_extended_flags
    kernel32.SetConsoleMode(handle, new_mode)


def main() -> None:
    """从项目根目录启动 DocMind 服务。"""

    _disable_console_quick_edit()
    settings = get_settings()
    host = settings.app_host
    port = settings.app_port
    # 默认关闭热重载：reload 的文件轮询和 worker 重启会中断在途分析任务，
    # 且 StatReload 在 Windows 上易阻塞 worker；改动代码后手动重启即可
    reload = bool(os.getenv("DOCMIND_RELOAD", "0") == "1")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=["app"] if reload else None,
        log_config=None,
    )


if __name__ == "__main__":
    main()
