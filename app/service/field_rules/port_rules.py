import re

from app.schemas.analysis import FieldCandidate
from app.service.field_rules.common import evidence_from_match, plain_document_text
from app.service.field_rules.port_whitelist import is_known_port_place

# 捕获值中出现这些标签词时，说明匹配到的是表头文字或粘连标签，而不是港口名。
PORT_VALUE_STOP_WORDS = (
    "AIRPORT",
    "DEPARTURE",
    "DESTINATION",
    "ROUTING",
    "DISCHARGE",
    "LOADING",
    "CARRIER",
)

# MinerU 输出中标签与值可能完全粘连（如“始发站Airport of DepartureSHANGHAI”），
# 英文标签与港口名之间没有边界，因此先捕获再剥离粘连在值前的英文标签前缀。
PORT_LABEL_PREFIXES = (
    "AIRPORT OF DEPARTURE",
    "AIRPORT OF DESTINATION",
    "PORT OF LOADING",
    "PORT OF DISCHARGE",
    "FINAL DESTINATION",
    "ROUTING AND DESTINATION",
)

PORT_PATTERNS = {
    "sfg": (
        r"(?:Airport\s+of\s+Departure|Port\s+of\s+Loading|始发站|始发港|装运港|起运港|始发地)"
        r"[ \t]*[)）]?[ \t]*[:：]?[ \t]*"
        r"([A-Za-z][A-Za-z .'-]{1,35}|[\u4e00-\u9fff]{2,15})"
    ),
    "mdg": (
        r"(?:Airport\s+of\s+Destination|Port\s+of\s+Discharge|Final\s+Destination|"
        r"到达站|到达港|目的港|卸货港|最终目的地)"
        r"[ \t]*[)）]?[ \t]*[:：]?[ \t]*"
        r"([A-Za-z][A-Za-z .'-]{1,35}|[\u4e00-\u9fff]{2,15})"
    ),
}


def extract_port_candidates(text: str, document_id: str) -> list[FieldCandidate]:
    """按明确港口标签抽取始发港和目的港，剥离粘连标签并过滤表头文字。"""

    plain_text = plain_document_text(text)
    candidates: list[FieldCandidate] = []
    for field_key, pattern in PORT_PATTERNS.items():
        for match in re.finditer(pattern, plain_text, flags=re.IGNORECASE):
            raw_value = match.group(1).strip(" .,:：")
            cleaned_value = _clean_glued_label(raw_value)
            if not cleaned_value or _is_label_text(cleaned_value):
                continue
            # 标签后粘连的未必是地名（如费用栏“始发地其他费用”），
            # 规则候选必须命中地名白名单，避免错误的规则值覆盖模型结果。
            if not is_known_port_place(cleaned_value):
                continue
            candidates.append(
                FieldCandidate(
                    field_key=field_key,
                    value=cleaned_value.upper(),
                    raw_value=raw_value,
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


def _clean_glued_label(value: str) -> str:
    """剥离粘连在值前的英文标签词，如 'Airport of DepartureSHANGHAI' -> 'SHANGHAI'。"""

    upper_value = value.upper()
    for prefix in PORT_LABEL_PREFIXES:
        if upper_value.startswith(prefix):
            return value[len(prefix) :].strip(" .,:：")
    return value


def _is_label_text(value: str) -> bool:
    """判断清洗后的值是否仍为标签词或表头文字。"""

    upper_value = value.upper()
    return any(word in upper_value for word in PORT_VALUE_STOP_WORDS)
