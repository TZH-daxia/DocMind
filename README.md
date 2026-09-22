# DocMind — AI 智能托书分析系统

从货代托书中自动提取订单字段的结构化分析服务。上传 `.doc` / `.docx` / `.xls` / `.xlsx` / `.pdf`
托书，本地光栅化为页面图片后由视觉模型（VLM）直读版式并抽取字段，输出订单字段 JSON
（`po_order.v1`）。

## 功能特性

- **版式直读**：文档先渲染成页面图片再由 VLM 读取，表格结构、空格子、栏头一目了然；
  抽取结果原样保留，不做标准化改写。
- **宁可为空，不可猜错**：每个非空候选必须绑定原文证据，低置信度一律拦截为
  `needs_review`，缺失字段不伪造。
- **结构化输出硬约束**：抽取阶段由 `PoOrderExtraction` JSON Schema 强制 12 个字段全部
  出现（缺失由 schema 填 `null`），模型不支持结构化输出时自动回退 free-form JSON 解析。
- **全链路可观测**：每个节点记录 `started/succeeded/failed/skipped` 事件，落盘任务
  `process.log` 并经 SSE 推进前端时间线；已完成任务可回放执行过程。
- **可视化核对**：内置核对弹窗支持点击字段即在原件上高亮定位；委托客户 / 始发港 /
  目的港「输入即下拉」，候选取自主数据缓存。
- **运行控制**：任务可暂停、从断点继续、取消，操作均幂等。

## 快速开始

环境要求：[uv](https://docs.astral.sh/uv/)、Python 3.12、**LibreOffice**
（DOC/XLS 转 PDF；Windows 安装后自动探测，Linux 用 `apt install libreoffice`）。
跨平台可用，不依赖 MS Office / pywin32；无 LibreOffice 时 XLS 自动降级为纯 Python
合成表格图（`.doc` 则报错提示安装）。

```bash
uv sync                      # 安装依赖
cp .env.example .env         # 填入真实的 DEEPSEEK_API_KEY
uv run python main.py        # 启动服务，默认 127.0.0.1:8000
```

| 地址 | 说明 |
|---|---|
| `http://127.0.0.1:8000/` | 内置分析前端（拖拽上传、节点时间线、结果核对） |
| `http://127.0.0.1:8000/docs` | Swagger UI 接口文档（中文描述 + 自定义主题） |
| `http://127.0.0.1:8000/redoc` | ReDoc 接口文档（适合通读） |
| `http://127.0.0.1:8000/health` | 健康检查 |

调试需要热重载时：`DOCMIND_RELOAD=1 uv run python main.py`（reload 会中断在途分析任务，仅限调试）。

## 工作流

统一使用本地渲染链路，**上传即自动运行**，不提供手动重跑入口。

```text
render_document        PDF → PyMuPDF 直接光栅化；DOC/XLS → LibreOffice 转 PDF 后光栅化
                       （XLS 导出前自动展开隐藏行列、修正合并单元格行高；近空白页过滤）
  ↓
read_images_with_vlm   VLM 直读页面图片，逐字转写为视觉理解文档
  ↓
extract_candidates     按 PoOrderExtraction Schema 结构化抽取 12 字段候选
  ↓
build_result           港口归一化为三字码；发货人/收货人剔除中文对照名；其余字段原样保留
```

渲染降级链（XLS）：LibreOffice UNO 修正导出 → LibreOffice CLI 直接转换 → 纯 Python
合成表格图（xlrd + Pillow，零 LibreOffice 依赖）。`render_meta.json` 与
`task_status.json` 的 `converter` 字段记录实际使用的路径：`pymupdf` /
`libreoffice_uno` / `libreoffice` / `synthetic`。

模型输出即最终值：只做 Schema 结构校验与置信度阈值判定，不做标准化改写、字段规则过滤
或冲突消解，缺失或低置信度一律交给人工审核。**唯一例外**是始发港 / 目的港——会按港口
主数据归一化为三字码，归一化无法定论时把字段置空并转人工核对（原文与候选保留在
`field_meta` 中），绝不采信模型自创的码。

## 结果核对与提交

分析完成后弹出核对弹窗（左侧原件预览、右侧字段表单）：

- **字段联动原文**：点击字段即在左侧原件上高亮其位置（坐标由服务端定位后随结果下发，
  定位不到时标注「未定位」）；
- **输入即下拉**：委托客户 / 始发港 / 目的港的候选来自主数据缓存。手输内容**只用于搜索、
  不会写入表单值**，值必须由下拉选中产生，未选中就离开输入框会自动清空；若载入值在主
  数据中匹配不到，输入框以琥珀色边框提示待确认，但与原文的对应关系不会丢；
- **提交前校验只在本地**：校验 8 个必填项、日期格式（真实日历校验）与派生的「预计运费
  总额」，不做远程校验；
- **真实提交由调用方接入**：本服务只负责产出结构化结果，不代提交。

## 输出字段

固定 12 字段，不区分进口 / 出口 / 国内场景，必填 8 个在前、选填 4 个在后（缺失填 `null`）：

| 字段 | 必填 | 说明 |
|---|---|---|
| `sfg` | ✅ | 始发港 |
| `mdg` | ✅ | 目的港 |
| `ybpiece` | ✅ | 件数 |
| `ybweight` | ✅ | 重量（毛重） |
| `ybvolume` | ✅ | 体积 |
| `inwageallinprice` | ✅ | 运费（应收运费价格；COLLECT/PREPAID 条款视为无效） |
| `hbrq` | ✅ | 预计航班日期 / 船期 |
| `fid` | ✅ | 委托客户（托书内无此字段，由调用方 context 提供） |
| `shipper` / `consignee` | 选填 | 发货人 / 收货人（name / address / phone / email，地址为单个完整字符串） |
| `chinesepm` / `englishpm` | 选填 | 中文 / 英文品名 |

> 发货人 / 收货人地址合并为单个完整字符串：国外地址里的逗号与换行是同一地址的层级写法
> （街道、邮编、城市、国家），不拆成列表。

## 配置

通过 `.env` 或环境变量配置，完整项见 [`.env.example`](.env.example)。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 无（必填） | 缺失时服务启动即失败 |
| `DOCMIND_API_PREFIX` | `/docmind` | 接口统一前缀；改动需同步前端两个 `api.js` |
| `DOCMIND_HOST` / `DOCMIND_PORT` | `127.0.0.1` / `8000` | 监听地址与端口 |
| `DOCMIND_DATA_ROOT` | `data` | 运行产物根目录 |
| `DOCMIND_SOFFICE_PATH` | 空（自动探测） | LibreOffice `soffice` 路径 |
| `DOCMIND_RENDER_BLANK_PAGE_RATIO` | `0.005` | 近空白页过滤阈值（非白像素占比），设 `0` 关闭 |
| `DOCMIND_MAX_FILE_SIZE_BYTES` | `52428800`（50MB） | 单文件上限；校验在内容读入内存之后，调大会放大请求内存占用 |
| `DOCMIND_DATA_RETENTION_HOURS` | `24.0` | 任务产物保留时长；启动时与每小时清理一次，`0` 永久保留。建议大于单任务最长耗时，否则可能删掉在途任务的原件 |
| `DOCMIND_MAX_CONCURRENT_TASKS` | `20` | 同时存活任务数上限（兜底），超出保持 `queued` 排队 |
| `DOCMIND_LO_MAX_CONCURRENT` | `5` | 同时进行的 LibreOffice 转换数；每个转换拉起独立 soffice 进程（约 200~400MB），不建议超过 CPU 核数 |
| `DOCMIND_MODEL_MAX_CONCURRENT` | `8` | 同时进行的模型调用数；上游 429 或大面积超时时调小到 3~5 |
| `DOCMIND_RELOAD` | `0` | 热重载，仅调试用（会中断在途任务） |
| `DOCMIND_CONSOLE_LOG` | `0` | 控制台日志开关；默认只写 `logs/app.log`，写 stdout 管道可能阻塞事件循环 |

### 主数据（港口 / 委托客户）

委托客户与始发港 / 目的港的候选来自 poOrder `PublicWebApi` 的同一份主数据：服务端拉取后
落盘缓存，核对弹窗再从缓存做「输入即下拉」搜索（纯本地匹配，不调用模型）。
`DOCMIND_PORT_API_BASE` 留空时，港口归一化与两个下拉搜索整体停用，前端退化为手工填写。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DOCMIND_PORT_API_BASE` | 空（停用） | `PublicWebApi` 根地址，如 `http://<host>/PublicWebApi/` |
| `DOCMIND_CUSTOMER_API_BASE` | 空 | 委托客户主数据地址；留空回退用 `DOCMIND_PORT_API_BASE` |
| `DOCMIND_PORT_CACHE_TTL_HOURS` | `168.0` | 港口主数据缓存有效期；重拉会连带清空归一化结论缓存 |
| `DOCMIND_CUSTOMER_CACHE_TTL_HOURS` | `24.0` | 委托客户缓存有效期，过期后按 `timestamp` 水位增量更新 |
| `DOCMIND_PORT_MODEL_TIMEOUT_SECONDS` | `8.0` | 港口识别模型硬超时；超时即放弃归一化、转人工审核 |

### 并发容量

| 场景 | 表现 |
|---|---|
| 1 人上传 1~5 份 | 立即进入流水线，无排队 |
| 3 人同时各上传 5 份（15 个任务） | 全部进入执行；模型阶段分两批，约 4~6 分钟出完 |
| 超过 `DOCMIND_MAX_CONCURRENT_TASKS` | 超出部分保持 `queued`，等前序任务结束后自动补位 |

吞吐受 `DOCMIND_MODEL_MAX_CONCURRENT` 与上游响应耗时支配：单任务模型阶段约
「VLM 转写 + 字段抽取」（thinking 开启时约 120s），据此 **8 并发 ≈ 240 份/小时**。
LibreOffice 只处理 doc/docx/xls/xlsx（单页约 5~15s），PDF 走 PyMuPDF 直接光栅化，
一般不是瓶颈。真实容量以服务器上的节点 `duration_ms` 实测为准。

## API 概览

接口统一前缀 `/docmind`。修改 `DOCMIND_API_PREFIX` 时需同步前端
`app/static/api.js` 与 `app/static/result-dialog/api.js` 的接口根路径。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/docmind/analysis/tasks` | 上传托书创建任务（multipart，默认自动开始分析；`request_id` 幂等） |
| `GET` | `/docmind/analysis/files` | 任务文件列表 |
| `GET` | `/docmind/analysis/customers?keyword=` | 委托客户候选搜索（`enabled=false` 表示未配置主数据） |
| `GET` | `/docmind/analysis/ports?keyword=` | 港口候选搜索（三字码 / 英文名） |
| `GET` | `/docmind/analysis/tasks/{id}` | 任务状态与进度 |
| `GET` | `/docmind/analysis/tasks/{id}/events` | SSE 实时节点事件（含历史回放，任务结束后自动关闭） |
| `GET` | `/docmind/analysis/tasks/{id}/events/history` | 已落盘的全部节点事件（回看已完成任务） |
| `POST` | `/docmind/analysis/tasks/{id}/pause` | 暂停在途任务（已产出节点保留，可继续） |
| `POST` | `/docmind/analysis/tasks/{id}/resume` | 从暂停处继续（不重跑已完成节点） |
| `POST` | `/docmind/analysis/tasks/{id}/cancel` | 取消在途任务（不可恢复，重跑需重新上传） |
| `GET` | `/docmind/analysis/tasks/{id}/result` | 分析结果 JSON |
| `GET` | `/docmind/analysis/tasks/{id}/pages/{page}` | 指定页的渲染图片（核对弹窗的原件预览） |
| `GET` | `/health` | 健康检查（不带 `/docmind` 前缀） |

字段级说明与响应结构以 `/docs` 为准。

### 结果结构示例

```json
{
  "task_id": "task_...",
  "schema_version": "po_order.v1",
  "result": {
    "sfg": "SZX",
    "mdg": "MEX",
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
  "overall_confidence": 0.926,
  "review_fields": ["fid", "hbrq", "inwageallinprice"],
  "field_meta": {
    "sfg": {
      "value": "SZX",
      "status": "normalized",
      "confidence": 0.98,
      "evidence": [
        { "quote": "Airport of Departure 起运港/航空站/始发地机场" },
        { "quote": "港口主数据：SZX SHENZHEN（原文：SZX，来源：code）" }
      ],
      "locations": [{ "target": "sfg", "page": 1, "bbox": [0.2349, 0.1313, 0.0255, 0.0104] }],
      "raw_value": "SZX",
      "candidates": [{ "three_code": "SZX", "english_name": "SHENZHEN", "country_code": "CN" }]
    },
    "hbrq": {
      "value": null,
      "status": "missing",
      "confidence": 0.0,
      "evidence": [],
      "locations": [],
      "raw_value": null,
      "candidates": []
    }
  },
  "validation": { "is_valid": true }
}
```

- `result`：提交用的键值对。始发港 / 目的港是**归一化后的三字码**，归一化未能定论时为
  `null`，原文留在 `field_meta` 的 `raw_value`；
- `field_meta[字段]`：字段级元数据。`status` 取值 `confirmed` / `normalized` / `conflict` /
  `missing` / `invalid` / `needs_review` / `not_applicable`；`locations` 是归一化坐标
  `[x, y, w, h]`（0~1，与图片分辨率无关），供核对弹窗在原件上高亮；`candidates` 是港口
  归一化未定论时的候选项；
- `review_fields`：需人工复核的字段。`overall_status` 规则：必填字段缺失或低于置信度
  阈值 → `needs_review`，全部就绪 → `ready`，分析整体失败 → `failed`。

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
└─ reference_cache/                        外部主数据本地缓存（按各自 TTL 清理）
     ├─ hbinfo.json                        港口主数据
     ├─ port_outcomes.json                 港口归一化结论（主数据更新后整体失效）
     └─ customers.json                     委托客户主数据
```

> 上传原件存进各任务自己的目录而非全局目录：托书模板常出现同名文件（如 `托书.xls`），
> 全局存放会被后来的上传覆盖，而任务是在后台才读取源文件，会导致分析到别人的文档且不报错。

## 项目结构

```text
main.py            服务启动入口（读取 DOCMIND_HOST/PORT/RELOAD）
app/
  api/             FastAPI 路由（薄层：校验 → 调 Service）
  service/         业务编排 analysis_service.py
                   customer_service.py / port_normalization_service.py 主数据缓存与候选搜索
    requirements/  输出字段清单与必填判定、context 字段回填
  workflow/        LangGraph 工作流（4 节点、事件发布、统一节点执行器）
    nodes/         render_document / read_images_with_vlm / extract_candidates / build_result
  agent/           deepseek_extractor.py 结构化抽取 + free-form 回退、VLM 调用
                   port_normalizer.py 港口消歧 / 补全
  collector/       document_renderer.py 本地文档渲染（PDF/DOC/XLS → 页面图片）
                   lo_xls_height_fix.py LibreOffice UNO 脚本（隐藏行列展开、合并单元格行高修正）
                   port_reference_index.py / customer_reference_index.py 主数据索引与候选搜索
                   evidence_locator.py 字段值的原文坐标定位
  schemas/         Pydantic 模型（po_order 字段目录、analysis 候选/结果、file 上传）
  storage/         本地文件存储（data/ 三目录契约）
  prompts/         提示词文件（视觉理解、字段抽取、港口消歧）
  static/          内置前端（拖拽上传、节点时间线、核对弹窗、历史回放）
deploy/
  nginx/           入口 nginx 配置
data/              运行时产物（不入库）
```

## 开发

```bash
uv run ruff check app    # lint
uv run mypy app          # 类型检查
uv run pytest            # 测试
```

## 部署

面向 Linux 服务器的 Docker 部署见 **[`DEPLOY.md`](DEPLOY.md)**——前置条件、`.env` 配置、
构建启动、安全组放行、验收清单、日常运维与故障排查都在那里。

三点备忘：

- 镜像已内置 Python 3.12、LibreOffice（Writer + Calc）与中文字体，**服务器无需再装运行时依赖**；
- 对外由自带的 `gateway` 容器提供 HTTPS，不占用同机其它站点的 80 / 443；
- 任务在单进程内调度，**只跑一个副本、一个 uvicorn worker**——多实例会把彼此在途任务
  误判为中断并标记失败。

## 设计约定

- **文件读写统一走 `app/storage`**，路径基于 `DOCMIND_DATA_ROOT`；上传原件与任务产物都放在
  `parsed_documents/<task_id>/`，同名文件不会跨任务互相干扰。
- **渲染参数集中在 `app/collector/document_renderer.py`**：页面上限 10 页、200 DPI 光栅化，
  近空白页不送 VLM；VLM 输入上限 6 张图（每页整页图，首页额外附上下两半放大图，12% 重叠）。
- **XLS 渲染前**由 LibreOffice UNO 脚本（`lo_xls_height_fix.py`）展开隐藏行列、修正合并单元格
  行高，避免合并区域内容被裁切。
- **提示词只存在于 `app/prompts/`**，不在代码中内联。
- **依赖方向单向**：API → Service → Storage / Collector；Workflow 节点通过 `WorkflowHandlers`
  回调 Service 方法，节点本身不直接触碰存储。
- **主数据统一经 `app/service/*_service.py`** 读取本地缓存（TTL 过期后按水位增量更新）；
  核对弹窗的下拉候选复用同一份缓存，候选搜索纯本地、不调用模型。
