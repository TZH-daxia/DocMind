"""兜底定位片段排序测试：日期类字段不能把高亮打到"年份来源"那个参考日期上。"""

from app.service.analysis_service import _fallback_segments


def test_value_fragment_wins_over_year_reference() -> None:
    """值 2024-07-25 来自 `ETD：7月25日`，落款 `Date 日期：2024.7.19` 只是年份来源。"""

    segments = _fallback_segments(
        ["ETD：7月25日", "Date 日期：2024.7.19"], "2024-07-25"
    )

    assert segments[0] == "7月25日"
    # 参考日期里的 2024-7-19（含值里没有的 1、9）排到最后，不会抢到主证据前面
    assert segments[-1] == "2024-7-19"


def test_digits_unexplainable_by_value_rank_after_labels() -> None:
    segments = _fallback_segments(
        ["ETD：7月25日", "Date 日期：2024.7.19"], "2024-07-25"
    )

    assert segments.index("date") < segments.index("2024-7-19")
    assert segments.index("etd") < segments.index("2024-7-19")


def test_labels_rank_after_value_fragments() -> None:
    segments = _fallback_segments(
        ["ETD：7月25日", "Date 日期：2024.7.19"], "2024-07-25"
    )

    assert segments.index("7月25日") < segments.index("date")
    assert segments.index("7月25日") < segments.index("etd")
    assert segments.index("7月25日") < segments.index("日期")


def test_primary_quote_wins_on_tie() -> None:
    """同级同长（都是 `8-20` 的等价写法）时按引用顺序，主证据的写法在前。"""

    segments = _fallback_segments(["订LH 8.20 航班", "Date 20-8"], "2024-08-20")

    assert segments[0] == "8-20"
    assert segments.index("8-20") < segments.index("20-8")


def test_reference_quote_day_is_not_preferred() -> None:
    """参考引用 `Date 11/Mar/24` 里的片段含值里没有的 1，整体排在主证据片段之后。"""

    segments = _fallback_segments(["订LH 8.20 航班", "Date 11/Mar/24"], "2024-08-20")

    assert segments[0] == "8-20"
    assert segments.index("8-20") < segments.index("11-mar-24")


def test_segments_are_deduplicated() -> None:
    segments = _fallback_segments(["Date 2026", "Date 2026"], "2026-08-14")

    assert segments.count("2026") == 1
    assert segments.count("date") == 1


def test_no_quotes_yields_nothing() -> None:
    assert _fallback_segments([], "2024-07-25") == []
