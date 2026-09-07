import logging.config
from pathlib import Path
from typing import Any

from app.config import Settings


def configure_logging(settings: Settings) -> None:
    """配置控制台与按日轮转的系统日志。"""

    log_path = Path(settings.system_log_file).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = settings.system_log_level.upper()
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
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "level": level,
                "stream": "ext://sys.stdout",
            },
            "app_file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "formatter": "standard",
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
                "handlers": ["console", "app_file"],
                "level": "WARNING",
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["console", "app_file"],
                "level": "WARNING",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["console", "app_file"],
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
            "handlers": ["console", "app_file"],
            "level": level,
        },
    }
    logging.config.dictConfig(config)
