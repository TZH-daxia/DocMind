import re

from app.schemas.analysis import FieldCandidate
from app.service.field_rules.common import evidence_from_match, plain_document_text

PACKAGE_UNIT_MAP = {
    "PLT": "PLT",
    "PALLET": "PLT",
    "PALLETS": "PLT",
    "CTN": "CTN",
    "CARTON": "CTN",
    "CARTONS": "CTN",
    "PKG": "PKG",
    "PKGS": "PKG",
    "PCS": "PCS",
}

LABEL_PATTERN = (
    r"(?:No\.?\s*of\s*Packages|Packages|件数|包装件数|包装数量|托盘数量)"
    r"[ \t]*[:：]?[ \t]*(\d[\d,]*)[ \t]*"
    r"(PLT|PALLETS?|CTN|CARTONS?|PKGS?|PCS)?"
)
PACKED_PATTERN = r"\b(\d[\d,]*)\s*(PLT|PALLETS?|CTN|CARTONS?|PKGS?)\b"


def extract_package_candidates(text: str, document_id: str) -> list[FieldCandidate]:
    """抽取包装件数并保留包装单位。"""

    plain_text = plain_document_text(text)
    candidates: list[FieldCandidate] = []
    occupied: list[tuple[int, int]] = []
    for match in re.finditer(LABEL_PATTERN, plain_text, flags=re.IGNORECASE):
        candidates.append(_candidate(match, plain_text, document_id, 0.98))
        occupied.append((match.start(), match.end()))
    for match in re.finditer(PACKED_PATTERN, plain_text, flags=re.IGNORECASE):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        candidates.append(_candidate(match, plain_text, document_id, 0.92))
    return candidates


def _candidate(
    match: re.Match[str],
    text: str,
    document_id: str,
    confidence: float,
) -> FieldCandidate:
    quantity = int(match.group(1).replace(",", ""))
    raw_unit = (match.group(2) or "").upper()
    unit = PACKAGE_UNIT_MAP.get(raw_unit) if raw_unit else None
    raw_value = match.group(0).strip()
    return FieldCandidate(
        field_key="ybpiece",
        value=quantity,
        raw_value=raw_value,
        unit=unit,
        status="normalized",
        confidence=confidence,
        evidence=[
            evidence_from_match(
                document_id,
                text,
                match.start(),
                match.end(),
            )
        ],
        extraction_method="regex",
    )
