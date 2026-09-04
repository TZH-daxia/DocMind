import re

from app.schemas.analysis import FieldCandidate
from app.service.field_rules.common import evidence_from_match, plain_document_text

ENGLISH_NAME = r"[A-Za-z][A-Za-z0-9 /&().,'-]{2,}"
CHINESE_NAME = r"[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9 /（）()、，,.-]{1,}"


def extract_product_candidates(text: str, document_id: str) -> list[FieldCandidate]:
    """从托书明确的中英文品名位置抽取候选。"""

    plain_text = plain_document_text(text)
    candidates: list[FieldCandidate] = []
    candidates.extend(_extract_labeled_names(plain_text, document_id))
    candidates.extend(_extract_bilingual_names(plain_text, document_id))
    candidates.extend(_extract_slash_names(plain_text, document_id))
    return candidates


def _extract_labeled_names(text: str, document_id: str) -> list[FieldCandidate]:
    """抽取带英文品名或中文品名标签的值。"""

    candidates: list[FieldCandidate] = []
    patterns = (
        (
            "englishpm",
            rf"(?:English\s+Description|英文品名)[ \t]*[:：][ \t]*({ENGLISH_NAME})",
        ),
        (
            "chinesepm",
            rf"(?:中文品名|中文货物品名)[ \t]*[:：][ \t]*({CHINESE_NAME})",
        ),
    )
    for field_key, pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = _clean_name(match.group(1), field_key)
            if not value:
                continue
            candidates.append(_candidate(field_key, value, text, document_id, match))
    return candidates


def _extract_bilingual_names(text: str, document_id: str) -> list[FieldCandidate]:
    """抽取中文品名后紧跟英文品名的表格内容。"""

    pattern = rf"(?P<chinese>{CHINESE_NAME})[ \t]*\n[ \t]*(?P<english>{ENGLISH_NAME})(?=[ \t]*\n[ \t]*(?:HS\s*CODE|海关代码|$))"
    candidates: list[FieldCandidate] = []
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        chinese = _clean_name(match.group("chinese"), "chinesepm")
        english = _clean_name(match.group("english"), "englishpm")
        if chinese:
            candidates.append(
                _candidate(
                    "chinesepm",
                    chinese,
                    text,
                    document_id,
                    match,
                )
            )
        if english:
            candidates.append(
                _candidate(
                    "englishpm",
                    english,
                    text,
                    document_id,
                    match,
                )
            )
    return candidates


def _extract_slash_names(text: str, document_id: str) -> list[FieldCandidate]:
    """抽取货号、地区前缀和中英文品名使用斜线分隔的表格内容。"""

    pattern = rf"(?P<prefix>(?:\d{{6,}}\s+)?(?:[A-Z]{{2}}(?:,[A-Z]{{2}})*\s+)*)?(?P<english>{ENGLISH_NAME})[ \t]*/[ \t]*(?P<chinese>{CHINESE_NAME})"
    candidates: list[FieldCandidate] = []
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        english = _clean_name(match.group("english"), "englishpm")
        chinese = _clean_name(match.group("chinese"), "chinesepm")
        if english:
            candidates.append(_candidate("englishpm", english, text, document_id, match))
        if chinese:
            candidates.append(_candidate("chinesepm", chinese, text, document_id, match))
    return candidates


def _candidate(
    field_key: str,
    value: str,
    text: str,
    document_id: str,
    match: re.Match[str],
) -> FieldCandidate:
    """创建确定性品名候选。"""

    return FieldCandidate(
        field_key=field_key,
        value=value,
        raw_value=match.group(0).strip(),
        status="normalized",
        confidence=0.97,
        evidence=[evidence_from_match(document_id, text, match.start(), match.end())],
        extraction_method="regex",
    )


def _clean_name(value: str, field_key: str) -> str:
    """清理品名中的标签、货号和地区前缀。"""

    normalized = re.sub(r"<[^>]+>", " ", value)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ：:;,，")
    if field_key == "englishpm":
        normalized = re.sub(r"^\d{6,}\s+", "", normalized)
        normalized = re.sub(
            r"^(?:[A-Z]{2},)*[A-Z]{2}\s+",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"\s+(?:HS\s*CODE|海关代码).*?$", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*/\s*[\u4e00-\u9fff].*$", "", normalized)
        normalized = normalized.strip(" /")
        return normalized.upper()
    normalized = re.split(
        r"(?:HS\s*CODE|海关代码)",
        normalized,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    normalized = normalized.strip(" /：:;,，")
    return normalized
