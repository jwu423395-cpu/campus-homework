"""Validate an extracted task and map semantic answers to current UI choices.

These checks detect incomplete records and input mistakes. They do not certify
that an AI's mathematical reasoning is correct.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from collections import Counter


TYPES = {"single", "multiple", "judgment", "fill", "calculation", "short"}
REVIEW_CHECKS = ("conditions", "answer", "symbols", "units", "language")


class TaskError(ValueError):
    pass


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def normalized_text(value: str) -> str:
    # Do not erase punctuation, minus signs, units or mathematical symbols.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", html.unescape(value))).strip()


def option_key(option: dict) -> tuple[str, str]:
    return (normalized_text(option.get("text", "")), option.get("image_sha256", ""))


def validate_task(task: dict, require_review: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(task, dict):
        return ["Task must be an object"]
    if task.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    assignment = task.get("assignment", {})
    if not isinstance(assignment, dict):
        errors.append("assignment must be an object")
        assignment = {}
    for key in ("id", "title", "course"):
        if not isinstance(assignment.get(key), str) or not assignment[key].strip():
            errors.append(f"assignment.{key} is required")
    if assignment.get("kind") not in {"homework", "mock_exam"}:
        errors.append("Only homework and mock_exam are supported in v0.1")
    questions = task.get("questions")
    if not isinstance(questions, list) or not questions:
        return errors + ["questions must be a nonempty array"]
    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            errors.append(f"Question {index} must be an object")
            continue
        prefix = f"Question {question.get('number', index)}"
        qid = question.get("id")
        if not isinstance(qid, str) or not qid or qid in seen_ids:
            errors.append(f"{prefix}: missing/duplicate id")
        else:
            seen_ids.add(qid)
        number = question.get("number")
        if type(number) is not int or number < 1 or number in seen_numbers:
            errors.append(f"{prefix}: invalid/duplicate number")
        else:
            seen_numbers.add(number)
        if not isinstance(question.get("stem"), str) or not question["stem"].strip():
            errors.append(f"{prefix}: full stem is required")
        kind = question.get("type")
        if kind not in TYPES:
            errors.append(f"{prefix}: unsupported question type")
            continue
        if question.get("delivery", "native") not in {"native", "image"}:
            errors.append(f"{prefix}: delivery must be native or image")
        source = question.get("source", {})
        if not isinstance(source, dict):
            errors.append(f"{prefix}: source must be an object")
            source = {}
        solution = question.get("solution", {})
        if not isinstance(solution, dict):
            errors.append(f"{prefix}: solution must be an object")
            solution = {}
        final = solution.get("final", {})
        if not isinstance(final, dict):
            errors.append(f"{prefix}: solution.final must be an object")
            final = {}
        for field in ("text", "tex"):
            if field in final and not isinstance(final[field], str):
                errors.append(f"{prefix}: final.{field} must be a string")
        steps_required = require_review and kind in {"calculation", "short"}
        if "steps" in solution or steps_required:
            steps = solution.get("steps", [])
            if not isinstance(steps, list) or (steps_required and not steps):
                errors.append(f"{prefix}: explanation/steps must be an array of valid steps")
            else:
                for index, step in enumerate(steps, 1):
                    if not isinstance(step, dict):
                        errors.append(f"{prefix}: step {index} must be an object")
                        continue
                    for field in ("text", "tex"):
                        if field in step and not isinstance(step[field], str):
                            errors.append(f"{prefix}: step {index}.{field} must be a string")
                    if not any(isinstance(step.get(k), str) and step[k].strip() for k in ("text", "tex")):
                        errors.append(f"{prefix}: step {index} needs nonempty text/tex")
        if kind in {"single", "multiple"}:
            options = question.get("options", [])
            if not isinstance(options, list) or len(options) < 2:
                errors.append(f"{prefix}: at least two options required")
                options = []
            ids, keys = [], []
            for option in options:
                if not isinstance(option, dict):
                    errors.append(f"{prefix}: options must be objects")
                    continue
                oid = option.get("id")
                if not isinstance(oid, str) or not oid:
                    errors.append(f"{prefix}: each option needs an id")
                else:
                    ids.append(oid)
                if not isinstance(option.get("text", ""), str):
                    errors.append(f"{prefix}: option text must be a string")
                    continue
                key = option_key(option)
                if not any(key):
                    errors.append(f"{prefix}: option needs text or image_sha256")
                keys.append(key)
            if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
                errors.append(f"{prefix}: ambiguous/duplicate options")
            selected = final.get("option_ids", [])
            if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected):
                errors.append(f"{prefix}: option_ids must be an array of strings")
                selected = []
            if selected and (len(selected) != len(set(selected)) or not set(selected) <= set(ids)):
                errors.append(f"{prefix}: invalid selected option ids")
            if kind == "single" and selected and len(selected) != 1:
                errors.append(f"{prefix}: single choice must have exactly one answer")
            if require_review and not selected:
                errors.append(f"{prefix}: answer is missing")
        elif kind == "judgment":
            if (require_review or "value" in final) and type(final.get("value")) is not bool:
                errors.append(f"{prefix}: judgment answer must be a boolean")
        elif kind == "fill":
            count = question.get("blank_count")
            if type(count) is not int or count < 1:
                errors.append(f"{prefix}: blank_count must be a positive integer")
            values = final.get("values", [])
            if not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values):
                errors.append(f"{prefix}: fill values must be nonempty strings")
            elif (require_review or values) and len(values) != count:
                errors.append(f"{prefix}: answer count differs from blank_count")
        elif require_review:
            if not any(isinstance(final.get(k), str) and final[k].strip() for k in ("text", "tex")):
                errors.append(f"{prefix}: final text/tex answer is required")
        if require_review:
            if source.get("complete") is not True:
                errors.append(f"{prefix}: source is incomplete; do not guess")
            review = question.get("review", {})
            if not isinstance(review, dict):
                review = {}
            if review.get("status") != "verified":
                errors.append(f"{prefix}: independent review not complete")
            for field in ("reviewer", "method"):
                if not isinstance(review.get(field), str) or not review[field].strip():
                    errors.append(f"{prefix}: review.{field} is required")
            checks = review.get("checks", {})
            if not isinstance(checks, dict) or any(checks.get(k) is not True for k in REVIEW_CHECKS):
                errors.append(f"{prefix}: conditions/answer/symbols/units/language checks must pass")
    return errors


def require_valid(task: dict, require_review: bool = False) -> None:
    errors = validate_task(task, require_review)
    if errors:
        raise TaskError("\n".join(errors))


def match_options(question: dict, live_options: list[dict]) -> list[str]:
    """Return *visible* labels after checking the entire current option set."""
    expected = question.get("options", [])
    if question.get("type") not in {"single", "multiple"}:
        raise TaskError("Option mapping requires a single/multiple question")
    expected_keys = [option_key(o) for o in expected]
    live_keys = [option_key(o) for o in live_options]
    if Counter(expected_keys) != Counter(live_keys):
        raise TaskError("Current options differ from extracted question; re-read the question")
    if len(set(live_keys)) != len(live_keys):
        raise TaskError("Duplicate option content; inspect the current question visually")
    labels = [o.get("label") for o in live_options]
    if any(not isinstance(x, str) or not x for x in labels) or len(labels) != len(set(labels)):
        raise TaskError("Current visible labels are missing or ambiguous")
    chosen = question["solution"]["final"]["option_ids"]
    selected_keys = {option_key(o) for o in expected if o["id"] in chosen}
    if len(selected_keys) != len(chosen):
        raise TaskError("Selected option ids are invalid")
    return [o["label"] for o in live_options if option_key(o) in selected_keys]


def canonical_answer(question: dict, final: dict | None = None) -> dict:
    answer = final if final is not None else question["solution"]["final"]
    if question["type"] in {"single", "multiple"}:
        return {"option_ids": sorted(answer.get("option_ids", []))}
    if question["type"] == "judgment":
        return {"value": answer.get("value")}
    if question["type"] == "fill":
        return {"values": answer.get("values", [])}
    return {k: answer[k] for k in ("text", "tex") if k in answer}
