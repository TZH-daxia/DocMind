"""测试基础设施：隔离日志，避免测试输出写进正式 logs/app.log。"""

import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolate_logging() -> Iterator[None]:
    """每个测试结束后卸载 root handler。

    test_logging_config 会把 root logger 真的配置到 logs/app.log，不清理的话
    后续测试的日志会一并写进正式日志文件，干扰线上问题排查。
    """

    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
