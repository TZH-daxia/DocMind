"""参与人公司名去中文对照名测试：提示词之外的确定性兜底。"""

from app.schemas.analysis import Evidence, FieldMetadata
from app.service.analysis_service import (
    _strip_party_name_alias,
    _strip_party_name_aliases,
)


def test_full_width_bracket_alias_is_stripped() -> None:
    assert (
        _strip_party_name_alias(
            "Xinchang Pace Bearing Parts Co., Ltd（新昌沛斯轴承配件有限公司）"
        )
        == "Xinchang Pace Bearing Parts Co., Ltd"
    )


def test_half_width_bracket_alias_is_stripped() -> None:
    assert _strip_party_name_alias("SKF GmbH (德国)") == "SKF GmbH"
    assert (
        _strip_party_name_alias("ABC Logistics Co., Ltd(上海某某物流有限公司)")
        == "ABC Logistics Co., Ltd"
    )


def test_trailing_space_and_comma_before_alias_are_cleaned() -> None:
    assert _strip_party_name_alias("ABC Ltd, （某某有限公司）") == "ABC Ltd"


def test_chinese_only_name_is_kept() -> None:
    assert (
        _strip_party_name_alias("新昌沛斯轴承配件有限公司")
        == "新昌沛斯轴承配件有限公司"
    )
    # 中文名 + 括号里是拉丁字母：不是"英文名（中文名）"形式，保持原值
    assert (
        _strip_party_name_alias("新昌沛斯轴承配件有限公司（Xinchang Pace）")
        == "新昌沛斯轴承配件有限公司（Xinchang Pace）"
    )


def test_bracket_with_latin_or_digits_is_kept() -> None:
    assert _strip_party_name_alias("ABC Ltd（ABC 上海）") == "ABC Ltd（ABC 上海）"
    assert _strip_party_name_alias("ABC Ltd（上海 2 号库）") == "ABC Ltd（上海 2 号库）"


def test_bracket_in_the_middle_is_kept() -> None:
    assert (
        _strip_party_name_alias("ABC Logistics（上海）有限公司")
        == "ABC Logistics（上海）有限公司"
    )


def test_empty_name_is_handled() -> None:
    assert _strip_party_name_alias("") == ""
    assert _strip_party_name_alias(None) == ""  # type: ignore[arg-type]


def test_apply_strips_both_party_fields_and_keeps_evidence() -> None:
    shipper = FieldMetadata(
        value={
            "name": "Xinchang Pace Bearing Parts Co., Ltd（新昌沛斯轴承配件有限公司）"
        },
        status="confirmed",
        confidence=0.9,
        evidence=[
            Evidence(
                quote="Xinchang Pace Bearing Parts Co., Ltd（新昌沛斯轴承配件有限公司）"
            )
        ],
    )
    consignee = FieldMetadata(
        value={"name": "SKF GmbH（德国）", "address": "Uferstrasse 6, Germany"},
        status="confirmed",
        confidence=0.9,
    )
    result = {
        "shipper": dict(shipper.value),
        "consignee": dict(consignee.value),
        "chinesepm": "轴承配件",
    }
    field_meta = {"shipper": shipper, "consignee": consignee}

    _strip_party_name_aliases(result, field_meta)

    assert result["shipper"] == {"name": "Xinchang Pace Bearing Parts Co., Ltd"}
    assert result["consignee"] == {
        "name": "SKF GmbH",
        "address": "Uferstrasse 6, Germany",
    }
    assert field_meta["shipper"].value["name"] == "Xinchang Pace Bearing Parts Co., Ltd"
    assert field_meta["consignee"].value["name"] == "SKF GmbH"
    # 引用原文不改写，人工仍能在原件预览里看到完整写法
    assert field_meta["shipper"].evidence[0].quote == (
        "Xinchang Pace Bearing Parts Co., Ltd（新昌沛斯轴承配件有限公司）"
    )
    # 非对象字段与状态/置信度不受影响
    assert result["chinesepm"] == "轴承配件"
    assert field_meta["shipper"].status == "confirmed"
    assert field_meta["shipper"].confidence == 0.9


def test_apply_ignores_fields_without_name() -> None:
    meta = FieldMetadata(
        value={"address": "Uferstrasse 6（德国）"}, status="confirmed", confidence=0.9
    )
    result = {"consignee": dict(meta.value)}
    field_meta = {"consignee": meta}

    _strip_party_name_aliases(result, field_meta)

    assert result["consignee"] == {"address": "Uferstrasse 6（德国）"}
    assert field_meta["consignee"].value == {"address": "Uferstrasse 6（德国）"}
