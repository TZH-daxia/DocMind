from app.schemas.analysis import FieldCandidate
from app.schemas.po_order import PO_ORDER_KEYS
from app.service.field_rules.date_rules import extract_date_candidates
from app.service.field_rules.package_rules import extract_package_candidates
from app.service.field_rules.port_rules import extract_port_candidates
from app.service.field_rules.product_rules import extract_product_candidates
from app.service.field_rules.quantity_rules import extract_quantity_candidates

ALLOWED_EXTRACTION_KEYS = frozenset(PO_ORDER_KEYS)


def extract_deterministic_candidates(text: str, document_id: str) -> list[FieldCandidate]:
    """调用确定性字段规则生成候选，仅保留最终输出的 12 个字段。"""

    candidates: list[FieldCandidate] = []
    candidates.extend(extract_port_candidates(text, document_id))
    candidates.extend(extract_package_candidates(text, document_id))
    candidates.extend(extract_quantity_candidates(text, document_id))
    candidates.extend(extract_date_candidates(text, document_id))
    candidates.extend(extract_product_candidates(text, document_id))
    return [
        candidate
        for candidate in candidates
        if candidate.field_key in ALLOWED_EXTRACTION_KEYS
    ]
