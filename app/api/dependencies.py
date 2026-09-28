import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.service.analysis_service import AnalysisService

logger = logging.getLogger(__name__)

# 票据请求头。`Authorization` 是 poOrder 自己的约定（`src/common/http.js` 对所有请求
# 统一注入 `Authorization: sessionStorage.ticket`）；备用头给不方便用 Authorization 的
# 调用方（部分代理/网关会改写或剥离这个头）。
TICKET_HEADER = "Authorization"
TICKET_FALLBACK_HEADER = "X-PoOrder-Ticket"


def _clean_ticket(raw: str) -> str:
    """去掉首尾空白，并剥掉 `Bearer ` 前缀（按标准写法带这个前缀的调用方也能识别）。"""

    value = str(raw or "").strip()
    if value[:7].lower() == "bearer ":
        value = value[7:].strip()
    return value


def current_ticket(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """取当前请求携带的 poOrder 票据；没有则返回空串。

    取值顺序：

    1. `Authorization: <ticket>` —— **推荐**。票据不进 URL，因此不会进浏览器历史、
       `Referer` 与网关访问日志（放 URL 里等于把登录凭据写进日志）；
    2. `X-PoOrder-Ticket: <ticket>` —— 备用通道，同语义；
    3. `?ticket=<ticket>` —— **仅开发期兜底**，由 `DOCMIND_ALLOW_URL_TICKET` 控制
       （默认关闭；关闭时该参数会被忽略并记一条告警）。

    票据只做**透传**：DocMind 不签发、不校验、不落盘，只在本次请求内交给 poOrder
    （提交订单 `api/ExHpoAxpline`、用户默认设置 `api/UserTemplet`）。拿不到票据时各
    调用点自行降级，不在这一层报错。
    """

    for name in (TICKET_HEADER, TICKET_FALLBACK_HEADER):
        ticket = _clean_ticket(request.headers.get(name, ""))
        if ticket:
            return ticket
    url_ticket = _clean_ticket(request.query_params.get("ticket", ""))
    if not url_ticket:
        return ""
    if settings.allow_url_ticket:
        logger.debug("从 URL 参数取票据（仅开发期通道）：path=%s", request.url.path)
        return url_ticket
    logger.warning(
        "忽略 URL 里的票据：DOCMIND_ALLOW_URL_TICKET 未开启（生产请改用 %s 请求头）。path=%s",
        TICKET_HEADER,
        request.url.path,
    )
    return ""


@lru_cache
def get_analysis_service() -> AnalysisService:
    """返回进程级分析服务。"""

    return AnalysisService(get_settings())
