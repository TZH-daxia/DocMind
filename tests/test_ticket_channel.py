"""票据通道：请求头优先，`?ticket=` 仅在 DOCMIND_ALLOW_URL_TICKET 打开时作开发兜底。

票据最终要透传给 poOrder（提交订单 `api/ExHpoAxpline`、用户默认设置
`api/UserTemplet`），取值口径放错通道的后果是：要么票据被写进浏览器历史 / `Referer`
/ 网关访问日志，要么线上提交时拿不到票据。所以这里把优先级与开关都钉住。
"""

from typing import Any

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.dependencies import current_ticket, get_analysis_service
from app.config import Settings


def make_request(query: str = "", headers: dict[str, str] | None = None) -> Request:
    """构造最小请求：`current_ticket` 只读 headers / query_params。"""

    raw = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/docmind/analysis/submit",
            "query_string": query.encode("latin-1"),
            "headers": raw,
        }
    )


def build_settings(monkeypatch: Any, allow_url_ticket: bool) -> Settings:
    """按环境变量构配置：_env_file=None 保证不受本机 .env 影响，只取决于入参。"""

    monkeypatch.setenv("DOCMIND_ALLOW_URL_TICKET", "1" if allow_url_ticket else "0")
    return Settings(_env_file=None)


def test_header_wins_over_query_and_body_channel(monkeypatch: Any) -> None:
    settings = build_settings(monkeypatch, allow_url_ticket=True)

    ticket = current_ticket(
        make_request(query="ticket=URL-TICKET", headers={"Authorization": "HDR-TICKET"}),
        settings,
    )

    assert ticket == "HDR-TICKET"


def test_authorization_wins_over_fallback_header(monkeypatch: Any) -> None:
    settings = build_settings(monkeypatch, allow_url_ticket=True)

    ticket = current_ticket(
        make_request(headers={"X-PoOrder-Ticket": "FALLBACK", "Authorization": "MAIN"}),
        settings,
    )

    assert ticket == "MAIN"


def test_fallback_header_used_when_authorization_absent(monkeypatch: Any) -> None:
    settings = build_settings(monkeypatch, allow_url_ticket=False)

    ticket = current_ticket(make_request(headers={"X-PoOrder-Ticket": "FALLBACK"}), settings)

    assert ticket == "FALLBACK"


def test_bearer_prefix_is_stripped(monkeypatch: Any) -> None:
    settings = build_settings(monkeypatch, allow_url_ticket=False)

    ticket = current_ticket(
        make_request(headers={"Authorization": "Bearer TICKET-1"}), settings
    )

    assert ticket == "TICKET-1"


def test_blank_header_falls_through_to_query(monkeypatch: Any) -> None:
    settings = build_settings(monkeypatch, allow_url_ticket=True)

    ticket = current_ticket(
        make_request(query="ticket=URL-TICKET", headers={"Authorization": "   "}), settings
    )

    assert ticket == "URL-TICKET"


def test_query_ticket_used_only_when_enabled(monkeypatch: Any) -> None:
    request = make_request(query="ticket=URL-TICKET")

    assert current_ticket(request, build_settings(monkeypatch, True)) == "URL-TICKET"
    # 关闭时忽略 URL 里的票据：这是开发期通道，不该在生产生效
    assert current_ticket(request, build_settings(monkeypatch, False)) == ""


def test_no_ticket_anywhere_returns_empty(monkeypatch: Any) -> None:
    settings = build_settings(monkeypatch, allow_url_ticket=True)

    assert current_ticket(make_request(), settings) == ""


def test_submit_route_takes_ticket_from_header() -> None:
    """接线验证：路由从请求头取票据，优先级高于请求体里的兼容字段。"""

    from app.main import app

    captured: dict[str, Any] = {}

    class FakeService:
        async def submit_order(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {
                "ok": True,
                "order_code": "",
                "message": "",
                "payload": None,
                "response": None,
            }

    app.dependency_overrides[get_analysis_service] = FakeService
    try:
        response = TestClient(app).post(
            "/docmind/analysis/submit",
            json={
                "form": {"fid": "12794"},
                "order": {},
                "czman": "admin",
                "ticket": "BODY-TICKET",
            },
            headers={"Authorization": "HDR-TICKET"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["ticket"] == "HDR-TICKET"
    assert captured["czman"] == "admin"


def test_submit_route_falls_back_to_body_ticket() -> None:
    """旧调用方只在请求体里带票据时仍可用（不回归）。"""

    from app.main import app

    captured: dict[str, Any] = {}

    class FakeService:
        async def submit_order(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {
                "ok": True,
                "order_code": "",
                "message": "",
                "payload": None,
                "response": None,
            }

    app.dependency_overrides[get_analysis_service] = FakeService
    try:
        response = TestClient(app).post(
            "/docmind/analysis/submit",
            json={"form": {}, "order": {}, "czman": "admin", "ticket": "BODY-TICKET"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["ticket"] == "BODY-TICKET"
