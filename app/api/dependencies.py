from functools import lru_cache

from app.config import get_settings
from app.service.analysis_service import AnalysisService


@lru_cache
def get_analysis_service() -> AnalysisService:
    """返回进程级分析服务。"""

    return AnalysisService(get_settings())
