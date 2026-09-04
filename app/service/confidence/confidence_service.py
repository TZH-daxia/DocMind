from app.schemas.analysis import FieldMetadata


class ConfidenceService:
    """计算字段和任务整体置信度。"""

    def calculate_overall(self, field_meta: dict[str, FieldMetadata]) -> float:
        """计算所有最终非空字段的平均置信度。"""

        values = [
            metadata.confidence
            for metadata in field_meta.values()
            if metadata.value is not None
        ]
        return sum(values) / len(values) if values else 0.0
