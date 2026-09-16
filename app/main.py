import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.dependencies import get_analysis_service
from app.api.routes.analysis import router as analysis_router
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.service.analysis_service import AnalysisService
from app.storage.file_store import FileStore

settings = get_settings()
configure_logging(settings)
FileStore(settings)
logger = logging.getLogger(__name__)

# 周期清理间隔（小时）：启动时先清一次，之后按此间隔重复
DATA_CLEANUP_INTERVAL_HOURS = 1.0

# 接口文档（/docs）展示用的中文元数据：仅影响 OpenAPI 描述，不影响接口行为。
API_VERSION = "0.1.0"
API_SUMMARY = "托书结构化分析服务"
API_DESCRIPTION = """
## 概述

**DocMind** 面向货代订单录入场景：上传托书（PDF / Word / Excel）后自动完成版面渲染、
视觉识别、字段抽取以及港口与委托客户主数据归一化，最终产出可直接提交到 poOrder
「订单新增」模块的结构化 JSON。

## 典型流程

1. `POST /analysis/tasks` 上传托书，并按需自动开始分析；
2. `GET /analysis/tasks/{task_id}/events` 以 SSE 实时订阅分析进度；
3. `GET /analysis/tasks/{task_id}/result` 获取结构化结果与字段级元数据；
4. 人工核对后提交（委托客户与始发港/目的港可直接从主数据下拉选取，
   最终提交动作由调用方后续接入）。

## 运行控制

任务运行过程中可暂停（`pause`）、从断点继续（`resume`）、取消（`cancel`，取消后
不可恢复，需重新上传）。以上操作均幂等。

## 通用约定

- 所有业务接口统一前缀为 `/docmind`（由配置 `api_prefix` 决定，前端调用需保持一致）；
- 任务状态取值：`queued`、`running`、`paused`、`succeeded`、`failed`、`cancelled`；
- 任务产物默认保留 24 小时，到期自动清理；
- 时间字段统一使用 `YYYY-MM-DD` 或 ISO 8601 字符串。
"""

TAGS_METADATA: list[dict[str, str]] = [
    {
        "name": "分析任务",
        "description": "托书的上传、运行控制、进度订阅与结果查询。",
    },
    {
        "name": "系统",
        "description": "服务健康状态等运维相关接口。",
    },
]

# Swagger UI 行为配置：默认展开分组、支持搜索过滤与「试一试」、展示请求耗时。
SWAGGER_UI_PARAMETERS: dict[str, object] = {
    "docExpansion": "list",
    "defaultModelsExpandDepth": 1,
    "defaultModelExpandDepth": 2,
    "displayRequestDuration": True,
    "filter": True,
    "persistAuthorization": True,
    "tryItOutEnabled": True,
    "deepLinking": True,
    "syntaxHighlight": {"theme": "monokai"},
}


async def _periodic_data_cleanup(service: AnalysisService, interval_hours: float) -> None:
    """周期清理超过保留期的任务产物；单次失败只记日志，不影响服务。"""

    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            service.run_data_retention_cleanup()
        except Exception:
            logger.exception("周期清理任务产物失败")


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
    service = get_analysis_service()
    service.recover_interrupted_tasks()
    try:
        service.run_data_retention_cleanup()
    except Exception:
        logger.exception("启动清理任务产物失败")
    cleanup_task = asyncio.create_task(
        _periodic_data_cleanup(service, DATA_CLEANUP_INTERVAL_HOURS)
    )
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        logger.info("%s application stopped", settings.app_name)
        _echo(f"{settings.app_name} 已停止")


# 关闭框架自带的 /docs、/redoc，改用下方自定义路由注入中文描述与美化样式。
app = FastAPI(
    title=f"{settings.app_name} · 托书结构化分析接口",
    summary=API_SUMMARY,
    description=API_DESCRIPTION,
    version=API_VERSION,
    openapi_tags=TAGS_METADATA,
    docs_url=None,
    redoc_url=None,
    swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
    lifespan=lifespan,
)
# 调用方（唯凯官网客服面板）部署在另一个域下，浏览器直连本服务：
# 不放开跨域会被浏览器拦在 CORS 预检，接口即使正常也拿不到响应。
# 不开 allow_credentials：接口不依赖 Cookie，保持最小放行面。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(analysis_router, prefix=settings.api_prefix)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def revalidate_static_assets(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """静态资源每次回源校验，避免前端改动"看起来没生效"。

    HTML 里只给入口脚本带了版本号，ES 模块之间互相 import 的路径
    （./fields.js、./components/*.js 等）不带版本号；若允许浏览器直接复用
    启发式缓存，那些子模块会长期停留在首次加载的旧版本。这里统一要求
    回源校验：内容未变仍是 304，不额外消耗带宽。
    """

    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/docs", include_in_schema=False)
async def swagger_ui_docs() -> HTMLResponse:
    """返回自定义样式的 Swagger UI 接口文档。

    用 get_swagger_ui_html 自行渲染，是为了替换默认样式表（/static/docs.css
    在官方样式之上叠加主题），并统一中文标题与站点图标。
    """

    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{settings.app_name} · 接口文档",
        swagger_css_url="/static/docs.css",
        swagger_favicon_url="/static/docs-favicon.svg",
        swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
    )


@app.get("/redoc", include_in_schema=False)
async def redoc_docs() -> HTMLResponse:
    """返回适合通读的中文接口文档（ReDoc）。"""

    return get_redoc_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{settings.app_name} · 接口文档",
        redoc_favicon_url="/static/docs-favicon.svg",
    )


@app.get("/", include_in_schema=False)
async def frontend_index() -> FileResponse:
    """返回文档分析前端页面。

    HTML 必须每次回源校验（no-cache）：页面里带着带版本号的静态资源引用，
    一旦 HTML 被浏览器启发式缓存，新版本号就不会生效，用户会一直看到旧前端。
    """

    return FileResponse(
        "app/static/index.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/health", tags=["系统"], summary="健康检查")
async def health() -> dict[str, str]:
    """返回服务健康状态；用于探活与负载均衡检查，恒返回 `{"status": "ok"}`。"""

    return {"status": "ok"}
