"""港口主数据索引测试：覆盖三字码直通、词元匹配、防误命中与非港口判定。"""

from app.collector.port_reference_index import (
    PortReferenceIndex,
    looks_like_place,
    normalize_port_text,
    tokenize_port_text,
)
from app.schemas.port import PortRecord

RECORDS = [
    PortRecord(three_code="PVG", country_code="CN", english_name="SHANGHAIPUDONG"),
    PortRecord(three_code="SHA", country_code="CN", english_name="SHANGHAIHONGQIAO"),
    PortRecord(three_code="FRA", country_code="DE", english_name="FRANKFURT"),
    PortRecord(three_code="NGB", country_code="CN", english_name="NINGBO"),
    # 用于验证任意子串匹配会命中的"陷阱记录"
    PortRecord(three_code="JCC", country_code="US", english_name="SANFRANCISCOCHINABAS"),
    PortRecord(three_code="CAQ", country_code="CO", english_name="CAUCASIA"),
]


def test_normalize_and_tokenize() -> None:
    assert normalize_port_text("Frankfurt, Germany") == "FRANKFURTGERMANY"
    assert normalize_port_text("  ningbo  ") == "NINGBO"
    assert tokenize_port_text("Ningbo, China") == ["NINGBO", "CHINA"]


def test_three_code_passthrough() -> None:
    """用户本来就填了三字码：大小写都要直通，且不能走名字匹配。"""

    index = PortReferenceIndex(RECORDS)

    for raw in ("PVG", "pvg", "Pvg"):
        result = index.lookup(raw)
        assert result.kind == "unique"
        assert result.candidates[0].three_code == "PVG"
        assert result.matched_by == "code"


def test_three_code_length_input_is_not_treated_as_name() -> None:
    """`SHA` 在三字码表里存在 → 直通；不应被当成名字去命中一堆无关记录。"""

    index = PortReferenceIndex(RECORDS)

    result = index.lookup("SHA")

    assert result.kind == "unique"
    assert result.candidates[0].three_code == "SHA"


def test_exact_name_match() -> None:
    index = PortReferenceIndex(RECORDS)

    result = index.lookup("Shanghai Pudong")

    assert result.kind == "unique"
    assert result.candidates[0].three_code == "PVG"
    assert result.matched_by == "name_exact"


def test_token_match_ignores_country_token() -> None:
    """`NINGBO, China` 里的 CHINA 只是噪声，不应参与匹配。"""

    index = PortReferenceIndex(RECORDS)

    for raw in ("NINGBO", "Ningbo, China", "Ningbo,China"):
        result = index.lookup(raw)
        assert result.kind == "unique"
        assert result.candidates[0].three_code == "NGB"


def test_city_prefix_returns_multiple_candidates() -> None:
    """未限定机场的城市名 → 客观多义，候选来自主数据。"""

    index = PortReferenceIndex(RECORDS)

    result = index.lookup("SHANGHAI")

    assert result.kind == "ambiguous"
    assert {item.three_code for item in result.candidates} == {"PVG", "SHA"}
    assert result.matched_by == "name_prefix"


def test_substring_must_not_hit() -> None:
    """任意子串匹配会让 CHINA 命中 SANFRANCISCOCHINABAS，必须避免。"""

    index = PortReferenceIndex(RECORDS)

    for raw in ("CHINA", "ASIA", "GERMANY"):
        assert index.lookup(raw).kind == "not_found"


def test_short_name_does_not_participate_in_prefix_match() -> None:
    """主数据里有大量短名，短词元不能做前缀匹配。"""

    index = PortReferenceIndex(
        [PortRecord(three_code="AAA", country_code="PF", english_name="ANAA")]
    )

    assert index.lookup("AN").kind == "not_found"


def test_unknown_value_is_not_found() -> None:
    index = PortReferenceIndex(RECORDS)

    assert index.lookup("Schweinfurt").kind == "not_found"
    assert index.lookup("").kind == "not_found"


def test_noise_token_does_not_prefix_match() -> None:
    """`Frankfurt Intl` 里的 INTL 不能前缀命中 INTLFALLS（实测踩到的误命中）。"""

    index = PortReferenceIndex(
        [
            PortRecord(three_code="FRA", country_code="DE", english_name="FRANKFURT"),
            PortRecord(three_code="INL", country_code="US", english_name="INTLFALLS"),
        ]
    )

    result = index.lookup("Frankfurt Intl")

    assert result.kind == "unique"
    assert result.candidates[0].three_code == "FRA"


def test_many_candidates_are_capped() -> None:
    """极端输入命中上百条时：候选截断到上限，真实总数带回给前端折叠展示。"""

    records = [
        PortRecord(
            three_code=f"S{index:02d}X",
            country_code="US",
            english_name=f"SANTACITY{index}",
        )
        for index in range(30)
    ]
    index = PortReferenceIndex(records)

    result = index.lookup("SANTA")

    assert result.kind == "ambiguous"
    assert len(result.candidates) == 20
    assert result.total == 30


def test_looks_like_place() -> None:
    assert looks_like_place("SHANGHAI") is True
    assert looks_like_place("法兰克福") is True
    assert looks_like_place("Ningbo, China") is True
    assert looks_like_place("FOB") is False
    assert looks_like_place("GERMANY") is False
    assert looks_like_place("德国") is False
    assert looks_like_place("中国") is False
    assert looks_like_place("其他费用") is False
    assert looks_like_place("") is False
    assert looks_like_place("X" * 60) is False


def test_chinese_country_name_is_not_a_port() -> None:
    """输入"德国"这类中文国家名：本地直接判非港口，不走模型。"""

    index = PortReferenceIndex(RECORDS)

    assert index.lookup("德国").kind == "not_found"
