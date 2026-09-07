"""build_result 港口归一化门槛（字段级待人工审核判断）测试。"""

import json
from pathlib import Path

from app.config import Settings
from app.schemas.analysis import Evidence, FieldMetadata
from app.schemas.po_order import PORT_FIELD_KEYS
from app.schemas.port import PortNormalizationOutcome
from app.service.analysis_service import AnalysisService


class StubPortService:
    def __init__(self, outcomes: dict[str, PortNormalizationOutcome] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.calls: list[dict[str, str]] = []

    async def normalize(self, fields: dict[str, str]) -> dict[str, PortNormalizationOutcome]:
        self.calls.append(fields)
        return self.outcomes


def make_service(tmp_path: Path) -> AnalysisService:
    settings = Settings(DOCMIND_DATA_ROOT=tmp_path, deepseek_api_key="test-key")
    return AnalysisService(settings)


def make_meta(value: str | None, status: str, confidence: float = 0.9) -> FieldMetadata:
    return FieldMetadata(
        value=value,
        status=status,  # type: ignore[arg-type]
        confidence=confidence,
        evidence=[Evidence(quote="原文片段")],
        extraction_method="llm",
    )


async def test_gate_skips_needs_review_fields(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    stub = StubPortService()
    service.port_service = stub  # type: ignore[assignment]
    field_meta = {
        "sfg": make_meta("SHANGHAI PUDONG", "confirmed"),
        "mdg": make_meta("Schweinfurt, Germany", "needs_review"),
        "ybpiece": make_meta("10", "confirmed"),
    }
    review_fields: set[str] = {"mdg"}
    await service._normalize_port_fields({}, field_meta, review_fields)
    # 仅 sfg 参与归一化；待人工审核的 mdg 被跳过
    assert stub.calls == [{"sfg": "SHANGHAI PUDONG"}]


async def test_normalized_outcome_updates_result(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    outcome = PortNormalizationOutcome(
        field_key="sfg",
        status="normalized",
        raw_value="SHANGHAI PUDONG",
        three_code="PVG",
        english_name="Shanghai Pudong Intl",
        assembled="PVG",
    )
    stub = StubPortService({"sfg": outcome})
    service.port_service = stub  # type: ignore[assignment]
    field_meta = {"sfg": make_meta("SHANGHAI PUDONG", "confirmed")}
    result: dict = {"sfg": "SHANGHAI PUDONG"}
    review_fields: set[str] = set()
    await service._normalize_port_fields(result, field_meta, review_fields)
    assert result["sfg"] == "PVG"
    assert field_meta["sfg"].status == "normalized"
    assert field_meta["sfg"].value == "PVG"
    assert any("PVG" in (item.quote or "") for item in field_meta["sfg"].evidence)
    assert review_fields == set()


async def test_failed_outcome_downgrades_to_needs_review(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    outcome = PortNormalizationOutcome(
        field_key="sfg",
        status="failed",
        raw_value="Schweinfurt, Germany",
        reason="reference_miss",
    )
    stub = StubPortService({"sfg": outcome})
    service.port_service = stub  # type: ignore[assignment]
    field_meta = {"sfg": make_meta("Schweinfurt, Germany", "confirmed")}
    result: dict = {"sfg": "Schweinfurt, Germany"}
    review_fields: set[str] = set()
    await service._normalize_port_fields(result, field_meta, review_fields)
    # 保留原值，降级为待人工审核
    assert result["sfg"] == "Schweinfurt, Germany"
    assert field_meta["sfg"].status == "needs_review"
    assert "sfg" in review_fields


async def test_skipped_outcome_downgrades_to_needs_review(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    outcome = PortNormalizationOutcome(
        field_key="sfg",
        status="skipped",
        raw_value="SHANGHAI",
        reason="no_unique_code",
    )
    stub = StubPortService({"sfg": outcome})
    service.port_service = stub  # type: ignore[assignment]
    field_meta = {"sfg": make_meta("SHANGHAI", "confirmed")}
    result: dict = {"sfg": "SHANGHAI"}
    review_fields: set[str] = set()
    await service._normalize_port_fields(result, field_meta, review_fields)
    assert result["sfg"] == "SHANGHAI"
    assert field_meta["sfg"].status == "needs_review"
    assert review_fields == {"sfg"}


async def test_non_port_fields_ignored(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    stub = StubPortService()
    service.port_service = stub  # type: ignore[assignment]
    field_meta = {key: make_meta("value", "confirmed") for key in PORT_FIELD_KEYS}
    field_meta["ybpiece"] = make_meta("10", "confirmed")
    await service._normalize_port_fields({}, field_meta, set())
    assert stub.calls == [{"sfg": "value", "mdg": "value"}]


async def test_final_destination_candidate_proceeds_without_review(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    stub = StubPortService()
    service.port_service = stub  # type: ignore[assignment]
    task_id = "final-destination"
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
                    "field_key": "mdg",
                    "value": "GERMANY",
                    "status": "confirmed",
                    "confidence": 0.9,
                    "evidence": [{"quote": "Final Destination: GERMANY"}],
                    "extraction_method": "table",
                }
            ],
        }
    )

    assert output["result"]["mdg"] == "GERMANY"
    assert output["field_meta"]["mdg"]["status"] == "confirmed"
    assert stub.calls == [{"mdg": "GERMANY"}]


async def test_result_and_review_fields_follow_output_order(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.port_service = StubPortService()  # type: ignore[assignment]
    task_id = "ordered-result"
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
                    "field_key": "englishpm",
                    "value": "PRODUCT",
                    "status": "confirmed",
                    "confidence": 0.9,
                    "evidence": [{"quote": "PRODUCT"}],
                    "extraction_method": "llm",
                },
                {
                    "field_key": "mdg",
                    "value": "GERMANY",
                    "status": "needs_review",
                    "confidence": 0.9,
                    "evidence": [{"quote": "Final Destination: GERMANY"}],
                    "extraction_method": "llm",
                },
                {
                    "field_key": "sfg",
                    "value": "SHANGHAI",
                    "status": "needs_review",
                    "confidence": 0.9,
                    "evidence": [{"quote": "SHANGHAI"}],
                    "extraction_method": "llm",
                },
            ],
        }
    )

    assert list(output["result"]) == [
        "fid",
        "sfg",
        "mdg",
        "ybpiece",
        "ybweight",
        "ybvolume",
        "hbrq",
        "inwageallinprice",
        "chinesepm",
        "englishpm",
        "shipper",
        "consignee",
    ]
    assert list(output["field_meta"])[:3] == ["fid", "sfg", "mdg"]
    assert output["review_fields"] == [
        "sfg",
        "mdg",
        "ybpiece",
        "ybweight",
        "ybvolume",
        "hbrq",
        "inwageallinprice",
    ]
    status = service.get_task_status(task_id)
    assert status["status"] == "needs_review"
    assert status["completed_at"]
    assert status["review_fields"] == output["review_fields"]
    assert status["overall_confidence"] == output["overall_confidence"]

    process_events = [
        json.loads(line)
        for line in service.file_store.read_text(
            service.file_store.process_log_path(task_id)
        ).splitlines()
    ]
    result_event = next(
        event for event in process_events if event["event_type"] == "result_built"
    )
    assert result_event["kind"] == "task_lifecycle"
    assert result_event["details"]["review_fields"] == output["review_fields"]
