import json
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import get_analysis_service
from app.schemas.analysis import AnalysisContext, SubmissionValidationRequest
from app.schemas.file import UploadedDocument
from app.service.analysis_service import AnalysisService

router = APIRouter(prefix="/analysis", tags=["分析任务"])

TASK_NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    404: {"description": "任务不存在（TASK_NOT_FOUND）"}
}


@router.post(
    "/tasks",
    status_code=202,
    summary="创建分析任务（上传托书）",
    response_description="任务快照：包含 task_id、初始状态与是否为幂等复用",
)
async def create_analysis_task(
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
    file: Annotated[
        UploadFile,
        File(
            description="托书文件，支持 PDF / DOC / DOCX / XLS / XLSX，单份不超过配置上限"
        ),
    ],
    request_id: Annotated[
        str,
        Form(description="幂等请求 ID：相同 ID 重复提交会复用已有任务，不会重复处理"),
    ],
    schema_version: Annotated[
        str,
        Form(description="输出字段结构版本，当前为 po_order.v1"),
    ] = "po_order.v1",
    context: Annotated[
        str,
        Form(
            description=(
                "订单页面上下文（JSON 字符串）。用于补充托书本身没有的信息，"
                "如委托客户 fid；空对象传 `{}` 即可"
            )
        ),
    ] = "{}",
    auto_start: Annotated[
        bool,
        Form(
            description="是否上传后立即开始分析；传 false 时任务保持 queued，需另行触发"
        ),
    ] = True,
) -> dict[str, Any]:
    """上传一份托书并创建分析任务。

    处理说明：

    - `request_id` 用于幂等，相同 ID 重复提交会复用已有任务；
    - `context` 提供托书中不存在的信息（如委托客户 `fid`），以 JSON 字符串传入；
    - 响应中 `idempotent_reuse` 为真表示命中幂等复用，未新建任务；
    - `context` 非法或文件不合法时返回 422。
    """

    try:
        parsed_context = AnalysisContext.model_validate(json.loads(context))
        uploaded_document = UploadedDocument(
            filename=file.filename or "",
            content_type=file.content_type,
            content=await file.read(),
        )
        task = await service.create_task(
            uploaded_document,
            request_id,
            schema_version,
            parsed_context,
            auto_start=auto_start,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if auto_start and not task.get("idempotent_reuse"):
        # 用 start_task 而非 BackgroundTasks：任务作为独立 asyncio 任务登记句柄，
        # 之后可由 POST /tasks/{task_id}/cancel 中断
        service.start_task(task["task_id"])
    return task


@router.get(
    "/files",
    summary="列出已上传文件",
    response_description="文件与任务状态列表（items）",
)
async def list_analysis_files(
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """返回已上传文件及其对应任务的状态，用于前端左侧文件列表。"""

    return {"items": service.list_tasks()}


@router.get(
    "/tasks/{task_id}/events",
    summary="订阅任务事件（SSE）",
    response_description="text/event-stream 事件流，逐条推送任务状态与节点进度",
    responses=TASK_NOT_FOUND_RESPONSE,
)
async def stream_analysis_events(
    task_id: Annotated[str, Path(description="任务 ID，由创建任务接口返回")],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> StreamingResponse:
    """通过 SSE 持续推送任务状态与节点事件，供前端实时展示运行进度。

    连接保持到任务结束；客户端断开即可停止推送。
    """

    try:
        service.get_task_status(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND") from exc
    return StreamingResponse(
        service.iter_task_events(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/tasks/{task_id}/events/history",
    summary="查询任务历史事件",
    response_description="任务已落盘的生命周期、业务与节点事件（items）",
    responses=TASK_NOT_FOUND_RESPONSE,
)
async def get_analysis_event_history(
    task_id: Annotated[str, Path(description="任务 ID，由创建任务接口返回")],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """返回任务已落盘的全部事件，用于页面刷新后回放运行过程。"""

    try:
        return {"items": service.read_task_events(task_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND") from exc


@router.get(
    "/tasks/{task_id}",
    summary="查询任务状态",
    response_description="任务状态快照",
    responses=TASK_NOT_FOUND_RESPONSE,
)
async def get_analysis_task(
    task_id: Annotated[str, Path(description="任务 ID，由创建任务接口返回")],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """返回一个任务的状态快照（状态、进度、时间等）。"""

    try:
        return service.get_task_status(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND") from exc


@router.post(
    "/tasks/{task_id}/cancel",
    summary="取消任务",
    response_description="取消后的任务状态快照",
    responses=TASK_NOT_FOUND_RESPONSE,
)
async def cancel_analysis_task(
    task_id: Annotated[str, Path(description="任务 ID，由创建任务接口返回")],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """取消一个在途任务：立即停止且不可恢复（重跑需要重新上传）。

    幂等：任务已到终态（完成/失败/已取消）时原样返回，不覆盖既有结果。
    """

    try:
        return service.cancel_task(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND") from exc


@router.post(
    "/tasks/{task_id}/pause",
    summary="暂停任务",
    response_description="暂停后的任务状态快照",
    responses=TASK_NOT_FOUND_RESPONSE,
)
async def pause_analysis_task(
    task_id: Annotated[str, Path(description="任务 ID，由创建任务接口返回")],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """暂停一个在途任务：已产出的节点保留，可用 `/resume` 从断点继续。

    幂等：任务已处于冻结态（终态/已暂停）时原样返回。
    """

    try:
        return service.pause_task(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND") from exc


@router.post(
    "/tasks/{task_id}/resume",
    summary="继续任务",
    response_description="恢复后的任务状态快照",
    responses=TASK_NOT_FOUND_RESPONSE,
)
async def resume_analysis_task(
    task_id: Annotated[str, Path(description="任务 ID，由创建任务接口返回")],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """从暂停处继续：只执行没有落盘产物的节点，不重跑已完成的步骤。

    仅 `paused` 状态可恢复；其他状态原样返回当前快照。
    """

    try:
        return service.resume_task(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND") from exc


@router.get(
    "/tasks/{task_id}/result",
    summary="获取分析结果",
    response_description="结构化分析结果与字段级元数据",
    responses={404: {"description": "结果不存在或任务尚未完成（RESULT_NOT_FOUND）"}},
)
async def get_analysis_result(
    task_id: Annotated[str, Path(description="任务 ID，由创建任务接口返回")],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """返回一个已完成的分析结果，含字段值、置信度、原文证据与坐标位置。"""

    try:
        return service.get_result(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="RESULT_NOT_FOUND") from exc


@router.post(
    "/tasks/{task_id}/submission/validate",
    summary="提交前校验",
    response_description="始发港/目的港归一化结论与委托客户可用性判定",
    responses=TASK_NOT_FOUND_RESPONSE,
)
async def validate_analysis_submission(
    task_id: Annotated[str, Path(description="任务 ID，由创建任务接口返回")],
    request: SubmissionValidationRequest,
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """提交前校验：始发港/目的港转三字码 + 委托客户存在性（不发起真实提交）。

    入参不限形式（中文 / 英文 / 三字码 / 客户 ID），由后端统一转换为提交接口
    需要的形态后再返回结论。
    """

    return await service.validate_submission(task_id, request)


@router.get(
    "/tasks/{task_id}/pages/{page_no}",
    summary="获取页面渲染图片",
    response_description="该页的 PNG 渲染图片",
    responses={404: {"description": "任务或页面不存在（PAGE_NOT_FOUND）"}},
)
async def get_analysis_page_image(
    task_id: Annotated[str, Path(description="任务 ID，由创建任务接口返回")],
    page_no: Annotated[int, Path(description="页码，从 1 开始", ge=1)],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> FileResponse:
    """返回任务指定页的渲染图片，供前端预览区展示原件并叠加字段高亮框。"""

    try:
        image_path = service.get_page_image_path(task_id, page_no)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="PAGE_NOT_FOUND") from exc
    return FileResponse(image_path, media_type="image/png")
