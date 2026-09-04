from app.schemas.analysis import Evidence, FieldCandidate, FieldMetadata
from app.schemas.po_order import PO_ORDER_KEYS, PartyInfo
from app.service.result_builder.result_builder import ResultBuilder
from app.service.validation.candidate_validator import CandidateValidator


def test_party_reference_fields_are_optional_and_in_schema() -> None:
    """发货人和收货人是选填字段，出现在 12 字段输出中但不进入必填。"""

    assert {"shipper", "consignee"}.issubset(PO_ORDER_KEYS)

    result = ResultBuilder().build(
        task_id="task_party",
        schema_version="po_order.v1",
        field_meta={
            "shipper": FieldMetadata(
                value={
                    "name": "Cleva International Trading Limited",
                    "address": ["18/F, NAM WO HONG BUILDING", "SHEUNG WAN, HK"],
                    "phone": "+852 1234 5678",
                    "email": "contact@example.com",
                },
                status="normalized",
                confidence=0.99,
                evidence=[Evidence(document_id="doc_001", quote="Shipper")],
                extraction_method="table",
            )
        },
        context={},
        overall_confidence=0.99,
    )

    assert result.result["shipper"]["name"] == "Cleva International Trading Limited"
    assert result.result["shipper"]["address"][0] == "18/F, NAM WO HONG BUILDING"
    assert result.result["consignee"] is None
    assert list(result.result).index("shipper") > list(result.result).index("fid")


def test_party_info_only_keeps_name_address_phone_email() -> None:
    """PartyInfo 只保留名称、地址、电话、邮箱四个子字段。"""

    party = PartyInfo.model_validate(
        {
            "name": "Cleva International Trading Limited",
            "address": ["18/F, NAM WO HONG BUILDING", "SHEUNG WAN, HK"],
            "phone": "+852 1234 5678",
            "email": "contact@example.com",
        }
    )

    assert party.model_dump() == {
        "name": "Cleva International Trading Limited",
        "address": ["18/F, NAM WO HONG BUILDING", "SHEUNG WAN, HK"],
        "phone": "+852 1234 5678",
        "email": "contact@example.com",
    }


def test_party_reference_candidate_structure_is_validated() -> None:
    """参与方字段只能接受统一对象结构。"""

    valid = FieldCandidate(
        field_key="consignee",
        value={
            "name": "Grizzly Tools GmbH & Co. KG",
            "address": ["Stockstadter StraBe 20", "Germany"],
            "phone": None,
            "email": None,
        },
        status="normalized",
        confidence=0.99,
        evidence=[
            Evidence(document_id="doc_001", quote="Consignee")
        ],
    )
    invalid = FieldCandidate(
        field_key="shipper",
        value="整段发货人字符串",
        confidence=0.8,
        evidence=[Evidence(document_id="doc_001", quote="Shipper")],
    )

    validated = CandidateValidator().validate([valid, invalid])

    assert validated[0].status == "normalized"
    assert validated[1].status == "invalid"
    PartyInfo.model_validate(validated[0].value)
