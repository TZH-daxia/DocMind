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
    api_prefix: str = "/api/v1"
    app_host: str = Field(default="127.0.0.1", validation_alias="DOCMIND_HOST")
    app_port: int = Field(default=8001, validation_alias="DOCMIND_PORT")

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
    deepseek_model: str = "deepseek-v4-flash-vision-exp"
    deepseek_temperature: float = 0.0
    deepseek_timeout_seconds: float = 240.0
    deepseek_max_retries: int = 1
    # 字段抽取是否开启 thinking：开启后候选更稳，但单次耗时 8s → 95s 且随推理
    # token 数大幅波动
    deepseek_thinking: bool = True
    # 图片识别（read_images_with_vlm）是否开启 thinking：关闭后逐字转写会漏字、
    # 串语言（如英文被改写成德文），因此与抽取保持一致默认开启
    deepseek_vision_thinking: bool = False

    docmind_data_root: Path = Field(default=Path("data"), validation_alias="DOCMIND_DATA_ROOT")
    # 港口主数据接口（poOrder PublicWebApi）根地址，如 http://<host>/PublicWebApi/；
    # 为空时禁用始发港/到达港三字码归一化
    port_api_base: str = Field(default="", validation_alias="DOCMIND_PORT_API_BASE")
    # 港口主数据本地缓存有效期（小时），过期后重新拉取
    port_cache_ttl_hours: float = 24.0
    max_file_size_bytes: int = 200 * 1024 * 1024
    allowed_extensions: tuple[str, ...] = (".doc", ".docx", ".xls", ".xlsx", ".pdf")
    # 同时存活的工作流任务数上限（超出的快照保持 queued 排队）：只做兜底，防止
    # 一次堆积几十个任务把内存和文件句柄吃满；真正的资源节流交给下面两个阶段闸门
    max_concurrent_tasks: int = Field(
        default=12,
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
    # 不占 CPU，默认按"单个用户一次上传 5 份"给到 5，使 5 个任务能同时在等模型；
    # 若上游返回 429 或大面积超时，调小该值即可
    model_max_concurrent: int = Field(
        default=5,
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


@lru_cache
def get_settings() -> Settings:
    """返回缓存的应用配置。"""

    return Settings()
