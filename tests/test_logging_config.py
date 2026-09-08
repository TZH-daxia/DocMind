import asyncio
import logging
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.logging_config import configure_logging

_NAMED_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "httpx",
    "httpcore",
    "httpx2",
    "langchain_openai",
    "openai",
)


@pytest.fixture
def logging_context(tmp_path: Path) -> Generator[tuple[Path, logging.Logger], Any, None]:
    """配置临时系统日志并在用例结束后恢复原日志状态。"""

    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    named_loggers = [logging.getLogger(name) for name in _NAMED_LOGGERS]
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
    settings = Settings(
        DOCMIND_SYSTEM_LOG_FILE=log_path,
        DOCMIND_SYSTEM_LOG_LEVEL="INFO",
        deepseek_api_key="test-key",
    )
    configure_logging(settings)
    try:
        yield log_path, logging.getLogger("app.test")
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


def _read_log(log_path: Path) -> str:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return log_path.read_text(encoding="utf-8")


def test_console_handler_disabled_by_default(logging_context: tuple[Path, logging.Logger]) -> None:
    """默认不挂控制台 handler：宿主终端不消费输出时会阻塞事件循环。"""

    stdout_handlers = [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.StreamHandler)
        and getattr(handler, "stream", None) is sys.stdout
    ]
    assert not stdout_handlers


def test_configure_logging_writes_system_log(logging_context: tuple[Path, logging.Logger]) -> None:
    log_path, logger = logging_context

    logger.info("system-log-ready")
    logging.getLogger("uvicorn.error").info("routine-uvicorn-message")

    content = _read_log(log_path)
    assert "system-log-ready" in content
    assert "routine-uvicorn-message" not in content


def test_shutdown_noise_filter_drops_cancelled_error(
    logging_context: tuple[Path, logging.Logger],
) -> None:
    log_path, logger = logging_context

    try:
        raise asyncio.CancelledError
    except asyncio.CancelledError:
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
    logger.error("real-business-error")

    content = _read_log(log_path)
    assert "real-business-error" in content
    assert "Exception in ASGI application" not in content


def test_shutdown_noise_filter_keeps_real_exceptions(
    logging_context: tuple[Path, logging.Logger],
) -> None:
    log_path, logger = logging_context

    try:
        raise RuntimeError("业务处理失败")
    except RuntimeError:
        logger.exception("任务执行异常")

    content = _read_log(log_path)
    assert "任务执行异常" in content
    assert "业务处理失败" in content
