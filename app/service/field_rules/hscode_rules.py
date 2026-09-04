import re

from app.schemas.analysis import FieldCandidate
from app.service.field_rules.common import evidence_from_match, plain_document_text

HSCODE_PATTERN = (
    r"(?:HS\s*CODE|H\.?S\.?\s*Code|Customs\s*Code|海关代码)"
    r"[ \t]*[:：]?[ \t]*(\d{10}|\d{8}|\d{6})"
)


def extract_hscode_candidates(text: str, document_id: str) -> list[FieldCandidate]:
    """按字段边界抽取 HS Code，避免与货号拼接。"""

    plain_text = plain_document_text(text)
    candidates: list[FieldCandidate] = []
    for match in re.finditer(HSCODE_PATTERN, plain_text, flags=re.IGNORECASE):
        value = match.group(1)
        candidates.append(
            FieldCandidate(
                field_key="hscode",
                value=value,
                raw_value=value,
                status="normalized",
                confidence=0.99,
                evidence=[
                    evidence_from_match(
                        document_id,
                        plain_text,
                        match.start(),
                        match.end(),
                    )
                ],
                extraction_method="regex",
            )
        )
    return candidates
