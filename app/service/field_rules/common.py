import html
import re

from app.schemas.analysis import Evidence


def plain_document_text(text: str) -> str:
    """将 Markdown 中的 HTML 表格转换为便于规则匹配的文本。"""

    normalized = re.sub(
        r"</(?:td|th|tr|table|p)>|<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = html.unescape(normalized)
    normalized = normalized.replace("\u00a0", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{2,}", "\n", normalized)
    return normalized


def evidence_from_match(
    document_id: str,
    text: str,
    start: int,
    end: int,
) -> Evidence:
    """根据匹配位置生成短原文证据。"""

    quote = text[max(0, start - 80) : min(len(text), end + 80)]
    return Evidence(
        document_id=document_id,
        quote=quote.replace("\n", " ").strip()[:300],
    )
