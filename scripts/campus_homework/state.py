"""Local task journal with readback, save and submission gates."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .model import TaskError, canonical_answer, digest, require_valid
from .fileio import replace_file


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        replace_file(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def file_evidence(path: str | Path) -> dict:
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise TaskError("Evidence must be a file")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class RunStore:
    """One journal writer: Codex's sole UI operator. No credentials stored."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @classmethod
    def create(cls, path: str | Path, task: dict) -> "RunStore":
        require_valid(task)
        store = cls(path)
        if store.path.exists():
            raise TaskError("Run already exists; inspect/resume it instead")
        document = {"schema_version": 1, "task": copy.deepcopy(task),
                    "task_sha256": digest(task), "status": "selected", "revision": 0,
                    "saved_revision": None, "created_at": now(), "events": [],
                    "questions": {q["id"]: {"status": "pending", "artifacts": []}
                                  for q in task["questions"]}}
        write_json(store.path, document)
        return store

    def read(self) -> dict:
        result = json.loads(self.path.read_text(encoding="utf-8"))
        if digest(result["task"]) != result["task_sha256"]:
            raise TaskError("Task content changed outside the journal; re-extract/review in a new run")
        return result

    def _write(self, data: dict, event: str) -> None:
        data["events"].append({"at": now(), "event": event})
        write_json(self.path, data)

    def _question(self, data: dict, qid: str) -> dict:
        matches = [q for q in data["task"]["questions"] if q["id"] == qid]
        if not matches:
            raise TaskError("Unknown question id")
        return matches[0]

    def _editable(self, data: dict) -> None:
        if data["status"] in {"submitting", "submitted"}:
            raise TaskError("Submission pending/complete; reconcile visible platform status before further actions")

    def replace_reviewed_task(self, task: dict) -> None:
        """Accept newly solved records, invalidate readbacks on any content change."""
        require_valid(task)
        data = self.read()
        self._editable(data)
        old = data["task"]
        if old["assignment"] != task["assignment"]:
            raise TaskError("Assignment identity changed; create a new run")
        old_ids = {q["id"] for q in old["questions"]}
        if old_ids != {q["id"] for q in task["questions"]}:
            raise TaskError("Question set changed; create a new run")
        if digest(old) == digest(task):
            return
        workbook_changed = digest(old.get("workbook")) != digest(task.get("workbook"))
        old_by_id = {q["id"]: q for q in old["questions"]}
        for question in task["questions"]:
            if workbook_changed or digest(old_by_id[question["id"]]) != digest(question):
                data["questions"][question["id"]] = {"status": "pending", "artifacts": []}
        data["task"] = copy.deepcopy(task)
        data["task_sha256"] = digest(task)
        data["revision"] += 1
        data["saved_revision"] = None
        data["status"] = "in_progress"
        self._write(data, "task_updated_readback_invalidated")

    def register_artifacts(self, qid: str, paths: list[str]) -> None:
        data = self.read()
        self._editable(data)
        self._question(data, qid)
        if not paths:
            raise TaskError("At least one upload artifact is required")
        artifacts = [file_evidence(path) for path in paths]
        if len({item["path"] for item in artifacts}) != len(artifacts):
            raise TaskError("Duplicate upload artifact")
        data["questions"][qid] = {"status": "pending", "artifacts": artifacts}
        data["revision"] += 1
        data["saved_revision"] = None
        data["status"] = "in_progress"
        self._write(data, f"artifacts_registered:{qid}")

    def record_filled(self, qid: str, actual: dict, evidence_path: str) -> None:
        data = self.read()
        self._editable(data)
        question = self._question(data, qid)
        require_valid({"schema_version": 1, "assignment": data["task"]["assignment"],
                       "questions": [question]}, require_review=True)
        if digest(canonical_answer(question, actual)) != digest(canonical_answer(question)):
            raise TaskError("Visible answer differs from reviewed answer; do not continue")
        artifacts = data["questions"][qid]["artifacts"]
        if question.get("delivery") == "image":
            if not artifacts:
                raise TaskError("Image answer has no registered artifacts")
            for artifact in artifacts:
                if file_evidence(artifact["path"])["sha256"] != artifact["sha256"]:
                    raise TaskError("Upload image changed since registration")
            if actual.get("uploaded_sha256") != [item["sha256"] for item in artifacts]:
                raise TaskError("Uploaded attachments/order have not been verified")
        record = data["questions"][qid]
        record.update({"status": "filled", "actual": actual,
                       "evidence": file_evidence(evidence_path), "verified_at": now()})
        data["revision"] += 1
        data["saved_revision"] = None
        data["status"] = "in_progress"
        self._write(data, f"filled_readback_verified:{qid}")

    def save_verified(self, evidence_path: str, visible_message: str) -> None:
        data = self.read()
        self._editable(data)
        if not visible_message.strip():
            raise TaskError("Visible save result is required")
        require_valid(data["task"], require_review=True)
        if any(q["status"] != "filled" for q in data["questions"].values()):
            raise TaskError("Some questions have not been filled/read back")
        data["save_evidence"] = file_evidence(evidence_path) | {"message": visible_message}
        data["saved_revision"] = data["revision"]
        data["status"] = "saved"
        self._write(data, "saved_and_verified")

    def submission_gate(self) -> dict:
        data = self.read()
        require_valid(data["task"], require_review=True)
        if data["status"] != "saved" or data["saved_revision"] != data["revision"]:
            raise TaskError("Current answers are not in a verified saved state")
        for question in data["task"]["questions"]:
            record = data["questions"][question["id"]]
            if record["status"] != "filled" or digest(canonical_answer(question, record["actual"])) != digest(canonical_answer(question)):
                raise TaskError("Question readback missing or changed")
            for artifact in record["artifacts"]:
                if file_evidence(artifact["path"])["sha256"] != artifact["sha256"]:
                    raise TaskError("An attachment changed after save")
        return {"ready": True, "assignment": data["task"]["assignment"],
                "question_count": len(data["questions"]), "revision": data["revision"]}

    def begin_submission(self) -> None:
        self.submission_gate()
        data = self.read()
        data["status"] = "submitting"
        self._write(data, "submission_started_check_platform_before_retry")

    def confirm_submission(self, evidence_path: str, visible_message: str,
                           submitted_at: str, score: float | None = None) -> None:
        data = self.read()
        if data["status"] != "submitting":
            raise TaskError("Submission was not started or was already recorded")
        if not visible_message.strip() or not submitted_at.strip():
            raise TaskError("Visible submission result and time are required")
        data["submission"] = file_evidence(evidence_path) | {
            "message": visible_message, "submitted_at": submitted_at, "score": score}
        data["status"] = "submitted"
        self._write(data, "submission_confirmed")

    def reconcile_not_submitted(self, evidence_path: str, visible_message: str) -> None:
        data = self.read()
        if data["status"] != "submitting" or not visible_message.strip():
            raise TaskError("Reconciliation requires a pending submission and visible status")
        data["reconcile_evidence"] = file_evidence(evidence_path) | {"message": visible_message}
        data["status"] = "in_progress"
        data["saved_revision"] = None
        self._write(data, "platform_confirms_not_submitted_resave_required")
