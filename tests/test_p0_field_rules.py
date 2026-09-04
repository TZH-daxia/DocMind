from app.schemas.analysis import Evidence, FieldCandidate
from app.service.conflict.conflict_resolver import ConflictResolver
from app.service.field_rules.date_rules import extract_date_candidates
from app.service.field_rules.hscode_rules import extract_hscode_candidates
from app.service.field_rules.package_rules import extract_package_candidates
from app.service.field_rules.port_rules import extract_port_candidates
from app.service.field_rules.quantity_rules import extract_quantity_candidates
from app.service.normalization.candidate_normalizer import CandidateNormalizer
from app.service.validation.candidate_validator import CandidateValidator


def test_port_rules_only_take_values_after_port_labels() -> None:
    text = """
    Shipper: ACME LTD, 18 ROAD STREET, SHANGHAI
    Airport of Departure: SHANGHAI
    Consignee: BUYER GMBH, MAIN STREET 1, FRANKFURT
    Airport of Destination: FRANKFURT
    """

    candidates = extract_port_candidates(text, "doc_001")

    assert [(item.field_key, item.value) for item in candidates] == [
        ("sfg", "SHANGHAI"),
        ("mdg", "FRANKFURT"),
    ]


def test_port_rules_handle_bilingual_glued_labels_and_table_headers() -> None:
    """中英文标签粘连、表头文字不得成为港口候选。"""

    text = (
        "始发站Airport of DepartureSHANGHAI 到达站FRANKFURT "
        "路线及到达站Routing And Destination"
    )

    candidates = extract_port_candidates(text, "doc_001")
    values = {(item.field_key, item.value) for item in candidates}

    assert ("sfg", "SHANGHAI") in values
    assert ("mdg", "FRANKFURT") in values
    assert all(value != "AIRPORT OF DEPARTURESHANGHAI" for _, value in values)
    assert all(value != "ROUTING AND DESTINATION" for _, value in values)


def test_port_rules_handle_bracketed_bilingual_labels() -> None:
    text = "始发站 (Airport of Departure)：SHANGHAI 到达站 (Airport of Destination)：FRANKFURT"

    candidates = extract_port_candidates(text, "doc_001")
    values = {(item.field_key, item.value) for item in candidates}

    assert ("sfg", "SHANGHAI") in values
    assert ("mdg", "FRANKFURT") in values


def test_port_rules_reject_non_place_words_after_labels() -> None:
    """标签后粘连的非地名（如费用栏“始发地其他费用”）不得成为港口候选。"""

    text = "始发地其他费用 AIR FREIGHT CHARGES OTHER CHARGES AT ORIGIN"

    candidates = extract_port_candidates(text, "doc_001")

    assert candidates == []


def test_port_rules_keep_iata_codes_and_reject_unknown_words() -> None:
    """机场三字代码是合法地名；白名单外的普通词语被过滤。"""

    text = "始发站:PVG 到达站:FRA 始发港:半导体开关元件"

    candidates = extract_port_candidates(text, "doc_001")
    values = {(item.field_key, item.value) for item in candidates}

    assert ("sfg", "PVG") in values
    assert ("mdg", "FRA") in values
    assert all(value != "半导体开关元件" for _, value in values)


def test_package_rules_extract_quantity_and_unit() -> None:
    candidates = extract_package_candidates("包装数量：1PLT", "doc_001")

    assert len(candidates) == 1
    assert candidates[0].value == 1
    assert candidates[0].unit == "PLT"
    assert candidates[0].raw_value == "包装数量：1PLT"


def test_quantity_rules_keep_gross_weight_and_volume_separate() -> None:
    text = "Gross Weight: 1,250.50 KGS\nVolume(CBM): 0.780 CBM\nN.W.: 900 KGS"

    candidates = extract_quantity_candidates(text, "doc_001")
    values = {(item.field_key, item.value, item.unit) for item in candidates}

    assert ("ybweight", 1250.5, "KG") in values
    assert ("ybvolume", 0.78, "CBM") in values
    assert all(item.field_key != "ybweight" or item.value != 900 for item in candidates)


def test_hscode_rules_do_not_join_the_following_cargo_number() -> None:
    candidates = extract_hscode_candidates(
        "海关代码:843311000072048489 LT,EE,LV LAWN MOWER",
        "doc_001",
    )

    assert [item.value for item in candidates] == ["8433110000"]


def test_date_range_is_not_normalized_to_one_date() -> None:
    candidates = extract_date_candidates("船期：2026/09/28\\~2026/10/05", "doc_001")

    assert len(candidates) == 1
    assert candidates[0].status == "needs_review"
    assert "日期区间" in candidates[0].validation_errors[0]


def test_validator_rejects_address_as_port_and_freight_as_text() -> None:
    candidates = [
        FieldCandidate(
            field_key="sfg",
            value="BUYER GMBH, MAIN STREET 1, FRANKFURT",
            evidence=[Evidence(document_id="doc_001", quote="address")],
        ),
        FieldCandidate(
            field_key="inwageallinprice",
            value="COLLECT",
            evidence=[Evidence(document_id="doc_001", quote="运费: COLLECT")],
        ),
    ]

    validated = CandidateValidator().validate(candidates)

    assert validated[0].status == "invalid"
    assert validated[1].status == "invalid"
    assert "地址" in validated[0].validation_errors[0]
    assert "不是数值" in validated[1].validation_errors[0]


def test_validator_downgrades_unknown_port_place_to_needs_review() -> None:
    """白名单外的模型港口候选降级人工复核；白名单内地名保持 normalized。"""

    candidates = [
        FieldCandidate(
            field_key="sfg",
            value="DACHAU",
            raw_value="Dachau",
            status="normalized",
            confidence=0.9,
            evidence=[Evidence(document_id="doc_001", quote="Destination: Dachau")],
        ),
        FieldCandidate(
            field_key="mdg",
            value="FRANKFURT",
            raw_value="FRANKFURT",
            status="normalized",
            confidence=0.95,
            evidence=[Evidence(document_id="doc_001", quote="Destination: FRANKFURT")],
        ),
    ]

    validated = CandidateValidator().validate(candidates)

    assert validated[0].status == "needs_review"
    assert validated[0].value == "DACHAU"
    assert "白名单" in validated[0].validation_errors[-1]
    assert validated[1].status == "normalized"


def test_resolver_deduplicates_same_value_and_keeps_conflicts() -> None:
    candidates = [
        FieldCandidate(
            field_key="ybweight",
            value=168,
            raw_value="168 KGS",
            unit="KG",
            status="normalized",
            confidence=0.98,
            evidence=[Evidence(document_id="doc_001", quote="Gross Weight: 168 KGS")],
        ),
        FieldCandidate(
            field_key="ybweight",
            value="168",
            raw_value="168",
            unit="KG",
            status="normalized",
            confidence=0.92,
            evidence=[Evidence(document_id="doc_001", quote="168")],
        ),
        FieldCandidate(
            field_key="ybvolume",
            value=0.78,
            status="normalized",
            confidence=0.98,
            evidence=[Evidence(document_id="doc_001", quote="0.78 CBM")],
        ),
        FieldCandidate(
            field_key="ybvolume",
            value=1.2,
            status="normalized",
            confidence=0.9,
            evidence=[Evidence(document_id="doc_001", quote="1.2 CBM")],
        ),
    ]
    candidates = CandidateNormalizer().normalize(candidates)
    candidates = CandidateValidator().validate(candidates)
    resolved = ConflictResolver().resolve(candidates)

    assert resolved["ybweight"].value == 168
    assert len(resolved["ybweight"].evidence) == 2
    assert resolved["ybvolume"].value is None
    assert resolved["ybvolume"].status == "conflict"


def test_resolver_merges_whitespace_variants_of_english_name() -> None:
    """MinerU 粘连文本与 VLM 正常空格的品名视为同一值，保留分词更规范的表述。"""

    candidates = [
        FieldCandidate(
            field_key="englishpm",
            value="WOMEN KNITTED DRESS/WOMENKNITTED CARDIGAN",
            status="conflict",
            confidence=0.95,
            evidence=[
                Evidence(document_id="doc_001", quote="WOMEN KNITTED DRESS/WOMENKNITTED CARDIGAN")
            ],
        ),
        FieldCandidate(
            field_key="englishpm",
            value="WOMEN KNITTED DRESS/WOMEN KNITTED CARDIGAN",
            status="conflict",
            confidence=0.8,
            evidence=[Evidence(document_id="doc_001", quote="VLM 图片读取")],
        ),
    ]

    resolved = ConflictResolver().resolve(candidates)

    assert resolved["englishpm"].value == "WOMEN KNITTED DRESS/WOMEN KNITTED CARDIGAN"
    assert resolved["englishpm"].status == "normalized"
    assert resolved["englishpm"].confidence == 0.95
    assert len(resolved["englishpm"].evidence) == 2


def test_resolver_keeps_real_conflict_between_distinct_names() -> None:
    """去空白后仍不同的品名依然是冲突，不能合并。"""

    candidates = [
        FieldCandidate(
            field_key="englishpm",
            value="WOMEN KNITTED DRESS",
            status="normalized",
            confidence=0.95,
            evidence=[Evidence(document_id="doc_001", quote="dress")],
        ),
        FieldCandidate(
            field_key="englishpm",
            value="MEN KNITTED DRESS",
            status="normalized",
            confidence=0.8,
            evidence=[Evidence(document_id="doc_001", quote="men dress")],
        ),
    ]

    resolved = ConflictResolver().resolve(candidates)

    assert resolved["englishpm"].value is None
    assert resolved["englishpm"].status == "conflict"
