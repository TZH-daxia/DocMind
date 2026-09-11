"""证据坐标定位测试：覆盖命中、分隔符差异、表格符号、短值与无文本层。"""

from pathlib import Path

import pymupdf

from app.collector.evidence_locator import (
    DocumentTextIndex,
    LocatedBox,
    context_needles,
    resolve_source_pdf,
)
from app.schemas.analysis import Evidence, FieldMetadata
from app.service.analysis_service import AnalysisService


def build_pdf(path: Path, lines: list[tuple[float, float, str]]) -> Path:
    """按 (x, y, 文本) 生成单页可检索 PDF。"""

    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    for x, y, text in lines:
        page.insert_text((x, y), text, fontsize=12)
    document.save(path)
    document.close()
    return path


def build_image_only_pdf(path: Path) -> Path:
    """生成没有文本层的 PDF（模拟扫描件）。"""

    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    page.draw_rect(pymupdf.Rect(50, 50, 300, 120), fill=(0.8, 0.8, 0.8))
    document.save(path)
    document.close()
    return path


def test_search_finds_value_and_normalized_bbox(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 100, "SHANGHAI"), (50, 300, "FRANKFURT")])

    boxes = DocumentTextIndex(pdf).search("SHANGHAI")

    assert len(boxes) == 1
    box = boxes[0]
    assert box.page == 1
    x, y, width, height = box.bbox
    assert 0 <= x < 1 and 0 <= y < 1
    assert 0 < width < 1 and 0 < height < 1
    assert y < 0.2  # 位于页面顶部，与插入位置一致


def test_search_folds_separators(tmp_path: Path) -> None:
    """模型输出的 `-` 分隔日期要能命中原文的 `/`、`~` 写法。"""

    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 100, "Schedule 2026/09/28~2026/10/05")])

    boxes = DocumentTextIndex(pdf).search("2026-09-28~2026-10-05")

    assert len(boxes) == 1


def test_search_strips_table_pipes_and_whitespace(tmp_path: Path) -> None:
    """VLM 转写会用 `|` 画表格，原文没有这些符号，匹配时必须折叠掉。"""

    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 100, "Total 1PLT 168KGS")])

    boxes = DocumentTextIndex(pdf).search("Total||1PLT|168KGS|")

    assert len(boxes) == 1


def test_short_needle_is_ignored(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 100, "1PLT")])
    index = DocumentTextIndex(pdf)

    assert index.search("1") == []
    assert index.search_word("1") == []


def test_search_word_does_not_match_inside_longer_word(tmp_path: Path) -> None:
    """值 `1` 不能命中 `1PLT`，但能命中独立的 `1`。"""

    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 100, "1PLT"), (50, 400, "1")])
    index = DocumentTextIndex(pdf)

    boxes = index.search_word("1")

    assert len(boxes) == 1
    assert boxes[0].bbox[1] > 0.4  # 命中的是独立数字，而不是 1PLT


def test_search_word_matches_consecutive_words(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 100, "Gross Weight 3109 KG")])

    boxes = DocumentTextIndex(pdf).search_word("3109KG")

    assert len(boxes) == 1


def test_image_only_pdf_has_no_text_layer(tmp_path: Path) -> None:
    pdf = build_image_only_pdf(tmp_path / "scan.pdf")
    index = DocumentTextIndex(pdf)

    assert index.has_text_layer is False
    assert index.search("SHANGHAI") == []


def test_context_needles_keep_unit_from_quote() -> None:
    """从引用里取"带单位的值片段"，用于避开同数字的其它内容。"""

    assert context_needles("74", ["4cartons            74kg"]) == ["74kg"]
    assert context_needles("0.1828", ["0.1828CBM"]) == ["0-1828cbm"]


def test_context_needles_reject_partial_number() -> None:
    """值 `4` 不能把 `74kg` 当成自己的上下文（数字串号）。"""

    needles = context_needles("4", ["4cartons            74kg"])

    assert needles == ["4cartons"]
    assert all("74" not in needle for needle in needles)


def test_context_needles_skip_when_quote_has_no_context() -> None:
    assert context_needles("SHANGHAI", ["Airport of Departure SHANGHAI"]) == []
    assert context_needles("74", ["Weight"]) == []
    assert context_needles("74", []) == []


def test_locate_prefers_unit_context_over_bare_value(tmp_path: Path) -> None:
    """回归：裸值 74 只在收货人地址里作为独立词出现，带单位后必须定位到重量栏。"""

    pdf = build_pdf(
        tmp_path / "doc.pdf",
        [
            (50, 100, "R3 TEC GmbH"),
            (50, 115, "Schleissheimer Str. 74"),
            (50, 430, "4cartons"),
            (50, 445, "74kg"),
        ],
    )
    field_meta = {
        "ybweight": FieldMetadata(
            value="74",
            status="confirmed",
            evidence=[Evidence(quote="4cartons            74kg")],
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["ybweight"])

    assert len(located["ybweight"]) == 1
    box = located["ybweight"][0]
    assert box.bbox[1] > 0.4  # 落在重量栏，而不是地址行（y≈0.14）


def test_locate_without_context_still_uses_bare_value(tmp_path: Path) -> None:
    """引用里没有可用上下文时，退回裸值定位（现有行为不变）。"""

    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 430, "74kg")])
    field_meta = {
        "ybweight": FieldMetadata(
            value="74", status="confirmed", evidence=[Evidence(quote="Gross Weight")]
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["ybweight"])

    assert len(located["ybweight"]) == 1


def test_near_accepts_neighbouring_cell_in_same_row() -> None:
    """表格同一行的相邻列（不相交）也算邻近，跨行/跨页不算。"""

    anchor = LocatedBox(page=1, bbox=(0.10, 0.50, 0.05, 0.01))

    assert anchor.near(LocatedBox(page=1, bbox=(0.45, 0.50, 0.03, 0.01))) is True
    assert anchor.near(LocatedBox(page=1, bbox=(0.45, 0.60, 0.03, 0.01))) is False
    assert anchor.near(LocatedBox(page=2, bbox=(0.45, 0.50, 0.03, 0.01))) is False
    assert anchor.near(LocatedBox(page=1, bbox=(0.90, 0.50, 0.03, 0.01))) is False


def test_locate_keeps_place_segment_intact(tmp_path: Path) -> None:
    """回归：`Ningbo,China` 不能被逗号切开，否则 `ningbo` 会命中发货人公司名。"""

    pdf = build_pdf(
        tmp_path / "doc.pdf",
        [
            (50, 60, "Port of Loading:"),
            (300, 60, "Ningbo,China"),
            (50, 120, "Green Globe Machinery Ningbo Co., Ltd."),
        ],
    )
    field_meta = {
        "sfg": FieldMetadata(
            value="NGB",
            status="normalized",
            evidence=[
                Evidence(quote="Port of Loading:\nNingbo,China"),
                Evidence(quote="港口主数据：NGB NINGBO（原文：Ningbo,China）"),
            ],
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["sfg"])

    assert len(located["sfg"]) == 1
    # 落在 Port of Loading 的单元格（x≈0.5），而不是发货人公司名（x≈0.08）
    assert located["sfg"][0].bbox[0] > 0.4


def test_locate_narrows_by_quote_segment_when_full_quote_missing(tmp_path: Path) -> None:
    """回归：整条引用匹配不上时，用其词元锚点把多处命中收窄到标签旁那一处。"""

    pdf = build_pdf(
        tmp_path / "doc.pdf",
        [
            (50, 100, "Final Destination:"),
            (400, 105, "Other Column Text"),
            (50, 115, "GERMANY"),
            (50, 400, "GERMANY"),
        ],
    )
    field_meta = {
        "mdg": FieldMetadata(
            value="GERMANY",
            status="needs_review",
            evidence=[Evidence(quote="Final Destination:\nGERMANY")],
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["mdg"])

    assert len(located["mdg"]) == 1
    assert located["mdg"][0].bbox[1] < 0.25


def test_locate_narrows_duplicate_number_by_row_anchor(tmp_path: Path) -> None:
    """回归：体积 0.83 页面上出现两次，用同行锚点选到正确那一行。"""

    pdf = build_pdf(
        tmp_path / "doc.pdf",
        [
            (50, 100, "Item"),
            (400, 105, "Other Column Text"),
            (50, 115, "39 20 117 0.83"),
            (50, 400, "0.83"),
        ],
    )
    field_meta = {
        "ybvolume": FieldMetadata(
            value="0.83",
            status="confirmed",
            evidence=[Evidence(quote="Item\t39\t20\t117\t\t0.83")],
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["ybvolume"])

    assert len(located["ybvolume"]) == 1
    assert located["ybvolume"][0].bbox[1] < 0.25


def test_resolve_source_pdf_prefers_converted(tmp_path: Path) -> None:
    task_id = "task_demo"
    parsed_dir = tmp_path / "parsed_documents" / task_id
    parsed_dir.mkdir(parents=True)
    converted = parsed_dir / "托书_converted.pdf"
    converted.write_bytes(b"%PDF-1.4")
    upload = tmp_path / "uploaded_documents" / "托书.pdf"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"%PDF-1.4")

    resolved = resolve_source_pdf(
        tmp_path, task_id, "uploaded_documents/托书.pdf", "托书"
    )

    assert resolved == converted


def test_resolve_source_pdf_falls_back_to_uploaded_pdf(tmp_path: Path) -> None:
    task_id = "task_demo"
    (tmp_path / "parsed_documents" / task_id).mkdir(parents=True)
    upload = tmp_path / "uploaded_documents" / "托书.pdf"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"%PDF-1.4")

    resolved = resolve_source_pdf(
        tmp_path, task_id, "uploaded_documents/托书.pdf", "托书"
    )

    assert resolved == upload


def test_resolve_source_pdf_returns_none_without_pdf(tmp_path: Path) -> None:
    task_id = "task_demo"
    (tmp_path / "parsed_documents" / task_id).mkdir(parents=True)
    upload = tmp_path / "uploaded_documents" / "托书.xls"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"xls")

    assert resolve_source_pdf(tmp_path, task_id, "uploaded_documents/托书.xls", "托书") is None


def test_locate_narrows_value_by_evidence_quote(tmp_path: Path) -> None:
    """值在文档中出现多次时，用证据引用把位置收窄到正确的那一处。"""

    pdf = build_pdf(
        tmp_path / "doc.pdf",
        [
            (50, 100, "Airport of Departure"),
            (260, 100, "SHANGHAI"),
            (50, 400, "FCA SHANGHAI"),
        ],
    )
    field_meta = {
        "sfg": FieldMetadata(
            value="SHANGHAI",
            status="confirmed",
            evidence=[Evidence(quote="Airport of Departure")],
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["sfg"])

    assert len(located["sfg"]) == 1
    assert located["sfg"][0].target == "sfg"
    assert located["sfg"][0].bbox[1] < 0.2  # 命中表头那一处，而不是底部的 FCA 行


def test_locate_party_subfields_individually(tmp_path: Path) -> None:
    pdf = build_pdf(
        tmp_path / "doc.pdf",
        [
            (50, 100, "Shipper's Name and Address"),
            (50, 120, "ACME TRADING LTD"),
            (50, 140, "1 MAIN STREET BASEL"),
        ],
    )
    field_meta = {
        "shipper": FieldMetadata(
            value={"name": "ACME TRADING LTD", "address": "1 MAIN STREET BASEL"},
            status="confirmed",
            evidence=[Evidence(quote="Shipper's Name and Address")],
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["shipper"])

    targets = {item.target for item in located["shipper"]}
    assert targets == {"shipper.name", "shipper.address"}
    assert all(item.page == 1 for item in located["shipper"])


def test_locate_uses_raw_value_when_value_cleared(tmp_path: Path) -> None:
    """港口归一化失败后 value 被置空，定位要回落到 raw_value（原文）。"""

    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 100, "SHANGHAI")])
    field_meta = {
        "sfg": FieldMetadata(
            value=None,
            raw_value="SHANGHAI",
            status="needs_review",
            evidence=[Evidence(quote="Airport of Departure: SHANGHAI")],
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["sfg"])

    assert [item.target for item in located["sfg"]] == ["sfg"]


def test_locate_party_subfields_share_region_when_one_fails(tmp_path: Path) -> None:
    """参与人字段整体定位：地址写法对不上时沿用整块区域，不出现「未定位」。"""

    pdf = build_pdf(
        tmp_path / "doc.pdf",
        [
            (50, 100, "Shipper:"),
            (50, 115, "ACME TRADING LTD"),
            (50, 130, "1 MAIN STREET BASEL"),
        ],
    )
    field_meta = {
        "shipper": FieldMetadata(
            value={
                "name": "ACME TRADING LTD",
                "address": "No.1 Main Street, Basel",
                "phone": "",
            },
            status="confirmed",
            evidence=[Evidence(quote="Shipper:")],
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["shipper"])
    targets = {item.target: item for item in located["shipper"]}

    # 空值子项不产出定位（前端也就不会有标记）
    assert set(targets) == {"shipper.name", "shipper.address"}
    # 地址用整块区域兜底：上边界不晚于名称所在行，覆盖发货人这一块
    assert targets["shipper.address"].page == 1
    assert targets["shipper.address"].bbox[1] <= targets["shipper.name"].bbox[1]
    assert targets["shipper.address"].bbox[3] >= targets["shipper.name"].bbox[3]


def test_locate_party_falls_back_to_quote_region(tmp_path: Path) -> None:
    """参与人子值全都匹配不上时，用引用片段的区域作为整块位置。"""

    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 100, "Shipper")])
    field_meta = {
        "shipper": FieldMetadata(
            value={"name": "ZZZ UNKNOWN PARTY"},
            status="needs_review",
            evidence=[Evidence(quote="Shipper")],
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["shipper"])

    assert [item.target for item in located["shipper"]] == ["shipper.name"]
    assert located["shipper"][0].bbox[1] < 0.2


def test_locate_falls_back_to_quote_when_value_rewritten(tmp_path: Path) -> None:
    """值被模型改写（原文是斜杠日期）时，退化为引用片段的位置。"""

    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 100, "Schedule 2026/09/28")])
    field_meta = {
        "hbrq": FieldMetadata(
            value="2099-01-01",
            status="needs_review",
            evidence=[Evidence(quote="Schedule")],
        )
    }

    located = AnalysisService._locate_in_document(pdf, field_meta, ["hbrq"])

    assert len(located["hbrq"]) == 1
    assert located["hbrq"][0].page == 1


def test_ambiguous_value_without_quote_is_skipped(tmp_path: Path) -> None:
    """同一个值出现多处且没有引用可消歧时，宁可不标位置。"""

    pdf = build_pdf(
        tmp_path / "doc.pdf", [(50, 100, "SHANGHAI"), (50, 400, "SHANGHAI")]
    )
    field_meta = {
        "sfg": FieldMetadata(value="SHANGHAI", status="confirmed", evidence=[])
    }

    assert AnalysisService._locate_in_document(pdf, field_meta, ["sfg"]) == {}


def test_locate_skips_missing_values(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "doc.pdf", [(50, 100, "SHANGHAI")])

    located = AnalysisService._locate_in_document(pdf, {}, [])

    assert located == {}


def test_locate_returns_nothing_without_text_layer(tmp_path: Path) -> None:
    pdf = build_image_only_pdf(tmp_path / "scan.pdf")
    field_meta = {
        "sfg": FieldMetadata(
            value="SHANGHAI", status="confirmed", evidence=[Evidence(quote="SHANGHAI")]
        )
    }

    assert AnalysisService._locate_in_document(pdf, field_meta, ["sfg"]) == {}
