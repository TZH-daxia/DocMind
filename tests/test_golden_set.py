import json
from pathlib import Path

from app.schemas.po_order import PO_ORDER_KEYS


def test_five_booking_samples_have_golden_field_definitions() -> None:
    golden_directory = Path(__file__).parent / "golden"
    files = sorted(golden_directory.glob("*.json"))

    assert len(files) == 5
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["source_file"]
        assert payload["expected_fields"]
        assert set(payload["expected_fields"]).issubset(set(PO_ORDER_KEYS))
        assert "review_fields" in payload
