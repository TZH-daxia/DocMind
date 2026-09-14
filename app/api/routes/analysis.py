import json
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import get_analysis_service
from app.schemas.analysis import AnalysisContext, SubmissionValidationRequest
from app.schemas.file import UploadedDocument
from app.service.analysis_service import AnalysisService

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/tasks", status_code=202)
async def create_analysis_task(
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
    file: Annotated[UploadFile, File(...)],
    request_id: Annotated[str, Form(...)],
    schema_version: Annotated[str, Form()] = "po_order.v1",
    context: Annotated[str, Form()] = "{}",
    auto_start: Annotated[bool, Form()] = True,
) -> dict[str, Any]:
    """创建单份托书分析任务。"""

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


@router.get("/files")
async def list_analysis_files(
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """返回已上传文件及其任务状态。"""

    return {"items": service.list_tasks()}


@router.get("/tasks/{task_id}/events")
async def stream_analysis_events(
    task_id: str,
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> StreamingResponse:
    """通过 SSE 推送任务状态和节点事件。"""

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


@router.get("/tasks/{task_id}/events/history")
async def get_analysis_event_history(
    task_id: str,
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """返回任务已落盘的生命周期、业务与节点事件。"""

    try:
        return {"items": service.read_task_events(task_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND") from exc


@router.get("/tasks/{task_id}")
async def get_analysis_task(
    task_id: str,
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """返回一个任务的状态快照。"""

    try:
        return service.get_task_status(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND") from exc


@router.post("/tasks/{task_id}/cancel")
async def cancel_analysis_task(
    task_id: str,
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """取消一个在途任务：立即停止且不可恢复（重跑需要重新上传）。

    幂等：任务已到终态（完成/失败/已取消）时原样返回，不覆盖既有结果。
    """

    try:
        return service.cancel_task(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND") from exc


@router.get("/tasks/{task_id}/result")
async def get_analysis_result(
    task_id: str,
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """返回一个已完成的分析结果。"""

    try:
        return service.get_result(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="RESULT_NOT_FOUND") from exc


@router.post("/tasks/{task_id}/submission/validate")
async def validate_analysis_submission(
    task_id: str,
    request: SubmissionValidationRequest,
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> dict[str, Any]:
    """提交前校验：始发港/目的港转三字码 + 委托客户存在性（不发起真实提交）。"""

    return await service.validate_submission(task_id, request)


@router.get("/tasks/{task_id}/pages/{page_no}")
async def get_analysis_page_image(
    task_id: str,
    page_no: int,
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> FileResponse:
    """返回任务指定页的渲染图片，供前端预览区展示原件。"""

    try:
        image_path = service.get_page_image_path(task_id, page_no)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="PAGE_NOT_FOUND") from exc
    return FileResponse(image_path, media_type="image/png")
