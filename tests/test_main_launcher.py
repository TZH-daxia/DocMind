"""启动器辅助函数测试。"""

import main as launcher
from app.config import Settings
from app.main import format_startup_banner


def test_startup_banner_contains_base_url() -> None:
    settings = Settings(
        DOCMIND_HOST="127.0.0.1",
        DOCMIND_PORT=8000,
        deepseek_api_key="test-key",
    )
    banner = format_startup_banner(settings)

    assert "http://127.0.0.1:8000/" in banner
    assert "http://127.0.0.1:8000/docs" in banner
    assert "http://127.0.0.1:8000/health" in banner
    assert str(settings.system_log_file) in banner


def test_disable_console_quick_edit_skips_non_windows(monkeypatch) -> None:
    """非 Windows 平台应直接跳过，不触碰控制台 API。"""

    monkeypatch.setattr(launcher.os, "name", "posix")
    launcher._disable_console_quick_edit()
