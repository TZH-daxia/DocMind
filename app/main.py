import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.analysis import router as analysis_router
from app.config import get_settings
from app.logging_config import configure_logging
from app.storage.file_store import FileStore

settings = get_settings()
configure_logging(settings)
FileStore(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """记录应用生命周期，同时保持 Uvicorn 常规生命周期日志静默。"""

    logger.info("%s application started", settings.app_name)
    try:
        yield
    finally:
        logger.info("%s application stopped", settings.app_name)


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
