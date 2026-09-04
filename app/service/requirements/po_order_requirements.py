from typing import Any

from app.schemas.po_order import (
    PO_ORDER_OPTIONAL_KEYS,
    PO_ORDER_REQUIRED_KEYS,
)


class PoOrderRequirementService:
    """最终输出固定为 12 个字段，不再按进口/出口/国内区分必填。"""

    def __init__(self) -> None:
        self.required_keys: tuple[str, ...] = PO_ORDER_REQUIRED_KEYS
        self.optional_keys: tuple[str, ...] = PO_ORDER_OPTIONAL_KEYS

    def required_field_keys(self, context: dict[str, Any] | None = None) -> list[str]:
        """返回必填字段 key（与运输场景无关）。"""

        return list(self.required_keys)

    def optional_field_keys(self, context: dict[str, Any] | None = None) -> list[str]:
        """返回选填字段 key（与运输场景无关）。"""

        return list(self.optional_keys)

    def context_client_value(self, context: dict[str, Any]) -> Any:
        """从调用方 context 中读取委托客户值（暂时使用 fid 回填）。"""

        return context.get("fid")
