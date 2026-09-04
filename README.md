# DocMind — AI 智能托书分析系统

从货代托书中自动提取订单字段的结构化分析服务。上传 `.doc` / `.xls` / `.pdf` 托书，
经 MinerU 解析、视觉模型复核、确定性规则 + DeepSeek 双路抽取，输出带证据、置信度与
校验状态的订单字段 JSON（`po_order.v1`）。

## 功能特性

固定 12 字段输出：不区分进口/出口/国内场景，统一抽取以下字段（缺失填 `null`）。

| 必填 | 字段 | 说明 | 必填 | 字段 | 说明 |
|---|---|---|---|---|---|
| ✅ | `sfg` | 始发港 | ✅ | `inwageallinprice` | 运费（金额；COLLECT/PREPAID 无效） |
| ✅ | `mdg` | 目的港 | ✅ | `hbrq` | 预计航班日期/船期 |
| ✅ | `ybpiece` | 件数 | ✅ | `fid` | 委托客户（由调用方 context 提供） |
| ✅ | `ybweight` | 重量（毛重） | 选填 | `shipper` / `consignee` | 发货人/收货人（名称/地址/电话/邮箱） |
| ✅ | `ybvolume` | 体积 | 选填 | `chinesepm` / `englishpm` | 中文/英文品名 |

核心原则：

- **宁可为空，不可猜错**：每个非空候选必须绑定原文证据；低置信度、冲突值一律拦截为
  `needs_review`，缺失字段不伪造；
- **双路抽取交叉核对**：确定性规则（港口/件重体/日期/品名正则）先行，DeepSeek 多模态
  模型补充；`full.md`、`content_list_v2.json`、页面图片三源交叉；
- **全链路可观测**：每个工作流节点记录 `started/succeeded/failed` 事件，落盘任务
  `process.log` 并通过 SSE 推送到前端时间线。

## 工作流

```text
parse_with_mineru（MinerU 解析）
  └─ route_image_step ── 有图片 ─→ read_images_with_vlm（VLM 读图）
  └───────── 无图片 ────────────────┐
                                    ↓
extract_candidates（确定性规则 + DeepSeek 结构化抽取）
  ↓ normalize_candidates（归一化） → validate_candidates（校验）
  ↓ resolve_conflicts（冲突消解） → calculate_confidence（置信度）
  ↓ finalize_result（生成 business_result.json）
```

## 快速开始

环境要求：Windows / macOS / Linux，[uv](https://docs.astral.sh/uv/)，Python 3.12。

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量（项目根目录 .env）
#    MINERU_API_KEY=...            MinerU 官网申请
#    DEEPSEEK_API_KEY=...          DeepSeek 官网申请
#    # 可选：DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / DOCMIND_DATA_ROOT 等

# 3. 启动服务（默认 127.0.0.1:8001）
uv run python main.py

# 或使用 uvicorn（支持热重载）
uv run uvicorn app.main:app --port 8001 --reload
```

启动后访问：

| 地址 | 说明 |
|---|---|
| `http://127.0.0.1:8001/` | 内置分析前端（上传、运行、查看结果） |
| `http://127.0.0.1:8001/docs` | OpenAPI 接口文档 |
| `http://127.0.0.1:8001/health` | 健康检查 |

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/analysis/tasks` | 上传托书创建分析任务（multipart，支持 context、auto_start） |
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
    "shipper": { "name": "...", "address": ["..."], "phone": null, "email": null },
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
    requirements/ 输出字段目录与必填/选填清单
  workflow/       LangGraph 工作流（节点、事件、统一执行器）
  agent/          DeepSeek 抽取/VLM 调用
  collector/      MinerU 解析客户端
  schemas/        Pydantic 模型（输出字段、候选、结果）
  storage/        本地文件存储（data/ 目录契约）
  prompts/        提示词文件
  static/         内置前端页面
tests/            单元测试 + golden 样例集
docs/             需求分析文档
data/             运行时产物（不入库）
```

## 开发

```bash
uv run pytest                   # 运行全部测试
uv run ruff check app tests     # lint
uv run mypy app                 # 类型检查
```

## 设计约定

- 所有文件读写经 `app/storage`，路径基于 `DOCMIND_DATA_ROOT`，`data/` 不入库；
- 提示词只存在于 `app/prompts/`，不在代码中内联；
- API → Service → Storage/Collector 单向依赖，Workflow 只调用 Service；
- 模型输出不直接作为业务最终值，必须经过 schema 校验、字段规则校验与冲突消解。
