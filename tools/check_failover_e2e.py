"""端到端验证模型兜底：模拟主模型（DeepSeek）整体宕机，看千问是否接管。

做法是把主 provider 的 base_url 指向本机不可达端口（连接被拒 ≈ 上游挂了），
其余一切照旧：走的是真实的 `AnalysisService` 全链路（渲染 → VLM 识别 → 字段抽取
→ 结果落盘），因此产出的任务和正常上传的完全一样，能在内置前端里打开看。

用法（项目根目录）：

    uv run python tools/check_failover_e2e.py --files "HSJAE,CAF2407403"
    uv run python tools/check_failover_e2e.py --files "MEX CN" --keep-primary   # 对照组

看三件事：
1. 任务是否仍然 completed（而不是 MODEL_TIMEOUT / ANALYSIS_FAILED）；
2. 日志里有没有 `改用兜底 provider` / `熔断`；
3. 结果字段与对照组（--keep-primary）是否一致。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.schemas.analysis import AnalysisContext
from app.schemas.file import UploadedDocument
from app.schemas.po_order import PO_ORDER_KEYS
from app.service.analysis_service import AnalysisService

# 指向本机不可达端口：连接立即被拒，等价于"上游整家不可用"
DEAD_BASE_URL = "http://127.0.0.1:9"
SUPPORTED = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}
MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

INTERESTING = re.compile(r"provider|熔断|兜底|MODEL_TIMEOUT|EmptyExtraction", re.IGNORECASE)


class LogCollector(logging.Handler):
    """收集关键日志，报告里作为"确实切换过"的证据。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - 日志格式化失败不能影响任务
            return
        if INTERESTING.search(message):
            self.lines.append(f"[{record.levelname}] {message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="模型兜底端到端验证")
    parser.add_argument("--dir", default=r"D:\tengzhenhui\Desktop\托书样例")
    parser.add_argument("--files", default="", help="逗号分隔的文件名关键字；留空取前 N 份")
    parser.add_argument("--limit", type=int, default=2, help="最多跑几份（默认 2）")
    parser.add_argument(
        "--keep-primary",
        action="store_true",
        help="对照组：不动主模型地址，全部走 DeepSeek",
    )
    parser.add_argument("--out", default="data/model_eval/failover_e2e")
    return parser.parse_args()


def build_settings(args: argparse.Namespace) -> Settings:
    """构造配置；非对照组时把主 provider 打到死地址，模拟上游宕机。"""

    overrides: dict[str, Any] = {}
    if not args.keep_primary:
        overrides = {
            "deepseek_base_url": DEAD_BASE_URL,
            # 连接被拒是毫秒级的，超时压小一点让演示更快；这不影响真实故障的表现
            "deepseek_connect_timeout_seconds": 2.0,
            "deepseek_timeout_seconds": 5.0,
            "deepseek_max_retries": 0,
        }
    return Settings(**overrides)


def pick_files(args: argparse.Namespace) -> list[Path]:
    directory = Path(args.dir)
    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED
    )
    if args.files:
        wanted = [re.sub(r"\s+", "", item) for item in args.files.split(",") if item.strip()]
        files = [
            path
            for path in files
            if any(want in re.sub(r"\s+", "", path.name) for want in wanted)
        ]
    return files[: args.limit]


def extract_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """从结果 JSON 里取出字段值映射（结构随版本变，这里做兼容）。"""

    for candidate in (
        payload,
        payload.get("result"),
        payload.get("fields"),
        payload.get("values"),
    ):
        if isinstance(candidate, dict) and any(key in candidate for key in PO_ORDER_KEYS):
            return {
                key: (value.get("value") if isinstance(value, dict) else value)
                for key, value in candidate.items()
                if key in PO_ORDER_KEYS
            }
    return {}


def render_value(value: Any) -> str:
    if value is None:
        return "∅"
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    return text.replace("\n", " ⏎ ")


async def run_case(
    service: AnalysisService, path: Path, index: int
) -> dict[str, Any]:
    """上传 → 建任务 → 跑完整链路 → 取结果。"""

    request_id = f"failover_e2e_{int(time.time())}_{index}"
    upload = UploadedDocument(
        filename=path.name,
        content_type=MIME_BY_SUFFIX.get(path.suffix.lower()),
        content=path.read_bytes(),
    )
    task = await service.create_task(
        upload, request_id, "po_order.v1", AnalysisContext(), auto_start=False
    )
    task_id = str(task["task_id"])
    started = time.monotonic()
    await service.process_task(task_id)
    elapsed = time.monotonic() - started

    status = service.get_task_status(task_id)
    row: dict[str, Any] = {
        "name": path.name,
        "task_id": task_id,
        "status": status.get("status"),
        "stage": status.get("current_stage"),
        "error": status.get("error"),
        "review_fields": status.get("review_fields") or [],
        "elapsed_seconds": elapsed,
        "fields": {},
    }
    try:
        row["fields"] = extract_fields(service.get_result(task_id))
    except FileNotFoundError:
        row["fields"] = {}
    return row


def write_report(
    out_dir: Path, args: argparse.Namespace, rows: list[dict[str, Any]], logs: list[str]
) -> None:
    mode = "对照组（主模型可用）" if args.keep_primary else "模拟主模型宕机"
    lines = [
        "# 模型兜底端到端验证",
        "",
        f"- 时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 模式：{mode}",
        f"- 主模型地址：{'（未改动）' if args.keep_primary else DEAD_BASE_URL}",
        "",
        "## 任务结果",
        "",
        "| 文件 | 任务 ID | 状态 | 用时(s) | 非空字段 | 待人工核对 | 错误 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        non_empty = sum(1 for value in row["fields"].values() if value is not None)
        lines.append(
            f"| {row['name']} | {row['task_id']} | {row['status']} | "
            f"{row['elapsed_seconds']:.1f} | {non_empty}/{len(PO_ORDER_KEYS)} | "
            f"{len(row['review_fields'])} | "
            f"{(row['error'] or {}).get('code', '') if isinstance(row['error'], dict) else ''} |"
        )

    for row in rows:
        lines += ["", f"## {row['name']}", "", "| 字段 | 值 |", "| --- | --- |"]
        for key in PO_ORDER_KEYS:
            lines.append(f"| {key} | {render_value(row['fields'].get(key))} |")

    lines += ["", "## 关键日志（provider 切换 / 熔断证据）", "", "```"]
    lines += logs or ["（没有捕获到 provider 相关日志）"]
    lines += ["```"]

    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "raw.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n报告：{out_dir / 'report.md'}")


async def main() -> int:
    args = parse_args()
    collector = LogCollector()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().addHandler(collector)

    settings = build_settings(args)
    service = AnalysisService(settings)
    print(f"provider 链：{' -> '.join(service.deepseek.provider_names)}")
    if not args.keep_primary:
        print(f"主模型地址已被指向 {DEAD_BASE_URL}（模拟宕机）")

    files = pick_files(args)
    if not files:
        print("没找到可用样例")
        return 1

    rows: list[dict[str, Any]] = []
    for index, path in enumerate(files):
        print(f"\n=== {path.name} ===")
        row = await run_case(service, path, index)
        rows.append(row)
        non_empty = sum(1 for value in row["fields"].values() if value is not None)
        print(
            f"  任务 {row['task_id']}：{row['status']}，{row['elapsed_seconds']:.1f}s，"
            f"非空字段 {non_empty}/{len(PO_ORDER_KEYS)}"
        )
        if isinstance(row["error"], dict) and row["error"]:
            print(f"  错误：{row['error'].get('code')} {row['error'].get('message')}")

    out_dir = Path(args.out) / datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    write_report(out_dir, args, rows, collector.lines)
    if collector.lines:
        print("\n关键日志：")
        for line in collector.lines[-12:]:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
