# DocMind 运行镜像：Python 3.12 + LibreOffice（DOC/XLS 转 PDF）+ uv 依赖
#
# 构建：docker compose build
# 运行：docker compose up -d
# 境外服务器访问不了 uv.lock 里的清华源时：
#   docker compose build --build-arg UV_INDEX_URL=https://pypi.org/simple

# -------------------------------
# 1) 依赖阶段：用 uv 生成独立 venv
# -------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# venv 放在 /opt/venv，与源码目录解耦，便于整体拷贝到运行阶段
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 覆盖默认索引源（可选，仅在显式传 build-arg 时生效）
ARG UV_INDEX_URL

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    sh -c 'if [ -n "$UV_INDEX_URL" ]; then export UV_INDEX_URL; fi; uv sync --frozen --no-dev'

# -------------------------------
# 2) 运行阶段
# -------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # 容器内必须监听 0.0.0.0，否则宿主机访问不到（默认 127.0.0.1 只在容器内可达）
    DOCMIND_HOST=0.0.0.0 \
    DOCMIND_PORT=8001 \
    DOCMIND_DATA_ROOT=/app/data \
    DOCMIND_SYSTEM_LOG_FILE=/app/logs/app.log \
    # soffice 需要可写的 HOME 存放配置
    HOME=/root \
    TZ=Asia/Shanghai

# Debian 官方源在国内服务器可能很慢：构建时传 --build-arg APT_MIRROR=主机名 替换
# 例如 APT_MIRROR=mirrors.tuna.tsinghua.edu.cn / mirrors.aliyun.com
ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
            [ -f "$f" ] && sed -i "s|deb.debian.org|$APT_MIRROR|g; s|security.debian.org|$APT_MIRROR|g" "$f"; \
        done; \
    fi

# LibreOffice：Writer 负责 DOC/DOCX，Calc 负责 XLS/XLSX，python3-uno 提供
# XLS 行高修正脚本需要的 uno 模块；
# 中文字体：LibreOffice 转 PDF 与合成图都依赖系统中文字体，缺失会渲染成方块
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-calc \
        python3-uno \
        fonts-wqy-zenhei \
        fonts-wqy-microhei \
        tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && (command -v fc-cache >/dev/null && fc-cache -f || true)

# XLS 行高修正调用 LibreOffice 自带的 python（与 soffice 同级目录）；
# Debian 未提供该入口时回退到系统 python3（uno 模块由 python3-uno 提供）
RUN if [ ! -e /usr/lib/libreoffice/program/python ]; then \
        ln -s /usr/bin/python3 /usr/lib/libreoffice/program/python; \
    fi

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY main.py ./
COPY app ./app

RUN mkdir -p /app/data /app/logs

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('DOCMIND_PORT', '8001') + '/health', timeout=4)"

# 单进程 uvicorn（任务在进程内调度，不要开多 worker / 多副本）
CMD ["python", "main.py"]
