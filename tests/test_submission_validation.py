"""提交前校验编排测试：港口转三字码 + 委托客户存在性（全部用替身）。"""

from pathlib import Path

from app.config import Settings
from app.schemas.analysis import SubmissionValidationRequest
from app.schemas.customer import CustomerCandidate, CustomerValidationOutcome
from app.schemas.port import PortCandidate, PortNormalizationOutcome
from app.service.analysis_service import AnalysisService


class StubPortService:
    def __init__(self, outcomes: dict[str, PortNormalizationOutcome] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.calls: list[dict[str, str]] = []

    async def normalize(self, fields: dict[str, str]) -> dict[str, PortNormalizationOutcome]:
        self.calls.append(fields)
        return self.outcomes


class StubCustomerService:
    def __init__(self, outcome: CustomerValidationOutcome | None = None) -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    async def validate(self, raw_value: str) -> CustomerValidationOutcome:
        self.calls.append(raw_value)
        return self.outcome  # type: ignore[return-value]


def make_service(tmp_path: Path) -> AnalysisService:
    return AnalysisService(
        Settings(DOCMIND_DATA_ROOT=tmp_path, deepseek_api_key="test-key")
    )


def port_outcome(
    status: str,
    assembled: str | None = None,
    candidates: list[PortCandidate] | None = None,
) -> PortNormalizationOutcome:
    return PortNormalizationOutcome(
        field_key="sfg",
        status=status,  # type: ignore[arg-type]
        raw_value="SHANGHAI",
        assembled=assembled,
        candidates=candidates or [],
    )


async def test_missing_fields_reported(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    stub = StubPortService()
    stub_customer = StubCustomerService()
    service.port_service = stub  # type: ignore[assignment]
    service.customer_service = stub_customer  # type: ignore[assignment]

    result = await service.validate_submission("t", SubmissionValidationRequest())

    assert result["ok"] is False
    for key in ("sfg", "mdg", "fid"):
        assert result["fields"][key]["code"] == "missing"
    # 没有可校验的值时不应触发下游服务
    assert stub.calls == []
    assert stub_customer.calls == []


async def test_ports_resolved_and_customer_ok(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.port_service = StubPortService(
        {
            "sfg": port_outcome("normalized", assembled="PVG"),
            "mdg": port_outcome("normalized", assembled="FRA"),
        }
    )
    service.customer_service = StubCustomerService(
        CustomerValidationOutcome(
            status="ok",
            raw_value="XPD",
            customer=CustomerCandidate(id="14620", usr_name="XPD", available=True),
            matched_by="usr_code",
        )
    )

    result = await service.validate_submission(
        "t", SubmissionValidationRequest(fid="XPD", sfg="上海浦东", mdg="FRA")
    )

    assert result["ok"] is True
    assert result["resolved"] == {"sfg": "PVG", "mdg": "FRA", "fid": "14620"}
    assert result["fields"]["sfg"]["value"] == "PVG"
    assert service.port_service.calls == [{"sfg": "上海浦东", "mdg": "FRA"}]  # type: ignore[attr-defined]


async def test_port_ambiguous_carries_candidates(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.port_service = StubPortService(
        {
            "sfg": port_outcome(
                "ambiguous",
                candidates=[
                    PortCandidate(three_code="PVG", english_name="SHANGHAIPUDONG"),
                    PortCandidate(three_code="SHA", english_name="SHANGHAIHONGQIAO"),
                ],
            ),
            "mdg": port_outcome("normalized", assembled="FRA"),
        }
    )
    service.customer_service = StubCustomerService(
        CustomerValidationOutcome(
            status="ok", raw_value="C1", customer=CustomerCandidate(id="1")
        )
    )

    result = await service.validate_submission(
        "t", SubmissionValidationRequest(fid="C1", sfg="SHANGHAI", mdg="FRA")
    )

    assert result["ok"] is False
    sfg = result["fields"]["sfg"]
    assert sfg["ok"] is False
    assert sfg["code"] == "ambiguous"
    assert {item["three_code"] for item in sfg["candidates"]} == {"PVG", "SHA"}
    assert result["resolved"].get("sfg") is None


async def test_customer_failures_mapped_to_messages(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.port_service = StubPortService(
        {
            "sfg": port_outcome("normalized", assembled="PVG"),
            "mdg": port_outcome("normalized", assembled="FRA"),
        }
    )

    for status, expected in (
        ("not_found", "客户不存在，请核对委托客户"),
        ("unavailable", "客户已停用或不参与新业务，请确认"),
        ("ambiguous", "该名称对应多个客户，请补充完整名称"),
    ):
        service.customer_service = StubCustomerService(
            CustomerValidationOutcome(
                status=status,  # type: ignore[arg-type]
                raw_value="X",
                candidates=[
                    CustomerCandidate(id="1"),
                    CustomerCandidate(id="2"),
                ]
                if status == "ambiguous"
                else [],
            )
        )
        result = await service.validate_submission(
            "t", SubmissionValidationRequest(fid="X", sfg="PVG", mdg="FRA")
        )
        assert result["fields"]["fid"]["message"] == expected, status
        assert result["ok"] is False
