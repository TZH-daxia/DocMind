# DocMind — AI 智能托书分析系统

从货代托书中自动提取订单字段的结构化分析服务。上传 `.doc` / `.xls` / `.pdf` 托书，
本地渲染为页面图片后由视觉模型（VLM）直读版式并抽取字段，结果原样保留，
仅按置信度阈值标记待人工审核，输出订单字段 JSON（`po_order.v1`）。

## 功能特性

固定 12 字段输出：不区分进口/出口/国内场景，统一抽取以下字段（缺失填 `null`），
必填 8 个在前、选填 4 个在后：

| 字段 | 必填 | 说明 |
|---|---|---|
| `sfg` | ✅ | 始发港 |
| `mdg` | ✅ | 目的港 |
| `ybpiece` | ✅ | 件数 |
| `ybweight` | ✅ | 重量（毛重） |
| `ybvolume` | ✅ | 体积 |
| `inwageallinprice` | ✅ | 运费（应收运费价格；COLLECT/PREPAID 条款视为无效） |
| `hbrq` | ✅ | 预计航班日期/船期 |
| `fid` | ✅ | 委托客户（托书内无此字段，由调用方 context 提供） |
| `shipper` / `consignee` | 选填 | 发货人/收货人（name/address/phone/email，地址为单个完整字符串） |
| `chinesepm` / `englishpm` | 选填 | 中文/英文品名 |

核心原则：

- **宁可为空，不可猜错**：每个非空候选必须绑定原文证据；低置信度一律拦截为
  `needs_review`，缺失字段不伪造；
- **版式直读**：文档先转换为页面图片由 VLM 直接读取版式（表格结构、空格子、栏头一目了然），
  抽取结果原样保留，不经标准化/校验改写，仅按置信度阈值标记待人工审核；
- **结构化输出硬约束**：抽取阶段通过 `PoOrderExtraction` JSON Schema 强制 12 个字段全部
  出现（缺失由 schema 填 `null`），从结构上杜绝模型漏字段；模型不支持结构化输出时自动
  回退 free-form JSON 解析，行为不回退；
- **置信度阈值审核**：每个候选自带置信度，低于 `review_confidence_threshold`（默认 0.6）的
  字段标记 `needs_review` 并保留原值，前端提示"待人工审核"，不静默丢弃；
- **地址单字符串**：国外地址中的逗号/换行是同一地址的层级写法（街道、邮编、城市、国家），
  发货人/收货人地址合并为单个完整字符串输出，不拆列表；
- **全链路可观测**：每个工作流节点记录 `started/succeeded/failed/skipped` 事件，落盘任务
  `process.log` 并通过 SSE 推送到前端时间线，节点逐个点亮、跳过节点明确标注；已完成任务
  可通过历史事件接口回放执行过程。
- **日志分层**：系统与 HTTP 访问日志按日轮转写入 `logs/app.log`；每个文件的生命周期、
  节点耗时和结果摘要写入该任务自己的 `process.log`。

## 工作流

统一使用 **local 渲染链路**，上传文件即自动运行（无手动重跑）：

```text
render_document（PDF→PyMuPDF 直接光栅化 / DOC、XLS→LibreOffice 转 PDF 后光栅化，
                 XLS 导出前自动展开隐藏行列、修正合并单元格行高，近空白页自动过滤）
  ↓
read_images_with_vlm（VLM 直读页面图片，逐字转写为视觉理解文档）
  ↓
extract_candidates（DeepSeek 按 PoOrderExtraction Schema 结构化抽取 12 字段候选）
  ↓
build_result（按候选置信度生成结果：值原样保留，低于阈值标记待人工审核）
```

渲染降级链（XLS）：LibreOffice UNO 修正导出 → LibreOffice CLI 直接转换 → 纯 Python
合成表格图（xlrd + Pillow，零 LibreOffice 依赖）。

模型抽取结果不做标准化/校验/冲突重判，仅按置信度阈值（`review_confidence_threshold`，
默认 0.6）判定字段是否需要人工审核，避免准确结果被下游规则误过滤为空值。

## 快速开始

环境要求：[uv](https://docs.astral.sh/uv/)、Python 3.12、**LibreOffice**
（DOC/XLS 转 PDF，Windows 安装后自动探测，Linux 用 `apt install libreoffice`）。
跨平台可用，不依赖 MS Office / pywin32；无 LibreOffice 时 XLS 自动降级为纯 Python
合成表格图（.doc 则报错提示安装）。

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量：复制示例文件并填入真实 DeepSeek API Key
cp .env.example .env

# 3. 启动服务（默认 127.0.0.1:8000）
uv run python main.py

# 或使用 uvicorn（支持热重载）
uv run uvicorn app.main:app --port 8000 --reload
```

启动后访问：

| 地址 | 说明 |
|---|---|
| `http://127.0.0.1:8000/` | 内置分析前端（拖拽上传即自动运行、节点时间线、查看结果） |
| `http://127.0.0.1:8000/docs` | OpenAPI 接口文档 |
| `http://127.0.0.1:8000/health` | 健康检查 |

## 配置

通过 `.env` 或环境变量配置（完整项见 `.env.example`），渲染与转换相关的关键项：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DOCMIND_SOFFICE_PATH` | 空（自动探测） | LibreOffice `soffice` 可执行路径；留空按 PATH 与常见安装位置探测 |
| `DOCMIND_RENDER_BLANK_PAGE_RATIO` | `0.005` | 近空白页过滤阈值（非白像素占比），设 `0` 关闭过滤 |
| `DOCMIND_DATA_RETENTION_HOURS` | `24.0` | 任务产物保留时长（小时）：`parsed_documents`（含上传原件）与 `analysis_results` 中超过该时长的内容会在服务启动时与每小时自动清理（运行中的任务跳过，`reference_cache` 不参与）；设 `0` 永久保留。建议大于单任务最长耗时，否则可能删掉在途任务的原件 |
| `DOCMIND_RELOAD` | `0`（关闭） | 热重载默认关闭：reload 会中断在途分析任务；调试时显式设 `1` |
| `DOCMIND_CONSOLE_LOG` | `0`（关闭） | 控制台日志开关，默认只写 `logs/app.log`（写 stdout 管道可能阻塞事件循环） |
| `DOCMIND_MAX_CONCURRENT_TASKS` | `20` | 同时存活的任务数上限（兜底），超出的任务快照保持 `queued` 排队；默认可覆盖"3 人同时各上传 5 份" |
| `DOCMIND_LO_MAX_CONCURRENT` | `5` | 同时进行的 LibreOffice 转换数：每个转换拉起独立 `soffice` 进程（单实例约 200~400MB），不建议超过 CPU 核数，内存吃紧或转换超时时调小到 2~3 |
| `DOCMIND_MODEL_MAX_CONCURRENT` | `8` | 同时进行的模型调用数（视觉识别 + 字段抽取），上游 429 或大面积超时时调小到 3~5 |
| `DOCMIND_MAX_FILE_SIZE_BYTES` | `52428800`（50MB） | 单文件大小上限；校验发生在内容读入内存之后，调大会同步放大请求内存占用 |

### 并发容量（默认参数）

| 场景 | 表现 |
|---|---|
| 1 人上传 1~5 份 | 立即进入流水线，无排队 |
| 3 人同时各上传 5 份（15 个任务） | 全部进入执行，无 `queued`；模型阶段分两批，约 4~6 分钟出完全部结果 |
| 提交量超过 20 个任务 | 超出的任务保持 `queued` 排队，等前序任务结束后自动补位 |

吞吐受 `DOCMIND_MODEL_MAX_CONCURRENT` 与上游响应耗时支配：单任务在模型阶段占用
约 `VLM 转写 + 字段抽取`（thinking 开启时约 120s），据此 `8 并发 ≈ 240 份/小时`。
LibreOffice 只处理 doc/docx/xls/xlsx（单页约 5~15s），PDF 走 PyMuPDF 直接光栅化，
因此一般情况下不是瓶颈。真实容量请以服务器上的节点 `duration_ms` 实测为准。

`converter` 字段（`render_meta.json` / `task_status.json`）记录实际使用的渲染路径：
`pymupdf` / `libreoffice_uno`（含行列展开与行高修正）/ `libreoffice`（CLI 直接转换）/
`synthetic`（纯 Python 合成图兜底）。

## Docker 部署（Linux 服务器）

镜像已内置 Python 3.12 运行时、LibreOffice（Writer + Calc，`DOCMIND_SOFFICE_PATH`
留空自动探测到 `/usr/bin/soffice`）、中文字体（文泉驿），**服务器无需额外安装任何依赖**。

### 前置条件

- Docker 24+ 与 Docker Compose v2（`docker compose version` 可查）
- 建议 2 核 4GB 以上：5 份文件并发时峰值约 2~3GB，主要来自 LibreOffice
- DeepSeek API Key

### 部署步骤

```bash
# 1. 获取代码
git clone <你的仓库地址> && cd DocMind

# 2. 配置密钥（.env 已被 .gitignore 排除，不会被提交）
cp .env.example .env
# 编辑 .env，至少填入 DEEPSEEK_API_KEY

# 3. 构建并后台启动（首次需下载依赖 + LibreOffice，约 5~10 分钟）
docker compose up -d --build

# 4. 查看启动日志
docker compose logs -f docmind

# 5. 健康检查
curl http://127.0.0.1:8000/health
```

启动后浏览器打开 `http://<服务器IP>:8000/` 即可使用（接口文档 `/docs`）。

改宿主端口：`HOST_PORT=9001 docker compose up -d`（容器内固定 8000）。

### 数据与日志

| 内容 | 位置 |
|---|---|
| 上传原件、任务产物、分析结果 | 卷 `docmind-data` → 容器 `/app/data` |
| 系统日志 | 卷 `docmind-logs` → 容器 `/app/logs`（也可 `docker compose logs -f`） |

升级：`git pull && docker compose up -d --build`（数据卷不受影响）。

### 部署相关参数

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HOST_PORT` | `8000` | 宿主机映射端口（compose 读取，非应用变量） |
| `DOCMIND_HOST` | `0.0.0.0` | compose 已强制设置：容器内监听 `127.0.0.1` 时宿主机访问不到 |
| `DEEPSEEK_API_KEY` | 必填 | 缺失时服务启动即失败，表现为容器反复重启 |
| `DOCMIND_LO_MAX_CONCURRENT` | `5` | 内存吃紧或转换超时时调小到 2~3 |
| `DOCMIND_MAX_CONCURRENT_TASKS` | `12` | 同时存活任务数上限（兜底） |
| `DOCMIND_MODEL_MAX_CONCURRENT` | `5` | 上游 429 或大面积超时时调小到 3 |
| `UV_INDEX_URL` | 空 | 构建参数：境外服务器改用 `--build-arg UV_INDEX_URL=https://pypi.org/simple` |

> 任务在单个进程内调度，状态与产物落在文件与卷上，**不要开多副本或多 uvicorn worker**：
> 多实例会把彼此正在运行的任务在启动时误判为中断并标记失败。

### 常见问题

| 现象 | 排查 |
|---|---|
| 页面正常但 DOC/XLS 转换失败 | `docker compose exec docmind bash -lc 'soffice --version; ls -l /usr/lib/libreoffice/program/python'` |
| 中文渲染成方块 | 镜像已装文泉驿；需要其它字体时挂载 `- /usr/share/fonts:/usr/share/fonts:ro` 并在容器内执行 `fc-cache -f` |
| 构建卡在下载依赖 | 境外服务器加 `--build-arg UV_INDEX_URL=https://pypi.org/simple` |
| 构建卡在 `apt-get update` | 加 `--build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn`（或 `mirrors.aliyun.com`） |
| 拉取基础镜像超时（Docker Hub 不通） | 镜像已改用 `ghcr.io` 源；可在 Docker Desktop → Settings → Docker Engine 配 `registry-mirrors` 加速 |
| 内存占用高 | 降低 `DOCMIND_LO_MAX_CONCURRENT`（每个 soffice 约 200~400MB）；PDF 不经过 LibreOffice |
| 容器反复重启 | `docker compose logs docmind`，通常是 `DEEPSEEK_API_KEY` 未配置 |
| 转换报超时 | 5 并发对低配机器偏重，调到 2~3；单份超时阈值为 180s |

## 数据目录契约

`data/` 下只保留三个目录（均已 gitignore）：

```text
data/
├─ parsed_documents/
│    └─ task_<时间戳>_<文件名>_<id>/        单个任务的全部工作文件
│         ├─ <原始文件名>                  上传原件（按任务隔离，同名不互相覆盖）
│         ├─ <文件名>_converted.pdf         DOC/XLS 统一转换的 PDF 中间件
│         ├─ <文件名>_page_001.png          渲染页面图片（VLM 输入）
│         ├─ <文件名>_render_meta.json      渲染元信息
│         ├─ <文件名>_vlm_image_content.md  VLM 视觉理解文档
│         ├─ <文件名>_candidates.json       12 字段抽取候选
│         └─ task_status.json / process.log 任务状态与节点事件流
├─ analysis_results/
│    └─ <task_id>.json                     最终业务结果
└─ reference_cache/                        外部主数据本地缓存（港口 hbinfo，按自身 TTL 清理）
```

> 上传原件存进各任务的 `parsed_documents/<task_id>/` 而非全局目录：托书模板常出现
> 同名文件（如 `托书.xls`），全局存放会被后来的上传覆盖，而任务是在后台才读取源
> 文件，会导致任务分析到别人的文档且不报错。

## API 概览

> 接口统一前缀为 `/docmind`（由 `Settings.api_prefix` 控制，可用 `DOCMIND_API_PREFIX` 覆盖；
> 修改时需同步前端 `app/static/api.js` 与 `app/static/result-dialog/api.js` 的接口根路径）。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/docmind/analysis/tasks` | 上传托书创建任务（multipart，默认自动开始分析） |
| `GET` | `/docmind/analysis/files` | 任务文件列表 |
| `GET` | `/docmind/analysis/tasks/{id}` | 任务状态与进度 |
| `GET` | `/docmind/analysis/tasks/{id}/events` | SSE 实时节点事件（含历史回放，任务结束后自动关闭） |
| `GET` | `/docmind/analysis/tasks/{id}/events/history` | 已落盘的全部节点事件（回看已完成任务） |
| `GET` | `/docmind/analysis/tasks/{id}/result` | 分析结果 JSON |

### 结果结构示例

```json
{
  "task_id": "task_...",
  "schema_version": "po_order.v1",
  "result": {
    "sfg": "SHANGHAI",
    "mdg": "FRANKFURT",
    "ybpiece": 169,
    "ybweight": 3109,
    "ybvolume": 15.2,
    "inwageallinprice": null,
    "hbrq": null,
    "fid": null,
    "shipper": { "name": "...", "address": "街道, 邮编 城市, 国家", "phone": "..." },
    "consignee": null,
    "chinesepm": "合纤针织女式连衣裙/化纤针织女式开襟衫",
    "englishpm": "WOMEN KNITTED DRESS/WOMEN KNITTED CARDIGAN"
  },
  "overall_status": "needs_review",
  "overall_confidence": 0.98,
  "field_meta": {
    "sfg": {
      "value": "SHANGHAI",
      "status": "normalized",
      "confidence": 0.99,
      "evidence": [{ "quote": "始发站 Airport of Departure SHANGHAI" }]
    },
    "hbrq": {
      "value": null,
      "status": "missing",
      "confidence": 0.0,
      "evidence": []
    }
  },
  "validation": { "is_valid": true }
}
```

`overall_status` 规则：必填字段缺失或低于置信度阈值 → `needs_review`；全部就绪 → `ready`。

## 项目结构

```text
main.py            服务启动入口（读取 DOCMIND_HOST/PORT/RELOAD）
app/
  api/             FastAPI 路由（薄层：校验 → 调 Service）
  service/         业务编排 analysis_service.py
    requirements/  输出字段清单与必填判定、context 字段回填
  workflow/        LangGraph 工作流（4 节点、事件发布、统一节点执行器）
    nodes/         render_document / read_images_with_vlm / extract_candidates / build_result
  agent/           deepseek_extractor.py（结构化抽取 + free-form 回退、VLM 调用）
  collector/       document_renderer.py 本地文档渲染（PDF/DOC/XLS → 页面图片）
                   lo_xls_height_fix.py LibreOffice UNO 脚本（隐藏行列展开、合并单元格行高修正）
  schemas/         Pydantic 模型（po_order 字段目录、analysis 候选/结果、file 上传）
  storage/         本地文件存储（data/ 三目录契约）
  prompts/         提示词文件（视觉理解、字段抽取）
  static/          内置前端（拖拽上传、节点时间线、历史任务回放、结果 JSON）
data/              运行时产物（不入库，三目录契约见上文）
```

## 开发

```bash
uv run ruff check app    # lint
uv run mypy app          # 类型检查
```

## 设计约定

- 所有文件读写经 `app/storage`，路径基于 `DOCMIND_DATA_ROOT`；上传原件与任务产物
  统一放在 `parsed_documents/<task_id>/`（同名文件不会跨任务互相干扰）；
- 文档渲染经 `app/collector/document_renderer.py`：页面上限 10 页、光栅化 200 DPI，
  近空白页（非白像素占比低于阈值）自动过滤不送 VLM；VLM 输入上限 6 张图：
  每页整页图，第一页额外附上下两半放大图（12% 重叠，保证小字号可辨认）；
- XLS 渲染前由 LibreOffice UNO 脚本（`app/collector/lo_xls_height_fix.py`）展开
  隐藏行列、修正合并单元格行高，避免合并区域内容被裁切；
- 提示词只存在于 `app/prompts/`，不在代码中内联；
- API → Service → Storage/Collector 单向依赖；Workflow 节点通过 `WorkflowHandlers`
  回调 Service 方法，节点本身不直接触碰存储；
- 模型输出即最终值：只做 Pydantic Schema 结构校验与置信度阈值判定，不做标准化改写、
  字段规则过滤或冲突消解，缺失/低置信度一律交给人工审核；
- 上传即运行：不提供手动重跑入口，任务失败请重新上传文件。
