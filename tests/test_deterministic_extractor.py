from app.service.deterministic_extractor import extract_deterministic_candidates


def test_extracts_high_signal_booking_fields() -> None:
    text = """
    No of Packages: 20 CTN
    Gross Weight: 1,250.50 KGS
    Volume: 0.830 CBM
    Flight Date: 2026-08-14
    """

    candidates = extract_deterministic_candidates(text, "doc_001")
    values = {(item.field_key, item.value) for item in candidates}

    assert ("ybpiece", 20) in values
    assert ("ybweight", 1250.5) in values
    assert ("ybvolume", 0.83) in values
    assert any(item.field_key == "hbrq" for item in candidates)
    assert all(item.evidence for item in candidates)
