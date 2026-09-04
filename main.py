import os

import uvicorn


def main() -> None:
    """从项目根目录启动 DocMind 服务。"""

    host = os.getenv("DOCMIND_HOST", "127.0.0.1")
    port = int(os.getenv("DOCMIND_PORT", "8001"))
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=bool(os.getenv("DOCMIND_RELOAD", "1") == "1"),
    )


if __name__ == "__main__":
    main()
