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
render_document（PDF→PyMuPDF / DOC→Word COM / XLS→Excel COM 行高修正，转页面图片）
  ↓
read_images_with_vlm（VLM 直读页面图片，逐字转写为视觉理解文档）
  ↓
extract_candidates（DeepSeek 按 PoOrderExtraction Schema 结构化抽取 12 字段候选）
  ↓
build_result（按候选置信度生成结果：值原样保留，低于阈值标记待人工审核）
```

模型抽取结果不做标准化/校验/冲突重判，仅按置信度阈值（`review_confidence_threshold`，
默认 0.6）判定字段是否需要人工审核，避免准确结果被下游规则误过滤为空值。

## 快速开始

环境要求：Windows（DOC/XLS 渲染依赖本机 Office；无 Office 时 XLS 自动降级为纯 Python
合成表格图）、[uv](https://docs.astral.sh/uv/)、Python 3.12。

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量：复制示例文件并填入真实 DeepSeek API Key
cp .env.example .env

# 3. 启动服务（默认 127.0.0.1:8001）
uv run python main.py

# 或使用 uvicorn（支持热重载）
uv run uvicorn app.main:app --port 8001 --reload
```

启动后访问：

| 地址 | 说明 |
|---|---|
| `http://127.0.0.1:8001/` | 内置分析前端（拖拽上传即自动运行、节点时间线、查看结果） |
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
│         ├─ <文件名>_converted.pdf         DOC/XLS 统一转换的 PDF 中间件
│         ├─ <文件名>_page_001.png          渲染页面图片（VLM 输入）
│         ├─ <文件名>_render_meta.json      渲染元信息
│         ├─ <文件名>_vlm_image_content.md  VLM 视觉理解文档
│         ├─ <文件名>_candidates.json       12 字段抽取候选
│         └─ task_status.json / process.log 任务状态与节点事件流
└─ analysis_results/
     └─ <task_id>.json                     最终业务结果
```

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/analysis/tasks` | 上传托书创建任务（multipart，默认自动开始分析） |
| `GET` | `/api/v1/analysis/files` | 任务文件列表 |
| `GET` | `/api/v1/analysis/tasks/{id}` | 任务状态与进度 |
| `GET` | `/api/v1/analysis/tasks/{id}/events` | SSE 实时节点事件（含历史回放，任务结束后自动关闭） |
| `GET` | `/api/v1/analysis/tasks/{id}/events/history` | 已落盘的全部节点事件（回看已完成任务） |
| `GET` | `/api/v1/analysis/tasks/{id}/result` | 分析结果 JSON |

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
      "evidence": [{ "quote": "始发站 Airport of Departure SHANGHAI" }],
      "extraction_method": "llm"
    },
    "hbrq": {
      "value": null,
      "status": "missing",
      "confidence": 0.0,
      "evidence": [],
      "extraction_method": "none"
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

- 所有文件读写经 `app/storage`，路径基于 `DOCMIND_DATA_ROOT`，`data/` 只保留
  uploaded_documents / parsed_documents / analysis_results 三个目录；
- 文档渲染经 `app/collector/document_renderer.py`：页面上限 10 页，VLM 输入为每页整页图
  外加首页四象限放大图（保证小字号可辨认）；
- 提示词只存在于 `app/prompts/`，不在代码中内联；
- API → Service → Storage/Collector 单向依赖；Workflow 节点通过 `WorkflowHandlers`
  回调 Service 方法，节点本身不直接触碰存储；
- 模型输出即最终值：只做 Pydantic Schema 结构校验与置信度阈值判定，不做标准化改写、
  字段规则过滤或冲突消解，缺失/低置信度一律交给人工审核；
- 上传即运行：不提供手动重跑入口，任务失败请重新上传文件。
