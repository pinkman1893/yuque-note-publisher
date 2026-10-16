#!/usr/bin/env python3
"""Create or append the managed, visible update-history table in a Markdown note."""

import argparse
import datetime as dt
import pathlib
import re
import sys

try:
    from .runtime import configure_stdio
except ImportError:
    from runtime import configure_stdio

START = "<!-- yuque-note-publisher:update-history:start -->"
END = "<!-- yuque-note-publisher:update-history:end -->"
HEADER = "## 更新记录\n\n| 日期 | 类型 | 说明 |\n| --- | --- | --- |"


def valid_iso_date(value):
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format")
    return value


def clean_cell(value):
    return " ".join(value.replace("|", "\\|").splitlines()).strip()


def update_history(text, date, entry_type, summary, allow_maintenance=False):
    if entry_type == "格式维护" and not allow_maintenance:
        raise ValueError("Maintenance is not learning history; explicit --allow-maintenance is required")
    if any(phrase in summary for phrase in (
        "按用户要求", "根据用户提供", "经用户确认", "已帮你", "AI 建议修改", "使用用户补充",
    )):
        raise ValueError("History must use the author's voice, not an assistant's work log")
    row = "| {} | {} | {} |".format(
        clean_cell(date), clean_cell(entry_type), clean_cell(summary)
    )
    if START in text or END in text:
        if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
            raise ValueError("Malformed managed update-history markers")
        before, remainder = text.split(START, 1)
        block, after = remainder.split(END, 1)
        if row in block:
            return text, False
        updated_block = block.rstrip() + "\n" + row + "\n"
        return before + START + updated_block + END + after, True

    if re.search(r"(?m)^#{1,6}\s+更新记录\s*$", text):
        raise ValueError("An unmanaged 更新记录 section already exists; append compatibly by hand")

    block = "{}\n{}\n{}\n".format(START, HEADER, row) + END
    return text.rstrip() + "\n\n" + block + "\n", True


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file")
    parser.add_argument("--date", required=True, type=valid_iso_date)
    parser.add_argument("--type", required=True, choices=["首次发布", "内容更新", "格式维护"])
    parser.add_argument("--summary", required=True)
    parser.add_argument("--allow-maintenance", action="store_true", help="only for a maintenance entry the user explicitly requested")
    args = parser.parse_args()
    path = pathlib.Path(args.file)
    try:
        original = path.read_text(encoding="utf-8")
        updated, changed = update_history(original, args.date, args.type, args.summary, args.allow_maintenance)
        if changed:
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(updated, encoding="utf-8")
            temp.replace(path)
        print("updated" if changed else "unchanged")
        return 0
    except Exception as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
