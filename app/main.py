import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.dependencies import get_analysis_service
from app.api.routes.analysis import router as analysis_router
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.storage.file_store import FileStore

settings = get_settings()
configure_logging(settings)
FileStore(settings)
logger = logging.getLogger(__name__)


def format_startup_banner(current: Settings) -> str:
    """拼装启动提示：服务地址、接口文档、健康检查与日志位置。"""

    base = f"http://{current.app_host}:{current.app_port}"
    return "\n".join(
        [
            f"{current.app_name} 已启动",
            f"  前端页面：{base}/",
            f"  接口文档：{base}/docs",
            f"  健康检查：{base}/health",
            f"  系统日志：{current.system_log_file.resolve()}",
            "  （实时查看日志：Get-Content <系统日志路径> -Wait -Tail 20）",
        ]
    )


def _echo(message: str) -> None:
    """向终端输出提示；宿主终端不可用时静默失败，不影响服务。"""

    try:
        sys.stdout.write(message + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        logger.debug("终端提示输出失败：%s", message)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """记录应用生命周期，同时保持 Uvicorn 常规生命周期日志静默。"""

    logger.info("%s application started", settings.app_name)
    _echo(format_startup_banner(settings))
    get_analysis_service().recover_interrupted_tasks()
    try:
        yield
    finally:
        logger.info("%s application stopped", settings.app_name)
        _echo(f"{settings.app_name} 已停止")


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(analysis_router, prefix=settings.api_prefix)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", include_in_schema=False)
async def frontend_index() -> FileResponse:
    """返回文档分析前端页面。"""

    return FileResponse("app/static/index.html")


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """返回服务健康状态。"""

    return {"status": "ok"}
