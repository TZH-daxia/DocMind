import re
from datetime import date

from app.schemas.analysis import FieldCandidate, FieldStatus
from app.service.field_rules.common import evidence_from_match, plain_document_text

SINGLE_DATE = r"(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?)"
DATE_RANGE = (
    r"(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?"
    r"\s*\\?\s*(?:~|～|至|到)\s*"
    r"20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?)"
)
DATE_LABEL = r"(?:Flight\s*Date|Flight/Day|Sailing\s*Date|航班日期|航班日|船期)"


def extract_date_candidates(text: str, document_id: str) -> list[FieldCandidate]:
    """抽取航班日期或船期，日期区间保持待确认状态。"""

    plain_text = plain_document_text(text)
    candidates: list[FieldCandidate] = []
    range_pattern = rf"{DATE_LABEL}[ \t]*[:：]?[ \t]*{DATE_RANGE}"
    occupied: list[tuple[int, int]] = []
    for match in re.finditer(range_pattern, plain_text, flags=re.IGNORECASE):
        raw_value = match.group(1)
        candidates.append(
            _candidate(
                document_id,
                plain_text,
                match,
                raw_value,
                raw_value,
                "needs_review",
                ["日期区间不能直接填入单日期字段"],
            )
        )
        occupied.append((match.start(), match.end()))

    single_pattern = rf"{DATE_LABEL}[ \t]*[:：]?[ \t]*{SINGLE_DATE}"
    for match in re.finditer(single_pattern, plain_text, flags=re.IGNORECASE):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        raw_value = match.group(1)
        candidates.append(
            _candidate(
                document_id,
                plain_text,
                match,
                _normalize_date(raw_value),
                raw_value,
                "normalized",
                [],
            )
        )
    return candidates


def _candidate(
    document_id: str,
    text: str,
    match: re.Match[str],
    value: str,
    raw_value: str,
    status: FieldStatus,
    validation_errors: list[str],
) -> FieldCandidate:
    return FieldCandidate(
        field_key="hbrq",
        value=value,
        raw_value=raw_value,
        status=status,
        confidence=0.92,
        evidence=[
            evidence_from_match(
                document_id,
                text,
                match.start(),
                match.end(),
            )
        ],
        extraction_method="regex",
        validation_errors=validation_errors,
    )


def _normalize_date(value: str) -> str:
    normalized = value.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-")
    parsed = date.fromisoformat(normalized)
    return parsed.isoformat()
