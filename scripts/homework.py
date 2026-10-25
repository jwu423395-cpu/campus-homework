#!/usr/bin/env python3
"""Local JSON/graphics helpers. Codex supplies perception and reasoning."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path

from campus_homework.model import TaskError, match_options, require_valid
from campus_homework.state import RunStore


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("styles")
    profile = commands.add_parser("profile")
    profile.add_argument("--set", metavar="JSON_FILE")
    validate = commands.add_parser("validate")
    validate.add_argument("task")
    validate.add_argument("--reviewed", action="store_true")
    options = commands.add_parser("options")
    options.add_argument("task")
    options.add_argument("question_id")
    options.add_argument("live_options")
    math = commands.add_parser("math-check")
    math.add_argument("left")
    math.add_argument("right")
    math.add_argument("--symbols", nargs="*", default=[])
    render = commands.add_parser("render")
    render.add_argument("task")
    render.add_argument("--output", required=True)
    render.add_argument("--profile")
    new = commands.add_parser("run-new")
    new.add_argument("task")
    new.add_argument("--path")
    update = commands.add_parser("run-update")
    update.add_argument("run")
    update.add_argument("task")
    for name in ("run-show", "run-gate", "run-begin"):
        sub = commands.add_parser(name)
        sub.add_argument("run")
    artifacts = commands.add_parser("run-artifacts")
    artifacts.add_argument("run")
    artifacts.add_argument("question_id")
    artifacts.add_argument("files", nargs="+")
    filled = commands.add_parser("run-filled")
    filled.add_argument("run")
    filled.add_argument("question_id")
    filled.add_argument("actual")
    filled.add_argument("--evidence", required=True)
    for name in ("run-saved", "run-submitted", "run-reconcile"):
        sub = commands.add_parser(name)
        sub.add_argument("run")
        sub.add_argument("--evidence", required=True)
        sub.add_argument("--message", required=True)
        if name == "run-submitted":
            sub.add_argument("--time", required=True)
            sub.add_argument("--score", type=float)
    args = parser.parse_args()
    command = args.command
    try:
        if command == "doctor":
            from campus_homework.environment import doctor
            result = doctor()
        elif command == "styles":
            from campus_homework.rendering import style_catalog
            result = style_catalog()
        elif command == "profile":
            from campus_homework.environment import load_profile, save_profile, local_profile_path
            if args.set:
                save_profile(read_json(args.set))
            result = {"path": str(local_profile_path()), "profile": load_profile()}
        elif command == "validate":
            task = read_json(args.task)
            require_valid(task, args.reviewed)
            result = {"ok": True, "questions": len(task["questions"]),
                      "review_records_checked": args.reviewed,
                      "note": "Record validation does not prove mathematical correctness."}
        elif command == "options":
            task = read_json(args.task)
            require_valid(task, True)
            question = next((q for q in task["questions"] if q["id"] == args.question_id), None)
            if question is None:
                raise TaskError("Unknown question id")
            result = {"question_id": args.question_id,
                      "visible_labels": match_options(question, read_json(args.live_options))}
        elif command == "math-check":
            from campus_homework.mathcheck import check_equivalence
            result = check_equivalence(args.left, args.right, args.symbols)
        elif command == "render":
            from campus_homework.environment import load_profile
            from campus_homework.rendering import render_document
            task_path = Path(args.task).resolve()
            task = copy.deepcopy(read_json(task_path))
            require_valid(task, True)
            for page in task.get("workbook", {}).get("pages", []):
                source = Path(page["image"])
                if not source.is_absolute():
                    page["image"] = str((task_path.parent / source).resolve())
            profile = read_json(args.profile) if args.profile else load_profile()
            if args.profile:
                profile_base = Path(args.profile).resolve().parent
                for field in ("photo_background", "font"):
                    value = profile.get(field)
                    if isinstance(value, str) and value and value not in {"wenkai", "yozai", "chenyu"}:
                        candidate = Path(value).expanduser()
                        if not candidate.is_absolute():
                            profile[field] = str((profile_base / candidate).resolve())
            result = render_document(task, Path(args.output).resolve(), profile)
        elif command == "run-new":
            if args.path:
                path = Path(args.path)
            else:
                from campus_homework.environment import local_profile_path
                from uuid import uuid4
                path = local_profile_path().parent / "runs" / (
                    datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8]) / "run.json"
            store = RunStore.create(path, read_json(args.task))
            result = {"path": str(store.path.resolve()), "status": store.read()["status"]}
        else:
            store = RunStore(args.run)
            if command == "run-update":
                store.replace_reviewed_task(read_json(args.task))
            elif command == "run-artifacts":
                store.register_artifacts(args.question_id, args.files)
            elif command == "run-filled":
                store.record_filled(args.question_id, read_json(args.actual), args.evidence)
            elif command == "run-saved":
                store.save_verified(args.evidence, args.message)
            elif command == "run-gate":
                result = store.submission_gate()
            elif command == "run-begin":
                store.begin_submission()
            elif command == "run-submitted":
                store.confirm_submission(args.evidence, args.message, args.time, args.score)
            elif command == "run-reconcile":
                store.reconcile_not_submitted(args.evidence, args.message)
            if command != "run-gate":
                result = store.read()
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (TaskError, ValueError, OSError, KeyError, StopIteration, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
