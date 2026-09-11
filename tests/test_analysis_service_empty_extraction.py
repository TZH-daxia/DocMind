"""服务层抽取守卫测试：VLM 有内容却抽不出字段时必须失败，不产出全空结果。"""

from pathlib import Path
from typing import Any

import pytest

from app.agent.deepseek_extractor import EmptyExtractionError
from app.config import Settings
from app.schemas.po_order import PO_ORDER_KEYS
from app.service.analysis_service import AnalysisService

# 像托书的内容：用于验证"文件类型没问题、只是抽不出字段"那条路径；
# 类型判定已提前拦下非托书文件，这里的样本必须带托书特征
BOOKING_LIKE_CONTENT = (
    "Shipper: ACME TRADING LTD\nConsignee: BASEL LOGISTICS GMBH\n"
    "Airport of Departure: SHANGHAI\nAirport of Destination: BASEL\n"
    "件数 No of Packages: 12\nGross Weight: 320 kg\nCBM: 1.8\n"
) * 3


class StubExtractor:
    """按配置抛出异常或返回空候选的抽取器替身。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def extract(
        self,
        system_prompt: str,
        vlm_image_content: str,
        context: dict[str, Any],
    ) -> list[Any]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return []


def make_service(tmp_path: Path) -> AnalysisService:
    return AnalysisService(Settings(DOCMIND_DATA_ROOT=tmp_path, deepseek_api_key="test-key"))


def make_state(vlm_content: str) -> dict[str, Any]:
    return {
        "task_id": "task_test_empty",
        "source_name": "托书.pdf",
        "context": {},
        "vlm_image_content": vlm_content,
    }


async def test_empty_extraction_fails_when_vlm_content_exists(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.deepseek = StubExtractor(EmptyExtractionError("EMPTY_EXTRACTION"))  # type: ignore[assignment]

    with pytest.raises(EmptyExtractionError):
        await service.extract_candidates(make_state(BOOKING_LIKE_CONTENT))


async def test_empty_extraction_kept_when_vlm_content_missing(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.deepseek = StubExtractor(EmptyExtractionError("EMPTY_EXTRACTION"))  # type: ignore[assignment]

    candidates = await service.extract_candidates(make_state(""))

    assert [item["field_key"] for item in candidates] == list(PO_ORDER_KEYS)
    assert all(item["status"] == "missing" for item in candidates)
