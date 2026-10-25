import copy
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from campus_homework.model import TaskError, canonical_answer, match_options, validate_task
from campus_homework.state import RunStore
from campus_homework import fileio


def reviewed_question():
    return {"id": "q1", "number": 1, "type": "single", "stem": "2+3=?",
            "source": {"complete": True, "kind": "synthetic"},
            "options": [{"id": "four", "label": "A", "text": "4"},
                        {"id": "five", "label": "B", "text": "5"}],
            "solution": {"final": {"option_ids": ["five"]}},
            "review": {"status": "verified", "reviewer": "fixture-author",
                       "method": "Independent arithmetic", "checks": {
                           "conditions": True, "answer": True, "symbols": True,
                           "units": True, "language": True}}}


def task():
    return {"schema_version": 1, "assignment": {"id": "synthetic-1", "title": "测试作业",
             "course": "自编测试", "kind": "homework"}, "questions": [reviewed_question()]}


class ModelTests(unittest.TestCase):
    def test_shuffle_uses_content_not_old_letters(self):
        labels = match_options(reviewed_question(), [{"label": "A", "text": "5"},
                                                     {"label": "B", "text": "4"}])
        self.assertEqual(labels, ["A"])

    def test_changed_option_set_rejected(self):
        with self.assertRaises(TaskError):
            match_options(reviewed_question(), [{"label": "A", "text": "5"},
                                                {"label": "B", "text": "6"}])

    def test_identical_options_are_ambiguous(self):
        q = reviewed_question()
        q["options"][1]["text"] = "4"
        self.assertTrue(validate_task(task() | {"questions": [q]}))

    def test_minus_and_units_are_not_normalized_away(self):
        q = reviewed_question()
        q["options"][0]["text"] = "-5 m/s"
        q["options"][1]["text"] = "5 m/s"
        with self.assertRaises(TaskError):
            match_options(q, [{"label": "A", "text": "5 m/s"}, {"label": "B", "text": "5 m/s"}])

    def test_incomplete_source_and_missing_review_block(self):
        data = task()
        data["questions"][0]["source"]["complete"] = False
        self.assertTrue(validate_task(data, True))
        data = task()
        data["questions"][0]["review"]["checks"]["symbols"] = False
        self.assertTrue(validate_task(data, True))

    def test_all_fill_blanks_required(self):
        q = reviewed_question() | {"type": "fill", "blank_count": 2}
        q["solution"]["final"] = {"values": ["2"]}
        self.assertTrue(validate_task(task() | {"questions": [q]}, True))

    def test_formal_exam_is_not_supported(self):
        data = task()
        data["assignment"]["kind"] = "formal_exam"
        self.assertTrue(validate_task(data))

    def test_multi_order_is_irrelevant(self):
        q = reviewed_question() | {"type": "multiple"}
        self.assertEqual(canonical_answer(q, {"option_ids": ["five", "four"]}),
                         canonical_answer(q, {"option_ids": ["four", "five"]}))

    def test_reviewed_written_answers_require_valid_steps_and_final_fields(self):
        self.assertEqual(validate_task(task(), True), [])  # Native choices need no steps.
        for kind in ("calculation", "short"):
            q = reviewed_question() | {"type": kind}
            q["solution"] = {"steps": [{"text": "2+3=5"}, {"tex": "2+3=5"}],
                             "final": {"text": "5", "tex": "5"}}
            data = task() | {"questions": [q]}
            self.assertEqual(validate_task(data, True), [])
            for steps in (None, [], [None], [{}], [{"text": " "}],
                          [{"tex": 5}], [{"text": "valid", "tex": None}]):
                with self.subTest(kind=kind, steps=steps):
                    invalid = copy.deepcopy(data)
                    invalid["questions"][0]["solution"]["steps"] = steps
                    self.assertTrue(validate_task(invalid, True))
            for field in ("text", "tex"):
                with self.subTest(kind=kind, final_field=field):
                    invalid = copy.deepcopy(data)
                    invalid["questions"][0]["solution"]["final"][field] = 5
                    self.assertTrue(validate_task(invalid, True))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.evidence = self.path / "evidence.txt"
        self.evidence.write_text("Synthetic visible result", encoding="utf-8")
        self.store = RunStore.create(self.path / "run.json", task())

    def fill_and_save(self):
        self.store.record_filled("q1", {"option_ids": ["five"]}, str(self.evidence))
        self.store.save_verified(str(self.evidence), "Synthetic save successful")

    def test_windows_error17_run_creation_and_update(self):
        error = OSError('simulated Windows rename rejection')
        error.winerror = 17
        with patch.object(fileio.platform, 'system', return_value='Windows'), \
             patch.object(fileio.os, 'replace', side_effect=error):
            with self.assertWarnsRegex(RuntimeWarning, 'non-atomic'):
                store = RunStore.create(self.path / 'fallback-run.json', task())
            self.assertEqual(store.read()['status'], 'selected')
            changed = task()
            changed['questions'][0]['stem'] = 'Calculate 2+3.'
            with self.assertWarnsRegex(RuntimeWarning, 'non-atomic'):
                store.replace_reviewed_task(changed)
            self.assertEqual(store.read()['revision'], 1)
            self.assertEqual(store.read()['task'], changed)
        self.assertEqual(list(self.path.glob('*.backup-*.tmp')), [])

    def test_windows_error17_partial_log_write_preserves_previous_journal(self):
        error = OSError('simulated Windows rename rejection')
        error.winerror = 17
        previous = self.store.path.read_bytes()
        writer = fileio._write_file
        def failing_writer(destination, payload):
            if destination == self.store.path and payload != previous:
                destination.write_bytes(b'{incomplete')
                raise OSError('simulated partial journal write')
            return writer(destination, payload)
        with patch.object(fileio.platform, 'system', return_value='Windows'), \
             patch.object(fileio.os, 'replace', side_effect=error), \
             patch.object(fileio, '_write_file', side_effect=failing_writer):
            with self.assertWarnsRegex(RuntimeWarning, 'non-atomic'):
                with self.assertRaisesRegex(OSError, 'simulated partial journal write'):
                    self.store.record_filled('q1', {'option_ids': ['five']}, str(self.evidence))
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertEqual(self.store.read()['revision'], 0)
        self.assertEqual(self.store.read()['questions']['q1']['status'], 'pending')

    def test_cannot_submit_unfilled_or_unsaved(self):
        with self.assertRaises(TaskError): self.store.submission_gate()
        self.store.record_filled("q1", {"option_ids": ["five"]}, str(self.evidence))
        with self.assertRaises(TaskError): self.store.submission_gate()

    def test_wrong_readback_rejected(self):
        with self.assertRaises(TaskError):
            self.store.record_filled("q1", {"option_ids": ["four"]}, str(self.evidence))

    def test_submit_once_and_no_mutation_afterwards(self):
        self.fill_and_save()
        self.assertTrue(self.store.submission_gate()["ready"])
        self.store.begin_submission()
        with self.assertRaises(TaskError): self.store.begin_submission()
        self.store.confirm_submission(str(self.evidence), "Synthetic submitted", "2026-10-24T11:00+08:00")
        self.assertEqual(self.store.read()["status"], "submitted")
        with self.assertRaises(TaskError): self.store.record_filled("q1", {"option_ids": ["five"]}, str(self.evidence))

    def test_task_update_invalidates_saved_and_readback(self):
        self.fill_and_save()
        data = task()
        data["questions"][0]["solution"]["final"] = {"option_ids": ["four"]}
        self.store.replace_reviewed_task(data)
        self.assertEqual(self.store.read()["questions"]["q1"]["status"], "pending")
        with self.assertRaises(TaskError): self.store.submission_gate()

    def test_workbook_change_invalidates_all_readbacks_and_artifacts(self):
        original = task()
        original["questions"][0]["delivery"] = "image"
        original["questions"].append(copy.deepcopy(original["questions"][0]) |
                                     {"id": "q2", "number": 2})
        original["workbook"] = {"pages": [{"image": "original.png", "page_number": 1,
            "regions": [{"question_id": "q1", "box": [0, 100, 200, 100]}],
            "protected_boxes": [[0, 0, 200, 100]]}]}
        image = self.path / "answer.png"
        image.write_bytes(b"Synthetic attachment bytes")
        for field, value in (("image", "different.png"),
                             ("regions", [{"question_id": "q1", "box": [0, 200, 200, 100]}]),
                             ("protected_boxes", [[0, 0, 200, 200]]),
                             ("remove_workbook", None)):
            with self.subTest(field=field):
                store = RunStore.create(self.path / (field + ".json"), original)
                for qid in ("q1", "q2"):
                    store.register_artifacts(qid, [str(image)])
                    sha = store.read()["questions"][qid]["artifacts"][0]["sha256"]
                    store.record_filled(qid, {"option_ids": ["five"], "uploaded_sha256": [sha]},
                                        str(self.evidence))
                store.save_verified(str(self.evidence), "Saved")
                self.assertTrue(store.submission_gate()["ready"])
                changed = copy.deepcopy(original)
                if field == "remove_workbook":
                    del changed["workbook"]
                else:
                    changed["workbook"]["pages"][0][field] = value
                store.replace_reviewed_task(changed)
                updated = store.read()
                self.assertIsNone(updated["saved_revision"])
                for qid in ("q1", "q2"):
                    self.assertEqual(updated["questions"][qid], {"status": "pending", "artifacts": []})
                with self.assertRaises(TaskError):
                    store.save_verified(str(self.evidence), "Saved again")
                with self.assertRaises(TaskError): store.submission_gate()

    def test_tampered_task_detected(self):
        data = self.store.read()
        data["task"]["questions"][0]["stem"] = "Changed question"
        self.store.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(TaskError): self.store.read()

    def test_uncertain_network_submission_requires_reconcile_and_resave(self):
        self.fill_and_save()
        self.store.begin_submission()
        self.store.reconcile_not_submitted(str(self.evidence), "Synthetic platform confirms not submitted")
        with self.assertRaises(TaskError): self.store.begin_submission()
        self.store.save_verified(str(self.evidence), "Saved again")
        self.store.begin_submission()

    def test_image_changed_or_not_uploaded_blocks(self):
        data = task()
        data["questions"][0]["delivery"] = "image"
        self.store = RunStore.create(self.path / "image-run.json", data)
        image = self.path / "answer.png"
        image.write_bytes(b"Synthetic attachment bytes")
        self.store.register_artifacts("q1", [str(image)])
        with self.assertRaises(TaskError):
            self.store.record_filled("q1", {"option_ids": ["five"]}, str(self.evidence))
        sha = self.store.read()["questions"]["q1"]["artifacts"][0]["sha256"]
        self.store.record_filled("q1", {"option_ids": ["five"], "uploaded_sha256": [sha]}, str(self.evidence))
        self.store.save_verified(str(self.evidence), "Saved")
        image.write_bytes(b"Modified attachment")
        with self.assertRaises(TaskError): self.store.submission_gate()


if __name__ == "__main__":
    unittest.main()
