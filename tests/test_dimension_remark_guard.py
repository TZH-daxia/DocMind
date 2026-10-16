"""预报尺寸备注（ybvolumeremark）判定测试：只有单个「长*宽*高」算正常。

样例来自现场托书：`尺寸： 30*50*50`（标准）、`38*27*25CM/9`（多一段件数）、
表头「尺寸(长*宽*高)」下多行尺寸（多个物品）。
"""

from pathlib import Path

import pytest

from app.collector.dimension_remark_guard import is_standard_dimension_remark
from app.config import Settings
from app.schemas.analysis import Evidence, FieldMetadata
from app.service.analysis_service import (
    AnalysisService,
    _apply_dimension_remark_guard,
)

# ---------- 判定规则 ----------


@pytest.mark.parametrize(
    "text",
    [
        "30*50*50",
        "30 * 50 * 50",
        "30x50x50",
        "30X50X50",
        "30×50×50",
        "60*40*40cm",
        "60*40*40CM",
        "尺寸： 30*50*50",
        "尺寸:30*50*50",
        "size: 30*50*50",
        "长30*宽50*高50",
        "L30*W50*H50",
        "30.5*50*50cm",
        "30,5*50*50",  # 逗号当小数点（欧洲写法）
    ],
)
def test_standard_forms_are_accepted(text: str) -> None:
    assert is_standard_dimension_remark(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "30*50*50*4",  # 多一段件数
        "38*27*25CM/9",  # 第 4 段是件数（现场样例）
        "60*40*40cm\n60*40*30cm",  # 多条尺寸（现场样例）
        "60*40*40cm\r\n60*40*30cm",  # 同上，CRLF
        "30cm 50cm 50cm",  # 非相乘写法
        "30,50,50",  # 逗号分隔
        "30*50",  # 只有两段
        "200*300",  # 两段（体积类写法）
        "30*50*50 60*40*40",  # 多个尺寸挤在一行
        "1*1*1*1*1",  # 五段
        "尺寸：30cm 50cm 50cm",  # 带标签的非相乘写法
        "abc",
        "30*50*50 件数4",
    ],
)
def test_abnormal_forms_need_review(text: str) -> None:
    assert is_standard_dimension_remark(text) is False


@pytest.mark.parametrize("text", [None, "", "   "])
def test_empty_value_is_not_standard(text) -> None:
    """空值不算标准，但调用方按"字段缺失"处理，不应转待审核（见下面的接线测试）。"""

    assert is_standard_dimension_remark(text) is False


# ---------- build_result 接线 ----------


def make_meta(value, status: str = "confirmed") -> dict[str, FieldMetadata]:
    return {
        "ybvolumeremark": FieldMetadata(
            value=value,
            status=status,
            confidence=0.98,
            evidence=[Evidence(quote="尺寸： 30*50*50")],
        )
    }


def test_non_standard_remark_is_downgraded_to_review() -> None:
    field_meta = make_meta("38*27*25CM/9")
    review_fields: set[str] = set()

    changed = _apply_dimension_remark_guard(field_meta, review_fields)

    assert changed is True
    assert field_meta["ybvolumeremark"].status == "needs_review"
    assert "ybvolumeremark" in review_fields
    # 值保留原文，操作员能看到并确认；证据不受影响
    assert field_meta["ybvolumeremark"].value == "38*27*25CM/9"
    assert field_meta["ybvolumeremark"].evidence[0].quote == "尺寸： 30*50*50"
    assert field_meta["ybvolumeremark"].confidence == 0.98


def test_standard_remark_keeps_its_status() -> None:
    field_meta = make_meta("30*50*50")
    review_fields: set[str] = set()

    changed = _apply_dimension_remark_guard(field_meta, review_fields)

    assert changed is False
    assert field_meta["ybvolumeremark"].status == "confirmed"
    assert review_fields == set()


def test_missing_remark_is_not_forced_to_review() -> None:
    """托书里本来就没有尺寸：保持 missing，不要因为"不是标准写法"而进待审核。"""

    field_meta = make_meta(None, status="missing")
    review_fields: set[str] = set()

    changed = _apply_dimension_remark_guard(field_meta, review_fields)

    assert changed is False
    assert field_meta["ybvolumeremark"].status == "missing"
    assert review_fields == set()


def test_absent_field_is_ignored() -> None:
    field_meta: dict[str, FieldMetadata] = {}
    review_fields: set[str] = set()

    assert _apply_dimension_remark_guard(field_meta, review_fields) is False
    assert review_fields == set()


async def test_build_result_marks_non_standard_remark_for_review(tmp_path: Path) -> None:
    """接线：build_result 会把非标准尺寸写法转成待审核，值仍保留原文。"""

    service = AnalysisService(
        Settings(DOCMIND_DATA_ROOT=tmp_path, deepseek_api_key="test-key")
    )
    task_id = "dimension-remark"
    service.file_store.write_json_atomic(
        service.file_store.task_status_path(task_id),
        {"task_id": task_id, "status": "running"},
    )

    output = await service.build_result(
        {
            "task_id": task_id,
            "schema_version": "po_order.v1",
            "context": {"fid": "customer-1"},
            "candidates": [
                {
                    "field_key": "ybvolumeremark",
                    "value": "38*27*25CM/9",
                    "status": "confirmed",
                    "confidence": 0.98,
                    "evidence": [{"quote": "dims (cm): 38*27*25CM/9"}],
                }
            ],
        }
    )

    assert output["result"]["ybvolumeremark"] == "38*27*25CM/9"
    assert output["field_meta"]["ybvolumeremark"]["status"] == "needs_review"
    assert "ybvolumeremark" in output["review_fields"]
    assert output["overall_status"] == "needs_review"
