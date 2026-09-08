import logging.config
import sys
from pathlib import Path
from typing import Any

from app.config import Settings


def _console_enabled() -> bool:
    """stdout 为真实终端时才启用控制台日志。

    uvicorn reload 的 worker 进程 stdout 可能为管道，父进程不及时消费时
    控制台写入会阻塞事件循环（曾导致任务卡死、/health 无响应），
    因此非终端环境只写文件日志。
    """

    try:
        stream = sys.stdout
        return stream is not None and bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


class ShutdownNoiseFilter(logging.Filter):
    """过滤服务正常关停时产生的 KeyboardInterrupt/CancelledError 堆栈噪音。"""

    _NOISE_EXC_NAMES = frozenset({"KeyboardInterrupt", "CancelledError", "SystemExit"})

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        if (
            record.exc_info is not None
            and record.exc_info[0] is not None
            and record.exc_info[0].__name__ in self._NOISE_EXC_NAMES
        ):
            return False
        message = record.getMessage()
        return (
            "KeyboardInterrupt" not in message and "CancelledError" not in message
        )


def configure_logging(settings: Settings) -> None:
    """配置控制台与按日轮转的系统日志。"""

    log_path = Path(settings.system_log_file).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = settings.system_log_level.upper()
    console = settings.system_log_console and _console_enabled()
    handler_names = ["app_file", *(["console"] if console else [])]
    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": (
                    "%(asctime)s %(levelname)s %(name)s "
                    "[pid=%(process)d] %(message)s"
                ),
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "filters": {
            "shutdown_noise": {"()": "app.logging_config.ShutdownNoiseFilter"},
        },
        "handlers": {
            "app_file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "formatter": "standard",
                "filters": ["shutdown_noise"],
                "level": level,
                "filename": str(log_path),
                "when": "midnight",
                "backupCount": settings.system_log_retention_days,
                "encoding": "utf-8",
                "delay": True,
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": handler_names,
                "level": "WARNING",
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": handler_names,
                "level": "WARNING",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": handler_names,
                "level": "WARNING",
                "propagate": False,
            },
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
            "httpx2": {"level": "WARNING"},
            "langchain_openai": {"level": "WARNING"},
            "openai": {"level": "WARNING"},
        },
        "root": {
            "handlers": handler_names,
            "level": level,
        },
    }
    if console:
        config["handlers"]["console"] = {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "filters": ["shutdown_noise"],
            "level": level,
            "stream": "ext://sys.stdout",
        }
    logging.config.dictConfig(config)
    logging.getLogger(__name__).info(
        "系统日志已初始化：level=%s console=%s file=%s",
        level,
        console,
        log_path,
    )
