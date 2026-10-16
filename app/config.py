from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从环境变量加载应用配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "DocMind"
    # 所有业务接口的统一前缀（改这里必须同步改前端 app/static/api.js 与
    # app/static/result-dialog/api.js 里的接口根路径）
    api_prefix: str = Field(default="/docmind", validation_alias="DOCMIND_API_PREFIX")
    app_host: str = Field(default="127.0.0.1", validation_alias="DOCMIND_HOST")
    app_port: int = Field(default=8000, validation_alias="DOCMIND_PORT")

    system_log_file: Path = Field(
        default=Path("logs/app.log"),
        validation_alias="DOCMIND_SYSTEM_LOG_FILE",
    )
    system_log_level: str = Field(
        default="INFO",
        validation_alias="DOCMIND_SYSTEM_LOG_LEVEL",
    )
    system_log_retention_days: int = Field(
        default=14,
        ge=1,
        validation_alias="DOCMIND_SYSTEM_LOG_RETENTION_DAYS",
    )
    # 控制台日志开关：宿主终端（尤其 IDE 集成终端）不及时消费输出时，
    # 写 stdout 会阻塞事件循环导致服务整体冻结，因此默认只写文件日志
    system_log_console: bool = Field(
        default=False,
        validation_alias="DOCMIND_CONSOLE_LOG",
    )

    review_confidence_threshold: float = 0.6

    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    # DeepSeek V4.1 Flash 的规范模型 ID。旧名 deepseek-v4-flash-vision-exp
    # 目前仍被兼容路由到 V4.1 Flash，但属过渡安排，随时可能下线
    deepseek_model: str = "deepseek-flash"
    deepseek_temperature: float = 0.0
    deepseek_timeout_seconds: float = 240.0
    # 连接建立（TCP/TLS 握手）的超时（秒）：与读响应分开算，握手 10s 还没完成
    # 基本就是网络断了，不必陪等满整个调用上限；正常连接建立远小于 1s
    deepseek_connect_timeout_seconds: float = 10.0
    deepseek_max_retries: int = 1
    # 字段抽取是否开启 thinking：开启后候选更稳，但单次耗时 8s → 95s 且随推理
    # token 数大幅波动
    deepseek_thinking: bool = True
    # 图片识别（read_images_with_vlm）是否开启 thinking：关闭后逐字转写会漏字、
    # 串语言（如英文被改写成德文），因此与抽取保持一致默认开启
    deepseek_vision_thinking: bool = False

    # —— 每次模型调用的超时与重试（统一走 app/agent/model_call.call_with_retry）——
    # 单次调用的**墙钟**硬超时（秒）。openai SDK 的 timeout 只是单次 HTTP 的
    # connect/read 超时，上游异常时只要连接上还有零星流量就永不触发，调用会无限
    # 挂起（表现为任务一直"正在解析中"），所以这里用 wait_for 兜底。
    # 阈值按历史节点耗时定（p90 / max）：VLM 9.4s / 26.4s、抽取 8.7s / 84.1s，
    # 留 2 倍以上余量；抽取开了 thinking（见上）波动大，因此单独放宽。
    model_vision_timeout_seconds: float = Field(
        default=60.0, validation_alias="DOCMIND_MODEL_VISION_TIMEOUT_SECONDS"
    )
    model_extract_timeout_seconds: float = Field(
        default=180.0, validation_alias="DOCMIND_MODEL_EXTRACT_TIMEOUT_SECONDS"
    )
    # 每次调用的重试次数（总尝试次数 = 1 + 本值）。重试耗尽即判任务失败并回给
    # 前端 MODEL_TIMEOUT 文案，不再无限等待
    model_call_retries: int = Field(
        default=2, validation_alias="DOCMIND_MODEL_CALL_RETRIES"
    )

    # —— 兜底模型（主模型整体不可用时接管；不配 api_key 则整条兜底链路不生效）——
    # 当前接的是阿里云百炼（通义千问）的 OpenAI 兼容接口：
    #   base_url = https://dashscope.aliyuncs.com/compatible-mode/v1
    # 兜底不是"备用副本"，而是换一家上游：主模型账号限流/欠费/宕机时照样能出结果
    fallback_api_key: str | None = Field(
        default=None, validation_alias="DOCMIND_FALLBACK_API_KEY"
    )
    fallback_name: str = Field(default="qwen", validation_alias="DOCMIND_FALLBACK_NAME")
    fallback_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="DOCMIND_FALLBACK_BASE_URL",
    )
    # 字段抽取用的兜底模型（结构化输出 + evidence，错值代价最高的一环）
    fallback_model: str = Field(
        default="qwen3.8-flash", validation_alias="DOCMIND_FALLBACK_MODEL"
    )
    # 图片识别用的兜底模型：留空回退 fallback_model。量大、任务简单时可单独降档
    fallback_vision_model: str = Field(
        default="", validation_alias="DOCMIND_FALLBACK_VISION_MODEL"
    )
    fallback_temperature: float = Field(
        default=0.0, validation_alias="DOCMIND_FALLBACK_TEMPERATURE"
    )
    fallback_timeout_seconds: float = Field(
        default=240.0, validation_alias="DOCMIND_FALLBACK_TIMEOUT_SECONDS"
    )
    fallback_connect_timeout_seconds: float = Field(
        default=10.0, validation_alias="DOCMIND_FALLBACK_CONNECT_TIMEOUT_SECONDS"
    )
    # 兜底 provider 自己的 SDK 重试与墙钟重试次数。默认都不重试（只试一次）：
    # 兜底已经是最后一环，再叠一轮重试只会把失败耗时翻倍
    fallback_max_retries: int = Field(
        default=0, validation_alias="DOCMIND_FALLBACK_MAX_RETRIES"
    )
    fallback_call_retries: int = Field(
        default=0, validation_alias="DOCMIND_FALLBACK_CALL_RETRIES"
    )
    # 结构化输出方式：**实测 function_calling 比 json_schema 稳**（见 tools/
    # eval_fallback_model.py 的验收报告）：千问的 json_schema(strict) 面对本项目
    # 13 字段嵌套 schema 时会大面积漏字段（只填 shipper/consignee），
    # function_calling 能完整抽出全部字段。留空则直接走自由文本 + JSON 解析
    fallback_structured_method: str = Field(
        default="function_calling",
        validation_alias="DOCMIND_FALLBACK_STRUCTURED_METHOD",
    )
    # 千问的思考开关（enable_thinking）：默认显式关闭。实测开启（或不传、走平台
    # 默认值）时，qwen3.8-flash 的结构化抽取耗时会翻倍且字段值出现整体错位
    fallback_thinking: bool = Field(
        default=False, validation_alias="DOCMIND_FALLBACK_THINKING"
    )

    # —— provider 熔断：主模型连续失败后直接跳过，不再让每个任务陪跑一遍它的重试 ——
    # 连续失败达到阈值即熔断；冷却期结束放行一次试探，成功即恢复
    model_failover_failure_threshold: int = Field(
        default=3,
        ge=1,
        validation_alias="DOCMIND_MODEL_FAILOVER_FAILURE_THRESHOLD",
    )
    model_failover_cooldown_seconds: float = Field(
        default=90.0,
        ge=0.0,
        validation_alias="DOCMIND_MODEL_FAILOVER_COOLDOWN_SECONDS",
    )

    docmind_data_root: Path = Field(default=Path("data"), validation_alias="DOCMIND_DATA_ROOT")
    # 港口主数据接口（poOrder PublicWebApi）根地址，如 http://<host>/PublicWebApi/；
    # 为空时禁用始发港/到达港三字码归一化
    port_api_base: str = Field(default="", validation_alias="DOCMIND_PORT_API_BASE")
    # 港口主数据本地缓存有效期（小时），过期后重新拉取；默认 7 天：
    # 主数据更新频率低，且每次重拉会连带清空归一化结论缓存（port_outcomes），
    # 拉得太勤会把"模型消歧结论"反复作废
    port_cache_ttl_hours: float = Field(
        default=168.0, validation_alias="DOCMIND_PORT_CACHE_TTL_HOURS"
    )
    # 港口识别模型调用的硬超时（秒）：本地匹配无法定论时才调用模型，
    # 超时即放弃归一化、字段转人工审核，避免把 build_result 拖成几十秒
    port_model_timeout_seconds: float = Field(
        default=8.0, validation_alias="DOCMIND_PORT_MODEL_TIMEOUT_SECONDS"
    )
    # 委托客户主数据接口（poOrder PublicWebApi /api/PubFCustom）根地址；
    # 与港口主数据是同一个服务，留空时回退使用 port_api_base，都为空则停用校验
    customer_api_base: str = Field(
        default="", validation_alias="DOCMIND_CUSTOMER_API_BASE"
    )
    # 委托客户主数据本地缓存有效期（小时），过期后按 timestamp 增量更新
    customer_cache_ttl_hours: float = Field(
        default=24.0, validation_alias="DOCMIND_CUSTOMER_CACHE_TTL_HOURS"
    )
    # 唯凯站点字典接口（poOrder PublicWebApi /api/PubTypeCode?groupid=101）根地址；
    # 与港口/客户主数据是同一个服务，留空时回退 port_api_base，都为空则停用站点候选
    site_api_base: str = Field(default="", validation_alias="DOCMIND_SITE_API_BASE")
    # 站点字典本地缓存有效期（小时）：字典是人工维护的静态数据，更新频率低
    site_cache_ttl_hours: float = Field(
        default=168.0, validation_alias="DOCMIND_SITE_CACHE_TTL_HOURS"
    )
    # 提交订单接口（poOrder api/ExHpoAxpline）根地址。该接口挂在 BoManagementWebApi 下
    # （不是公共主数据的 PublicWebApi，见 poOrder src/store/index.js:82），与它们同主机。
    # 留空时按 port_api_base 的应用名自动换成 BoManagementWebApi，本机 .env 只配了
    # PublicWebApi 也能开箱可用；配了则以本项为准
    order_api_base: str = Field(default="", validation_alias="DOCMIND_ORDER_API_BASE")
    # 是否允许真实下单。**默认关闭**：提交是写操作，一旦后端地址配到非开发环境就会
    # 在真实系统里建单，所以需要哪个环境能下单，就在该环境的 .env 里显式打开
    order_submit_enabled: bool = Field(
        default=False, validation_alias="DOCMIND_ORDER_SUBMIT_ENABLED"
    )
    # 是否允许从 URL 查询参数 `?ticket=` 读 poOrder 票据。**默认关闭**：票据是登录
    # 凭据，放进 URL 会跟着进浏览器历史、`Referer` 与网关访问日志；正常通道是请求头
    # （见 app/api/dependencies.py 的 current_ticket）。开发期同机调试时跨端口读不到
    # poOrder 的 storage，只能在 .env 里打开这一项用 `?ticket=` 兜底
    allow_url_ticket: bool = Field(
        default=False, validation_alias="DOCMIND_ALLOW_URL_TICKET"
    )
    # 委托项目主数据接口（poOrder PublicWebApi /api/PubCustom）根地址；
    # 与港口/客户主数据是同一个服务，留空时回退 port_api_base，都为空则停用项目候选
    project_api_base: str = Field(
        default="", validation_alias="DOCMIND_PROJECT_API_BASE"
    )
    # 项目主数据本地缓存有效期（小时）：比客户表短 —— poOrder 每次刷新页面都会重拉，
    # 且项目主数据变更后会主动刷新，说明这份数据变更比客户表频繁
    project_cache_ttl_hours: float = Field(
        default=12.0, validation_alias="DOCMIND_PROJECT_CACHE_TTL_HOURS"
    )
    # 用户设置模板接口（poOrder PublicWebApi /api/UserTemplet）根地址：订单新增页的
    # 默认站点/运输种类/服务方式就存在该接口的 `type=110` 记录里。与港口/客户/站点
    # 主数据同源，留空时回退 port_api_base，都为空则停用「用户默认设置」
    user_templet_api_base: str = Field(
        default="", validation_alias="DOCMIND_USER_TEMPLET_API_BASE"
    )
    # 用户默认设置的内存缓存有效期（分钟）：接口一次返回该用户的全部模板（实测 ~660KB），
    # 缓存能挡掉连续切任务的重复请求。但**别调太长**：在 poOrder 里改了默认站点后，
    # 缓存期内所有任务仍然拿旧值，表现为"有时生效有时无效"。默认 1 分钟：既够挡抖动，
    # 又几乎察觉不到延迟；0 表示不缓存
    user_defaults_cache_ttl_minutes: float = Field(
        default=1.0,
        ge=0.0,
        validation_alias="DOCMIND_USER_DEFAULTS_CACHE_TTL_MINUTES",
    )
    # 单文件大小上限：托书均为单页文档，50MB 已足够宽松；注意校验发生在
    # 上传内容读入内存之后，调大会同时放大单次请求的内存占用
    max_file_size_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1,
        validation_alias="DOCMIND_MAX_FILE_SIZE_BYTES",
    )
    allowed_extensions: tuple[str, ...] = (".doc", ".docx", ".xls", ".xlsx", ".pdf")
    # 同时存活的工作流任务数上限（超出的快照保持 queued 排队）：只做兜底，防止
    # 一次堆积几十个任务把内存和文件句柄吃满；真正的资源节流交给下面两个阶段闸门。
    # 20 可覆盖"3 人同时各上传 5 份（15 个任务）"全部进入流水线
    max_concurrent_tasks: int = Field(
        default=20,
        ge=1,
        validation_alias="DOCMIND_MAX_CONCURRENT_TASKS",
    )
    # 同时进行的 LibreOffice 转换上限：每个转换会拉起独立 soffice 进程
    # （XLS 还额外占用一个 UNO 端口）。单实例常驻内存约 200~400MB，默认 5 让
    # 一人一次上传的 5 份文件可以并行转换（峰值约 1.5GB）；机器内存吃紧或出现
    # 转换超时时调小到 2~3
    libreoffice_max_concurrent: int = Field(
        default=5,
        ge=1,
        validation_alias="DOCMIND_LO_MAX_CONCURRENT",
    )
    # 同时进行的模型调用上限（视觉识别 + 字段抽取）：这两步是长时间的网络等待，
    # 不占 CPU。默认 8 让"3 人各上传 5 份（15 个任务）"两批就能走完模型阶段；
    # 若上游返回 429 或大面积超时，调小（5 → 3）即可
    model_max_concurrent: int = Field(
        default=8,
        ge=1,
        validation_alias="DOCMIND_MODEL_MAX_CONCURRENT",
    )
    # LibreOffice soffice 可执行路径：doc/xls 转 PDF 的统一方案（跨平台，不依赖 Office）；
    # 留空时按 PATH 与常见安装位置自动探测
    libreoffice_path: str = Field(default="", validation_alias="DOCMIND_SOFFICE_PATH")
    # 近空白页过滤阈值：光栅化后非白像素占比低于该值的页面不送 VLM，设为 0 关闭过滤
    render_blank_page_ratio: float = Field(
        default=0.005,
        ge=0.0,
        le=1.0,
        validation_alias="DOCMIND_RENDER_BLANK_PAGE_RATIO",
    )
    # 任务产物保留时长（小时）：启动时与每小时清理一次，0 表示永久保留
    data_retention_hours: float = Field(
        default=24.0,
        ge=0.0,
        validation_alias="DOCMIND_DATA_RETENTION_HOURS",
    )
    # 允许跨域调用接口的前端来源（逗号分隔）：调用方是独立域名下的纯静态 SPA，
    # 浏览器直连本服务，不经过网关反代，因此必须显式放行来源。
    # 默认只放行本地前端 dev server，生产环境用
    # DOCMIND_CORS_ALLOW_ORIGINS 追加正式站点域名
    cors_allow_origins: str = Field(
        default="http://localhost:3005,http://127.0.0.1:3005",
        validation_alias="DOCMIND_CORS_ALLOW_ORIGINS",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """拆成来源列表；过滤空项，避免配置里多余逗号导致放行失败。"""

        return [
            item.strip() for item in self.cors_allow_origins.split(",") if item.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """返回缓存的应用配置。"""

    return Settings()
