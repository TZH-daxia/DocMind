"""兜底模型（通义千问）能力验收脚本。

用法（在项目根目录）：
    uv run python tools/eval_fallback_model.py --smoke
    uv run python tools/eval_fallback_model.py --dir "D:\\...\\托书样例" --limit 4
    uv run python tools/eval_fallback_model.py --dir "..." --files "11.png" --compare

分别验收三件事（与生产链路用的是同一套提示词/图片切分/schema）：
1. 连通性：一次纯文本调用，确认模型 ID 与 Key 可用；
2. 图片理解：整页 + 四象限共 5 张图，按生产提示词逐字转写；
3. 结构化输出：PoOrderExtraction（13 字段）走 provider 自己的结构化方式。

`--compare` 时同一样例再用主 provider 跑一遍，逐字段 diff 并统计耗时。
报告写到 `data/model_eval/<时间戳>/`。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 直接 `python tools/eval_fallback_model.py` 运行时，脚本目录在 sys.path 首位，
# 项目根不在，因此显式补上，保证 `import app.*` 生效
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.deepseek_extractor import ImageInput, ProviderClient
from app.agent.providers import build_providers
from app.config import Settings
from app.prompts.loader import (
    load_po_order_extraction_prompt,
    load_po_order_vision_prompt,
)
from app.schemas.po_order import PO_ORDER_KEYS
from app.service.analysis_service import AnalysisService

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
DOC_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}


@dataclass
class RunResult:
    """单个 provider 在单份样例上的表现。"""

    provider: str
    model: str
    vision_seconds: float = 0.0
    extract_seconds: float = 0.0
    vision_chars: int = 0
    vision_text: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    evidence_counts: dict[str, int] = field(default_factory=dict)
    structured_ok: bool = False
    error: str = ""


def _squeeze(text: str) -> str:
    """去掉所有空白字符（含 NBSP），用于宽松的文件名匹配。"""

    return re.sub(r"\s+", "", text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="兜底模型能力验收")
    parser.add_argument("--dir", default=r"D:\tengzhenhui\Desktop\托书样例")
    parser.add_argument("--files", nargs="*", default=None, help="只跑指定文件名")
    parser.add_argument("--limit", type=int, default=0, help="最多跑几份（0 = 全部）")
    parser.add_argument("--compare", action="store_true", help="同时跑主 provider 并对比")
    parser.add_argument("--smoke", action="store_true", help="只做连通性与结构化冒烟")
    parser.add_argument("--out", default="data/model_eval")
    return parser.parse_args()


def load_images(service: AnalysisService, path: Path) -> list[ImageInput]:
    """按生产口径组图：第一页整页 + 四象限切分（图片样例直接当整页）。"""

    if path.suffix.lower() in IMAGE_SUFFIXES:
        content = path.read_bytes()
        images = [
            ImageInput(content=content, media_type="image/png", name=path.name)
        ]
        images.extend(service._split_page_images(content, path.name))
        return images
    rendered = service.renderer.render(
        path, path.name, f"eval_{int(time.time() * 1000) % 100000}"
    )
    return service._load_images([str(item) for item in rendered.image_paths])


async def run_one(
    service: AnalysisService,
    client: ProviderClient,
    images: list[ImageInput],
    need_vision: bool = True,
) -> RunResult:
    """对一份样例跑图片识别 + 字段抽取。"""

    result = RunResult(provider=client.provider.name, model=client.provider.model)
    vision_text = ""
    if need_vision:
        started = time.monotonic()
        try:
            vision_text = await client.describe_images(load_po_order_vision_prompt(), images)
        except Exception as exc:  # noqa: BLE001 - 验收脚本要如实记录失败
            result.error = f"视觉识别失败：{type(exc).__name__}: {exc}"
            return result
        result.vision_seconds = time.monotonic() - started
        result.vision_chars = len(vision_text)
        result.vision_text = vision_text
    if not vision_text.strip():
        result.error = "视觉识别返回空内容，跳过抽取"
        return result

    started = time.monotonic()
    try:
        candidates = await client.extract(
            system_prompt=load_po_order_extraction_prompt(),
            vlm_image_content=vision_text,
            context={},
        )
    except Exception as exc:  # noqa: BLE001
        result.error = f"字段抽取失败：{type(exc).__name__}: {exc}"
        return result
    result.extract_seconds = time.monotonic() - started
    # structured_chain 被置空说明结构化输出失败、已降级 free-form
    result.structured_ok = client.structured_chain is not None
    for candidate in candidates:
        result.fields[candidate.field_key] = candidate.value
        result.evidence_counts[candidate.field_key] = len(candidate.evidence or [])
    return result


def render_value(value: Any) -> str:
    if value is None:
        return "∅"
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    return text.replace("\n", " ⏎ ")


def write_report(out_dir: Path, args: argparse.Namespace, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# 兜底模型能力验收报告",
        "",
        f"- 时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 样例目录：{args.dir}",
        f"- provider：{rows[0]['providers'] if rows else ''}",
        "",
        "## 汇总",
        "",
        "| 样例 | provider | 模型 | 视觉耗时(s) | 抽取耗时(s) | 转写字数 | 结构化 | 非空字段 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        for item in row["results"]:
            non_empty = sum(1 for value in item["fields"].values() if value is not None)
            lines.append(
                "| {name} | {provider} | {model} | {vs:.1f} | {es:.1f} | {chars} | {structured} "
                "| {non_empty}/{total} | {error} |".format(
                    name=row["name"],
                    provider=item["provider"],
                    model=item["model"],
                    vs=item["vision_seconds"],
                    es=item["extract_seconds"],
                    chars=item["vision_chars"],
                    structured="是" if item["structured_ok"] else "否(降级)",
                    non_empty=non_empty,
                    total=len(PO_ORDER_KEYS),
                    error=item["error"] or "",
                )
            )

    for row in rows:
        lines += ["", f"## {row['name']}", ""]
        for item in row["results"]:
            lines += [
                f"### {item['provider']} / {item['model']}",
                "",
                f"- 视觉耗时：{item['vision_seconds']:.1f}s，转写 {item['vision_chars']} 字",
                f"- 抽取耗时：{item['extract_seconds']:.1f}s，结构化输出：{'是' if item['structured_ok'] else '否（已降级 free-form）'}",
            ]
            if item["error"]:
                lines.append(f"- 失败：{item['error']}")
            lines += ["", "| 字段 | 值 | evidence 条数 |", "| --- | --- | --- |"]
            for key in PO_ORDER_KEYS:
                lines.append(
                    f"| {key} | {render_value(item['fields'].get(key))} | "
                    f"{item['evidence_counts'].get(key, 0)} |"
                )
            lines.append("")

        if len(row["results"]) == 2:
            main, fallback = row["results"]
            lines += [
                f"### 逐字段对比（{main['provider']} vs {fallback['provider']}）",
                "",
                "| 字段 | 主 provider | 兜底 provider | 一致 |",
                "| --- | --- | --- | --- |",
            ]
            for key in PO_ORDER_KEYS:
                left = render_value(main["fields"].get(key))
                right = render_value(fallback["fields"].get(key))
                same = "✅" if left == right else "❌"
                lines.append(f"| {key} | {left} | {right} | {same} |")
            lines.append("")

    report_path = out_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "raw.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"报告已写入：{report_path}")


async def main() -> int:
    args = parse_args()
    settings = Settings()
    providers = build_providers(settings)
    print("provider 链：" + " -> ".join(f"{p.name}/{p.model}" for p in providers))

    run_providers = providers if args.compare else providers[-1:]
    if args.smoke:
        run_providers = providers[-1:]
    service = AnalysisService(settings)
    clients = [
        ProviderClient(provider)
        for provider in run_providers
    ]

    if args.smoke:
        return await run_smoke(clients)

    directory = Path(args.dir)
    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES | DOC_SUFFIXES
    )
    if args.files:
        # 按子串匹配；同时支持逗号分隔与多个参数两种写法。
        # 比较前统一去掉空白：Windows/编辑器给文件名里的空格有时是 NBSP，
        # 直接 in 比较会莫名匹配不上
        wanted = [
            _squeeze(item)
            for chunk in args.files
            for item in chunk.split(",")
            if item.strip()
        ]
        print(f"筛选关键字：{wanted}")
        files = [
            path for path in files if any(want in _squeeze(path.name) for want in wanted)
        ]
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print("没找到可用的样例文件；目录下候选：")
        for path in sorted(directory.iterdir()):
            if path.is_file():
                print(f"  - {path.name}")
        return 1

    out_dir = Path(args.out) / datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for path in files:
        print(f"\n=== {path.name} ===")
        try:
            images = load_images(service, path)
        except Exception as exc:  # noqa: BLE001
            print(f"  渲染失败：{type(exc).__name__}: {exc}")
            rows.append(
                {
                    "name": path.name,
                    "providers": ",".join(p.name for p in run_providers),
                    "results": [
                        {
                            "provider": client.provider.name,
                            "model": client.provider.model,
                            "vision_seconds": 0.0,
                            "extract_seconds": 0.0,
                            "vision_chars": 0,
                            "fields": {},
                            "evidence_counts": {},
                            "structured_ok": False,
                            "error": f"渲染失败：{type(exc).__name__}: {exc}",
                        }
                        for client in clients
                    ],
                }
            )
            continue
        print(f"  送模型图片数：{len(images)}")
        results = []
        for client in clients:
            item = await run_one(service, client, images)
            results.append(item)
            print(
                f"  [{client.provider.name}] 视觉 {item.vision_seconds:.1f}s / "
                f"{item.vision_chars} 字，抽取 {item.extract_seconds:.1f}s，"
                f"结构化={'是' if item.structured_ok else '否'}，"
                f"非空字段={sum(1 for v in item.fields.values() if v is not None)}"
                + (f"，错误：{item.error}" if item.error else "")
            )
            if item.vision_text:
                (out_dir / f"{path.stem}.{client.provider.name}.vision.md").write_text(
                    item.vision_text, encoding="utf-8"
                )
        rows.append(
            {
                "name": path.name,
                "providers": ",".join(p.name for p in run_providers),
                "results": [item.__dict__ for item in results],
            }
        )

    write_report(out_dir, args, rows)
    return 0


async def run_smoke(clients: list[ProviderClient]) -> int:
    """冒烟：一次纯文本调用 + 一次结构化输出，确认全链路可用。"""

    for client in clients:
        print(f"\n=== {client.provider.name} / {client.provider.model} ===")
        started = time.monotonic()
        try:
            text = await client.model.ainvoke("用一句话回答：什么是空运托书？")
            print(f"  文本调用 OK（{time.monotonic() - started:.1f}s）：{text.content[:80]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  文本调用失败：{type(exc).__name__}: {exc}")
            continue
        started = time.monotonic()
        try:
            candidates = await client.extract(
                system_prompt="只依据给定文本抽取，没有的字段填 missing。",
                vlm_image_content=(
                    "### 整页版面描述\n空运托书。Shipper: ACME LTD。"
                    "Airport of Departure: SHANGHAI。Airport of Destination: FRANKFURT。"
                    "件数 No of Packages: 169。Gross Weight: 3109 KG。CBM: 15.2。"
                ),
                context={},
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  抽取失败：{type(exc).__name__}: {exc}")
            continue
        with_value = [c.field_key for c in candidates if c.value is not None]
        print(
            f"  结构化抽取 OK（{time.monotonic() - started:.1f}s，"
            f"结构化={'是' if client.structured_chain is not None else '否(降级)'}）："
            f"非空字段 {with_value}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
