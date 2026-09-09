"""抽取器"空结果守卫"测试。

模型偶发输出空对象或带包装层的 JSON，历史上会被 _parse_flat 静默解析成
12 个 missing 候选，表现为"任务成功但结果全空"。这里锁定新行为：空结果重试
一次，仍空则抛 EmptyExtractionError 交由调用方标记失败。
"""

import json
from typing import Any

import pytest

from app.agent.deepseek_extractor import DeepSeekExtractionAgent, EmptyExtractionError
from app.config import Settings
from app.schemas.po_order import PO_ORDER_KEYS


class StubChain:
    """按脚本顺序返回文本输出的链替身。"""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    async def ainvoke(self, _variables: dict[str, Any]) -> str:
        self.calls += 1
        return self.outputs.pop(0)


def build_agent(outputs: list[str]) -> tuple[DeepSeekExtractionAgent, StubChain]:
    """构造只走 free-form 的抽取器（结构化链置空便于隔离测试）。"""

    agent = DeepSeekExtractionAgent(Settings(deepseek_api_key="test-key"))
    agent.structured_chain = None  # type: ignore[assignment]
    chain = StubChain(outputs)
    agent.extraction_chain = chain  # type: ignore[assignment]
    return agent, chain


def flat_payload(with_value: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        key: {"value": None, "status": "missing"} for key in PO_ORDER_KEYS
    }
    if with_value:
        payload["sfg"] = {
            "value": "SHANGHAI",
            "status": "normalized",
            "confidence": 0.99,
            "evidence": [{"quote": "装运港： SHANGHAI"}],
        }
    return payload


async def test_normal_output_returns_candidates() -> None:
    agent, chain = build_agent([json.dumps(flat_payload(), ensure_ascii=False)])

    candidates = await agent.extract("system", "视觉理解内容", {})

    assert chain.calls == 1
    assert any(
        candidate.field_key == "sfg" and candidate.value == "SHANGHAI"
        for candidate in candidates
    )


async def test_empty_object_retries_then_raises() -> None:
    agent, chain = build_agent(["{}", "{}"])

    with pytest.raises(EmptyExtractionError):
        await agent.extract("system", "视觉理解内容", {})

    assert chain.calls == 2


async def test_all_missing_values_treated_as_empty() -> None:
    payload = json.dumps(flat_payload(with_value=False), ensure_ascii=False)
    agent, chain = build_agent([payload, payload])

    with pytest.raises(EmptyExtractionError):
        await agent.extract("system", "视觉理解内容", {})

    assert chain.calls == 2


async def test_retry_recovers_when_second_attempt_has_values() -> None:
    agent, chain = build_agent(
        ["{}", json.dumps(flat_payload(), ensure_ascii=False)]
    )

    candidates = await agent.extract("system", "视觉理解内容", {})

    assert chain.calls == 2
    assert any(candidate.value == "SHANGHAI" for candidate in candidates)


async def test_wrapper_key_is_unwrapped() -> None:
    wrapped = json.dumps({"result": flat_payload()}, ensure_ascii=False)
    agent, chain = build_agent([wrapped])

    candidates = await agent.extract("system", "视觉理解内容", {})

    assert chain.calls == 1
    assert any(candidate.value == "SHANGHAI" for candidate in candidates)
