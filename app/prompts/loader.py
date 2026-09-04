from pathlib import Path


def load_po_order_extraction_prompt() -> str:
    """加载托书订单字段抽取提示词。"""

    prompt_path = Path(__file__).with_name("po_order_extraction.md")
    return prompt_path.read_text(encoding="utf-8")


def load_po_order_vision_prompt() -> str:
    """加载托书图片视觉理解提示词。"""

    prompt_path = Path(__file__).with_name("po_order_vision.md")
    return prompt_path.read_text(encoding="utf-8")
