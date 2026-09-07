import logging
from pathlib import Path

from app.config import Settings
from app.logging_config import configure_logging


def test_configure_logging_writes_system_log(tmp_path: Path) -> None:
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    named_loggers = [
        logging.getLogger(name)
        for name in (
            "uvicorn",
            "uvicorn.error",
            "uvicorn.access",
            "httpx",
            "httpcore",
            "httpx2",
            "langchain_openai",
            "openai",
        )
    ]
    previous_named = [
        (logger, list(logger.handlers), logger.level, logger.propagate)
        for logger in named_loggers
    ]
    previous_handler_set = {
        handler
        for handlers in [previous_handlers, *(item[1] for item in previous_named)]
        for handler in handlers
    }
    log_path = tmp_path / "logs" / "app.log"
    try:
        settings = Settings(
            DOCMIND_SYSTEM_LOG_FILE=log_path,
            DOCMIND_SYSTEM_LOG_LEVEL="INFO",
            deepseek_api_key="test-key",
        )
        configure_logging(settings)
        logging.getLogger("app.test").info("system-log-ready")
        logging.getLogger("uvicorn.error").info("routine-uvicorn-message")
        for handler in logging.getLogger().handlers:
            handler.flush()
        content = log_path.read_text(encoding="utf-8")
        assert "system-log-ready" in content
        assert "routine-uvicorn-message" not in content
    finally:
        configured_handlers = {
            handler
            for logger in [root, *named_loggers]
            for handler in logger.handlers
            if handler not in previous_handler_set
        }
        for handler in configured_handlers:
            handler.close()
        root.handlers = previous_handlers
        root.setLevel(previous_level)
        for logger, handlers, level, propagate in previous_named:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
