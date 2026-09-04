import re

from app.schemas.analysis import FieldCandidate
from app.service.field_rules.common import evidence_from_match, plain_document_text

NUMBER = r"([0-9][0-9,]*(?:\.[0-9]+)?)"


def extract_quantity_candidates(text: str, document_id: str) -> list[FieldCandidate]:
    """抽取毛重、体积和计费重量候选。"""

    plain_text = plain_document_text(text)
    candidates: list[FieldCandidate] = []
    candidates.extend(
        _extract(
            plain_text,
            document_id,
            "ybweight",
            rf"(?:Gross\s*Weight|G\.?W\.?|实际毛重|毛重)[ \t]*(?:\([^)]*\))?[ \t]*[:：]?[ \t]*{NUMBER}[ \t]*(KG|KGS|公斤)?",
            "KG",
            0.98,
        )
    )
    candidates.extend(
        _extract_explicit_cbm_values(
            plain_text,
            document_id,
            occupied=[
                (item.evidence[0].quote or "").find(item.raw_value or "")
                for item in candidates
                if item.field_key == "ybvolume"
            ],
        )
    )
    candidates.extend(
        _extract(
            plain_text,
            document_id,
            "ybvolume",
            rf"(?:Total\s*Volume|Volume|VOL\.?|Meas\.|总体积|体积)[ \t]*(?:\([^)]*\))?[ \t]*[:：]?[ \t]*{NUMBER}[ \t]*(CBM|CMB|立方米)?",
            "CBM",
            0.98,
        )
    )
    candidates.extend(
        _extract(
            plain_text,
            document_id,
            "jfweight",
            rf"(?:Chargeable\s*Weight|C\.?W\.?|计费重量)[ \t]*(?:\([^)]*\))?[ \t]*[:：]?[ \t]*{NUMBER}[ \t]*(KG|KGS|公斤)?",
            "KG",
            0.98,
        )
    )
    return candidates


def _extract_explicit_cbm_values(
    text: str,
    document_id: str,
    occupied: list[int],
) -> list[FieldCandidate]:
    """补充表格合计行中带 CBM 单位但没有字段标签的体积。"""

    pattern = rf"(?<![0-9.]){NUMBER}[ \t]*(CBM|CMB|立方米)\b"
    candidates: list[FieldCandidate] = []
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        if any(position >= 0 and match.start() <= position <= match.end() for position in occupied):
            continue
        raw_number = match.group(1)
        candidates.append(
            FieldCandidate(
                field_key="ybvolume",
                value=float(raw_number.replace(",", "")),
                raw_value=match.group(0).strip(),
                unit="CBM",
                status="normalized",
                confidence=0.96,
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
        )
    return candidates


def _extract(
    text: str,
    document_id: str,
    field_key: str,
    pattern: str,
    default_unit: str,
    confidence: float,
) -> list[FieldCandidate]:
    candidates: list[FieldCandidate] = []
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        raw_number = match.group(1)
        raw_unit = match.group(2) if match.lastindex and match.lastindex >= 2 else None
        candidates.append(
            FieldCandidate(
                field_key=field_key,
                value=float(raw_number.replace(",", "")),
                raw_value=match.group(0).strip(),
                unit=default_unit if raw_unit or default_unit else None,
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
        )
    return candidates
