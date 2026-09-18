"""文档类型守卫测试：传错文件时必须在调用模型之前拦下，并给出可读提示。"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.agent.deepseek_extractor import EmptyExtractionError
from app.collector.document_type_guard import (
    MIN_CONTENT_CHARS,
    MISMATCH_MESSAGE,
    DocumentTypeMismatchError,
    build_content_hint,
    detect_document_type,
)
from app.config import Settings
from app.service.analysis_service import (
    FAILURE_EVENT_MESSAGES,
    FAILURE_USER_MESSAGES,
    AnalysisService,
)

# 真实失败样本：任务 task_20260911_131239_11_dc7d1c61 上传的 Python 项目规范文档
SPEC_DOCUMENT_CONTENT = """
### 整页版面描述（对应11_page_001.png）
该页为纯文本项目规范说明页，无表格、无货运相关字段，内容为无序列表项，所有条目均带项目符号。
- Python 3.11+。
- 使用 uv 或 Poetry 锁定依赖和版本。
- 采用 src/ 目录、分层模块和类型检查。
- 使用 ruff、mypy、pytest、pre-commit 和 CI。
- API DTO、领域模型、持久化模型分离。
- 所有外部调用设置连接、读取和总超时。
- 重试必须有上限、退避、熔断和死信。
- 大文件只通过对象存储 key 或短期签名 URL 传递。
"""

BOOKING_CONTENT = """
### 整页版面描述（对应空运托书_page_001.png）
Shipper: Xinchang Pace Bearing Parts Co., Ltd
Consignee: SKF GmbH----SKF Logistics Services
Airport of Departure: SHANGHAI
Airport of Destination: FRANKFURT
件数 No of Packages: 169
实际毛重 Gross Weight: 3109 kg
总体积 CBM: 15.2
品名 Description of Goods: DRESS
"""


class StubExtractor:
    """记录调用次数的抽取器替身。"""

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
    return AnalysisService(
        Settings(DOCMIND_DATA_ROOT=tmp_path, deepseek_api_key="test-key")
    )


def make_state(vlm_content: str) -> dict[str, Any]:
    return {
        "task_id": "task_test_document_type",
        "source_name": "11.doc",
        "context": {},
        "vlm_image_content": vlm_content,
    }


def test_spec_document_is_mismatch() -> None:
    """本次真实失败样本（Python 规范说明）应判定为不是托书。"""

    verdict = detect_document_type(SPEC_DOCUMENT_CONTENT)

    assert verdict.kind == "mismatch"
    assert verdict.blocks_extraction is True
    assert verdict.hit_groups == ()


def test_booking_document_is_accepted() -> None:
    verdict = detect_document_type(BOOKING_CONTENT)

    assert verdict.kind == "booking"
    assert verdict.blocks_extraction is False
    assert "参与人" in verdict.hit_groups
    assert "起讫港" in verdict.hit_groups


def test_single_feature_group_is_not_blocked() -> None:
    """只命中 1 组时可能是别的单据（机票行程单也有 flight），一律放行。"""

    content = "Flight MU587 上海浦东到纽约肯尼迪 2026-08-25 起飞时间 11:20 " * 8

    verdict = detect_document_type(content)

    assert verdict.kind == "uncertain"
    assert verdict.blocks_extraction is False
    assert verdict.hit_groups == ("单据航班",)


def test_short_content_is_undetermined() -> None:
    """内容太短（空白件/扫描件）无从判断，保持原流程处理。"""

    content = "该页为扫描图片，未能识别文字"

    verdict = detect_document_type(content)

    assert len(content) < MIN_CONTENT_CHARS
    assert verdict.kind == "undetermined"
    assert verdict.blocks_extraction is False


def test_content_hint_strips_markup() -> None:
    hint = build_content_hint(SPEC_DOCUMENT_CONTENT)

    assert hint.startswith("整页版面描述")
    assert "#" not in hint
    assert "- " not in hint
    assert hint.endswith("…")
    assert len(hint) == 81


async def test_extract_candidates_blocks_before_model_call(tmp_path: Path) -> None:
    """不像托书时直接终止：既给出类型提示，也不浪费一次模型调用。"""

    service = make_service(tmp_path)
    stub = StubExtractor()
    service.deepseek = stub  # type: ignore[assignment]

    with pytest.raises(DocumentTypeMismatchError) as excinfo:
        await service.extract_candidates(make_state(SPEC_DOCUMENT_CONTENT))

    assert stub.calls == 0
    assert "不像空运托书" in str(excinfo.value)
    assert excinfo.value.hint


async def test_extract_candidates_still_reports_empty_extraction(tmp_path: Path) -> None:
    """像托书却抽不出字段：仍归因为抽取失败，不是文件类型问题。"""

    service = make_service(tmp_path)
    service.deepseek = StubExtractor(EmptyExtractionError("EMPTY_EXTRACTION"))  # type: ignore[assignment]

    with pytest.raises(EmptyExtractionError):
        await service.extract_candidates(make_state(BOOKING_CONTENT))


def test_list_tasks_exposes_error_code(tmp_path: Path) -> None:
    """失败原因随文件列表下发，历史失败任务也能显示准确提示。"""

    service = make_service(tmp_path)
    task_id = "task_20260911_131239_11_dc7d1c61"
    task_dir = tmp_path / "parsed_documents" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "task_status.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "original_name": "11.doc",
                "status": "failed",
                "progress": 100,
                "current_stage": "failed",
                "error": {
                    "code": "DOCUMENT_TYPE_MISMATCH",
                    "message": "该文件不像空运托书",
                    "hint": "整页版面描述 该页为纯文本项目规范说明页",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    items = service.list_tasks()

    assert items[0]["error_code"] == "DOCUMENT_TYPE_MISMATCH"
    assert items[0]["error_message"] == "该文件不像空运托书"


def test_list_tasks_tolerates_missing_error(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    task_dir = tmp_path / "parsed_documents" / "task_ok"
    task_dir.mkdir(parents=True)
    (task_dir / "task_status.json").write_text(
        json.dumps({"task_id": "task_ok", "status": "ready", "progress": 100}),
        encoding="utf-8",
    )

    items = service.list_tasks()

    assert items[0]["error_code"] is None
    assert items[0]["error_message"] is None


def test_not_booking_error_messages_are_unified() -> None:
    """两种错误码对用户是同一件事，提示文案必须一致。

    守卫拦下（DOCUMENT_TYPE_MISMATCH）与守卫放过但模型抽不出字段
    （EMPTY_EXTRACTION）都是"上传的不是托书"，界面上不该出现两种说法；
    技术区分只保留在 error.code 与 error.detail 里。
    """

    assert (
        FAILURE_USER_MESSAGES["DOCUMENT_TYPE_MISMATCH"]
        == FAILURE_USER_MESSAGES["EMPTY_EXTRACTION"]
        == MISMATCH_MESSAGE
    )
    assert (
        FAILURE_EVENT_MESSAGES["DOCUMENT_TYPE_MISMATCH"]
        == FAILURE_EVENT_MESSAGES["EMPTY_EXTRACTION"]
    )
