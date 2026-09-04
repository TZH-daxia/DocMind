# DocMind — AI 智能托书分析系统

从货代托书中自动提取订单字段的结构化分析服务。上传 `.doc` / `.xls` / `.pdf` 托书，
本地渲染为页面图片后由视觉模型（VLM）直读版式，再经确定性规则 + DeepSeek 双路抽取，
输出带证据、置信度与校验状态的订单字段 JSON（`po_order.v1`）。

## 功能特性

固定 12 字段输出：不区分进口/出口/国内场景，统一抽取以下字段（缺失填 `null`）。

| 必填 | 字段 | 说明 | 必填 | 字段 | 说明 |
|---|---|---|---|---|---|
| ✅ | `sfg` | 始发港 | ✅ | `inwageallinprice` | 运费（金额；COLLECT/PREPAID 无效） |
| ✅ | `mdg` | 目的港 | ✅ | `hbrq` | 预计航班日期/船期 |
| ✅ | `ybpiece` | 件数 | ✅ | `fid` | 委托客户（由调用方 context 提供） |
| ✅ | `ybweight` | 重量（毛重） | 选填 | `shipper` / `consignee` | 发货人/收货人（名称/单个完整地址/电话/邮箱） |
| ✅ | `ybvolume` | 体积 | 选填 | `chinesepm` / `englishpm` | 中文/英文品名 |

核心原则：

- **宁可为空，不可猜错**：每个非空候选必须绑定原文证据；低置信度、冲突值一律拦截为
  `needs_review`，缺失字段不伪造；
- **版式直读 + 双源交叉**：文档先转换为页面图片由 VLM 直接读取版式（表格结构、空格子、
  栏头一目了然），同时保留文本层（Markdown/HTML 表格）供规则与文本模型使用，多源交叉核对；
- **港口地名白名单**：始发港/目的港的规则候选必须命中地名白名单，模型候选未命中时降级
  人工复核，避免"始发地其他费用"这类粘连文本被误当港口；
- **地址单字符串**：国外地址中的逗号/换行是同一地址的层级写法（街道、邮编、城市、国家），
  发货人/收货人地址合并为单个完整字符串输出，不拆列表；
- **全链路可观测**：每个工作流节点记录 `started/succeeded/failed/skipped` 事件，落盘任务
  `process.log` 并通过 SSE 推送到前端时间线，节点逐个点亮、跳过节点明确标注。

## 工作流

默认使用 **local 渲染后端**（不经 MinerU）：

```text
render_document（PDF→PyMuPDF / DOC→Word COM / XLS→Excel COM 行高修正，转页面图片+文本层）
  ↓
read_images_with_vlm（VLM 直读页面图片）
  ↓
extract_candidates（确定性规则 + DeepSeek 结构化抽取）
  ↓ normalize_candidates（归一化） → validate_candidates（校验）
  ↓ resolve_conflicts（冲突消解） → calculate_confidence（置信度）
  ↓ finalize_result（生成 business_result.json）
```

如需切回 MinerU 解析，在 `.env` 配置 `DOCMIND_PARSE_BACKEND=mineru`：

```text
parse_with_mineru（MinerU 解析）
  └─ 有页面图片 ─→ read_images_with_vlm（VLM 读图）
  └─ 无页面图片 ─→ extract_candidates（VLM 节点标记 skipped）
  ↓ 后续节点与 local 后端一致
```

## 快速开始

环境要求：Windows（DOC/XLS 渲染依赖本机 Office；无 Office 时 XLS 自动降级为纯 Python
合成表格图）、[uv](https://docs.astral.sh/uv/)、Python 3.12。

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量（项目根目录 .env）
#    DEEPSEEK_API_KEY=...          DeepSeek 官网申请（必需）
#    MINERU_API_KEY=...            仅 DOCMIND_PARSE_BACKEND=mineru 时需要
#    # 可选：DOCMIND_PARSE_BACKEND=local|mineru（默认 local）
#    # 可选：DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / DOCMIND_DATA_ROOT 等

# 3. 启动服务（默认 127.0.0.1:8001）
uv run python main.py

# 或使用 uvicorn（支持热重载）
uv run uvicorn app.main:app --port 8001 --reload
```

启动后访问：

| 地址 | 说明 |
|---|---|
| `http://127.0.0.1:8001/` | 内置分析前端（上传即自动运行、节点时间线、查看结果） |
| `http://127.0.0.1:8001/docs` | OpenAPI 接口文档 |
| `http://127.0.0.1:8001/health` | 健康检查 |

## 数据目录契约

`data/` 下只保留三个目录（均已 gitignore）：

```text
data/
├─ uploaded_documents/
│    └─ <原始文件名>                        用户上传的原件（按原名保存）
├─ parsed_documents/
│    └─ task_<时间戳>_<文件名>_<id>/        单个任务的全部工作文件
│         ├─ <文件名>_page_001.png          渲染页面图片（VLM 输入）
│         ├─ <文件名>_full.md               文本层（规则/文本模型输入）
│         ├─ <文件名>_vlm_image_content.md  VLM 视觉理解文档
│         ├─ task_status.json / process.log 任务状态与节点事件流
│         └─ *_candidates.json 等           归一化/校验/决议中间产物
└─ analysis_results/
     └─ <task_id>.json                     最终业务结果
```

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/analysis/tasks` | 上传托书创建分析任务（multipart，支持 context、auto_start，默认自动运行） |
| `GET` | `/api/v1/analysis/files` | 任务文件列表 |
| `POST` | `/api/v1/analysis/tasks/{id}/run` | 运行/重跑任务 |
| `GET` | `/api/v1/analysis/tasks/{id}` | 任务状态与进度 |
| `GET` | `/api/v1/analysis/tasks/{id}/events` | SSE 实时节点事件 |
| `GET` | `/api/v1/analysis/tasks/{id}/result` | 分析结果 JSON |
| `GET` | `/api/v1/analysis/schemas/po_order/{version}` | 目标字段 schema（含中文名/必填标记） |

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
    "shipper": { "name": "...", "address": "街道, 邮编 城市, 国家", "phone": null, "email": null },
    "consignee": null,
    "chinesepm": "合纤针织女式连衣裙/化纤针织女式开襟衫",
    "englishpm": "WOMEN KNITTED DRESS/WOMEN KNITTED CARDIGAN"
  },
  "overall_status": "ready",
  "overall_confidence": 0.97,
  "field_meta": {
    "sfg": { "value": "SHANGHAI", "status": "normalized", "confidence": 0.99, "evidence": ["..."] }
  },
  "validation": { "is_valid": true, "errors": [], "warnings": [] }
}
```

## 项目结构

```text
app/
  api/            FastAPI 路由（薄层：校验 → 调 Service）
  service/        业务逻辑：抽取规则、校验、归一化、冲突消解、结果构建
    field_rules/  确定性字段规则（港口/件重体/日期/品名等）
      port_whitelist.py   港口/城市地名白名单（sfg/mdg 候选校验）
    requirements/ 输出字段目录与必填/选填清单
  workflow/       LangGraph 工作流（节点、事件、统一执行器、local/mineru 双后端）
  agent/          DeepSeek 抽取/VLM 调用
  collector/      document_renderer.py 本地文档渲染；mineru_client.py MinerU 客户端
  schemas/        Pydantic 模型（输出字段、候选、结果）
  storage/        本地文件存储（data/ 三目录契约）
  prompts/        提示词文件
  static/         内置前端页面（节点常驻时间线、逐个点亮、跳过标注）
tests/            单元测试 + golden 样例集
docs/             需求分析文档
data/             运行时产物（不入库，三目录契约见上文）
```

## 开发

```bash
uv run pytest                   # 运行全部测试
uv run ruff check app tests     # lint
uv run mypy app                 # 类型检查
```

## 设计约定

- 所有文件读写经 `app/storage`，路径基于 `DOCMIND_DATA_ROOT`，`data/` 只保留
  uploaded_documents / parsed_documents / analysis_results 三个目录；
- local 后端的文档渲染经 `app/collector/document_renderer.py`，页面上限 10 页、
  VLM 读图上限 6 张；
- 提示词只存在于 `app/prompts/`，不在代码中内联；
- API → Service → Storage/Collector 单向依赖，Workflow 只调用 Service；
- 模型输出不直接作为业务最终值，必须经过 schema 校验、字段规则校验与冲突消解。
