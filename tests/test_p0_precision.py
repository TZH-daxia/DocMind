from app.schemas.analysis import Evidence, FieldCandidate, FieldMetadata
from app.schemas.po_order import PO_ORDER_KEYS
from app.service.conflict.conflict_resolver import ConflictResolver
from app.service.deterministic_extractor import extract_deterministic_candidates
from app.service.normalization.candidate_normalizer import CandidateNormalizer
from app.service.requirements.po_order_requirements import PoOrderRequirementService
from app.service.result_builder.result_builder import ResultBuilder
from app.service.validation.candidate_validator import CandidateValidator


def test_deterministic_extractor_keeps_total_volume_conflict() -> None:
    text = (
        "体积(CBM): 0.780CMB "
        "<table><tr><td>体积(CBM)</td></tr>"
        "<tr><td>1.2CBM</td></tr></table>"
    )

    candidates = extract_deterministic_candidates(text, "doc_001")
    volumes = {
        candidate.value
        for candidate in candidates
        if candidate.field_key == "ybvolume"
    }

    assert volumes == {0.78, 1.2}


def test_product_rules_and_normalizer_remove_item_and_region_prefix() -> None:
    text = (
        "<table><tr><td>货名规格及货号</td></tr>"
        "<tr><td>72048488 DE LAWN MOWER/手推式电动割草机"
        "海关代码:8433110000</td></tr></table>"
    )

    candidates = extract_deterministic_candidates(text, "doc_001")
    normalized = CandidateNormalizer().normalize(candidates)
    names = {
        candidate.value
        for candidate in normalized
        if candidate.field_key == "englishpm"
    }

    assert names == {"LAWN MOWER"}
    chinese_names = {
        candidate.value
        for candidate in normalized
        if candidate.field_key == "chinesepm"
    }
    assert chinese_names == {"手推式电动割草机"}


def test_normalizer_removes_code_label_from_model_chinese_name() -> None:
    candidate = FieldCandidate(
        field_key="chinesepm",
        value="手推式电动割草机海关代码",
        evidence=[Evidence(document_id="doc_001", quote="手推式电动割草机海关代码")],
    )

    normalized = CandidateNormalizer().normalize([candidate])

    assert normalized[0].value == "手推式电动割草机"


def test_required_and_optional_key_sets_are_static() -> None:
    """必填 8 个、选填 4 个，与运输方向无关。"""

    service = PoOrderRequirementService()

    for context in (
        {},
        {"opersystem": "出口", "opersystemdom": "空运"},
        {"opersystem": "进口"},
        {"opersystem": "国内"},
    ):
        assert service.required_field_keys(context) == list(
            PO_ORDER_KEYS[:8]
        )
        assert service.optional_field_keys(context) == list(
            PO_ORDER_KEYS[8:]
        )


def test_result_has_only_twelve_fields_in_required_first_order() -> None:
    field_meta = {
        key: FieldMetadata(
            value=value,
            status="normalized",
            confidence=0.99,
            evidence=[Evidence(document_id="doc_001", quote=str(value))],
            extraction_method="table",
        )
        for key, value in {
            "sfg": "SHANGHAI",
            "mdg": "FRANKFURT",
            "ybpiece": 1,
            "ybweight": 168,
            "ybvolume": 1.2,
            "inwageallinprice": 1200.5,
            "hbrq": "2026-09-28",
            "englishpm": "LAWN MOWER",
        }.items()
    }
    result = ResultBuilder().build(
        task_id="task_001",
        schema_version="po_order.v1",
        field_meta=field_meta,
        context={"fid": 1},
        overall_confidence=0.9,
    )

    assert list(result.result) == list(PO_ORDER_KEYS)
    assert result.result["fid"] == 1
    assert result.result["sfg"] == "SHANGHAI"
    assert result.result["englishpm"] == "LAWN MOWER"
    assert result.result["shipper"] is None
    assert result.validation.is_valid is True
    assert result.overall_status == "ready"


def test_missing_required_fields_fill_null_without_error() -> None:
    """必填缺失只填 null，不再报 error 拦截。"""

    result = ResultBuilder().build(
        task_id="task_missing",
        schema_version="po_order.v1",
        field_meta={},
        context={},
        overall_confidence=0.0,
    )

    assert result.result["sfg"] is None
    assert result.result["mdg"] is None
    assert result.result["fid"] is None
    assert not result.validation.errors
    assert result.validation.is_valid is True
    assert result.field_meta["sfg"].status == "missing"


def test_required_field_conflict_remains_error() -> None:
    field_meta = {
        "ybweight": FieldMetadata(
            value=None,
            status="conflict",
            confidence=0.9,
            evidence=[],
            extraction_method="regex",
            validation_errors=["存在两个毛重"],
        )
    }
    result = ResultBuilder().build(
        task_id="task_002",
        schema_version="po_order.v1",
        field_meta=field_meta,
        context={},
        overall_confidence=0.0,
    )

    assert result.validation.is_valid is False
    assert any(item["field_key"] == "ybweight" for item in result.validation.errors)


def test_optional_field_conflict_becomes_warning_not_error() -> None:
    field_meta = {
        "englishpm": FieldMetadata(
            value=None,
            status="conflict",
            confidence=0.9,
            evidence=[],
            extraction_method="regex",
            validation_errors=["同一字段存在多个不同候选值"],
        )
    }
    result = ResultBuilder().build(
        task_id="task_003",
        schema_version="po_order.v1",
        field_meta=field_meta,
        context={},
        overall_confidence=0.0,
    )

    assert result.validation.is_valid is True
    assert any(item["field_key"] == "englishpm" for item in result.validation.warnings)


def test_freight_collect_text_is_invalid_and_becomes_null() -> None:
    candidate = FieldCandidate(
        field_key="inwageallinprice",
        value="COLLECT",
        raw_value="运费： COLLECT",
        confidence=0.9,
        evidence=[Evidence(document_id="doc_001", quote="运费： COLLECT")],
    )
    validated = CandidateValidator().validate([candidate])
    resolved = ConflictResolver().resolve(validated)

    assert validated[0].status == "invalid"
    assert "inwageallinprice" not in resolved or resolved["inwageallinprice"].value is None
