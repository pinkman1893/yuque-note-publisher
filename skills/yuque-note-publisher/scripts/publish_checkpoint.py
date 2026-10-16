#!/usr/bin/env python3
"""Persist a non-secret Yuque publishing checkpoint across Codex conversations."""

import argparse
import datetime as dt
import json
import pathlib
import sys
from collections import Counter

try:
    from .runtime import configure_stdio, state_dir
except ImportError:
    from runtime import configure_stdio, state_dir


DEFAULT_STATE_FILE = state_dir() / "checkpoint.json"
BATCH_SIZE = 3
STATUSES = (
    "planned",
    "fetching",
    "awaiting-confirmation",
    "quota-paused",
    "ready-to-push",
    "ready-to-clear",
)


def now_utc():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {"schema_version": 2, "pending": None}
    pending = data.get("pending")
    if pending:
        pending.setdefault("batch_size", BATCH_SIZE)
        pending.setdefault("remaining_selections", list(pending.get("selections", [])))
        pending.setdefault("completed_selections", [])
        pending.setdefault("current_round", 1)
        refresh_batch(pending)
    data["schema_version"] = 2
    return data


def refresh_batch(pending):
    size = int(pending.get("batch_size", BATCH_SIZE))
    pending["current_batch"] = list(pending.get("remaining_selections", []))[:size]


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show")

    start = subparsers.add_parser("start")
    start.add_argument("--selection", action="append", required=True)
    start.add_argument("--github-repo", required=True, help="explicit destination owner/repository")
    start.add_argument("--status", choices=STATUSES, default="planned")
    start.add_argument("--reason")
    start.add_argument("--source-url")
    start.add_argument("--source-repo")
    start.add_argument("--doc-id")
    start.add_argument("--target-path")
    start.add_argument(
        "--replace-existing",
        action="store_true",
        help="replace an existing pending checkpoint after explicit user approval",
    )

    update = subparsers.add_parser("update")
    update.add_argument("--status", choices=STATUSES)
    update.add_argument("--reason")
    update.add_argument("--source-url")
    update.add_argument("--source-repo")
    update.add_argument("--doc-id")
    update.add_argument("--target-path")

    advance = subparsers.add_parser("advance")
    advance.add_argument("--completed-selection", action="append", required=True)

    drop = subparsers.add_parser("drop")
    drop.add_argument("--selection", action="append", required=True)

    clear = subparsers.add_parser("clear")
    clear.add_argument(
        "--force",
        action="store_true",
        help="clear an unfinished checkpoint after explicit user cancellation",
    )
    args = parser.parse_args()
    path = pathlib.Path(args.state_file).expanduser()

    try:
        data = load(path)
        if args.command == "show":
            pass
        elif args.command == "start":
            if len(set(args.selection)) != len(args.selection):
                raise RuntimeError("Duplicate selections are not allowed")
            if data.get("pending") and not args.replace_existing:
                raise RuntimeError(
                    "A pending publishing checkpoint already exists; resume it or use "
                    "--replace-existing only after explicit user approval"
                )
            timestamp = now_utc()
            data = {
                "schema_version": 2,
                "pending": {
                    "selections": args.selection,
                    "remaining_selections": list(args.selection),
                    "completed_selections": [],
                    "batch_size": BATCH_SIZE,
                    "current_round": 1,
                    "current_batch": list(args.selection)[:BATCH_SIZE],
                    "github_repo": args.github_repo,
                    "status": args.status,
                    "reason": args.reason,
                    "source_url": args.source_url,
                    "source_repo": args.source_repo,
                    "doc_id": args.doc_id,
                    "target_path": args.target_path,
                    "started_at": timestamp,
                    "updated_at": timestamp,
                },
            }
            save(path, data)
        elif args.command == "update":
            pending = data.get("pending")
            if not pending:
                raise RuntimeError("No pending publishing checkpoint exists")
            for name in ("status", "reason", "source_url", "source_repo", "doc_id", "target_path"):
                value = getattr(args, name)
                if value is not None:
                    pending[name] = value
            pending["updated_at"] = now_utc()
            refresh_batch(pending)
            save(path, data)
        elif args.command == "advance":
            pending = data.get("pending")
            if not pending:
                raise RuntimeError("No pending publishing checkpoint exists")
            expected = pending.get("current_batch", [])
            completed = args.completed_selection
            if Counter(completed) != Counter(expected):
                raise RuntimeError(
                    "Completed selections must exactly match the current batch: {}".format(expected)
                )
            for selection in expected:
                if selection in pending["remaining_selections"]:
                    pending["remaining_selections"].remove(selection)
                if selection not in pending["completed_selections"]:
                    pending["completed_selections"].append(selection)
            if pending["remaining_selections"]:
                pending["current_round"] = int(pending.get("current_round", 1)) + 1
                pending["status"] = "planned"
                pending["reason"] = None
            else:
                pending["status"] = "ready-to-clear"
            pending["updated_at"] = now_utc()
            refresh_batch(pending)
            save(path, data)
        elif args.command == "drop":
            pending = data.get("pending")
            if not pending:
                raise RuntimeError("No pending publishing checkpoint exists")
            for selection in args.selection:
                while selection in pending["remaining_selections"]:
                    pending["remaining_selections"].remove(selection)
                while selection in pending["selections"]:
                    pending["selections"].remove(selection)
            if not pending["remaining_selections"]:
                pending["status"] = "ready-to-clear"
                pending["reason"] = None
            pending["updated_at"] = now_utc()
            refresh_batch(pending)
            save(path, data)
        else:
            pending = data.get("pending")
            if pending and pending.get("status") != "ready-to-clear" and not args.force:
                raise RuntimeError(
                    "Checkpoint is not ready to clear; use --force only after explicit user cancellation"
                )
            data = {"schema_version": 2, "pending": None}
            save(path, data)
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except Exception as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
