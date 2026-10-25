"""Run after setup: python -m unittest discover -s tests -p test_rendering.py."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from campus_homework.rendering import PAGE_SIZE, _ballpoint_layer, _function, render_document, style_catalog


def question(qid="q1", steps=None, figures=None, final=None):
    return dict(id=qid, number=1, type="calculation", stem="计算并说明过程。",
                solution=dict(steps=steps or [{"text": "根据已核对的条件建立方程。"}],
                              figures=figures or [], final=final or {"tex": r"a=\sqrt{a_\tau^2+a_n^2}"}),
                review={"status": "verified"})


def task(questions=None):
    return dict(schema_version=1, assignment={"id": "demo", "title": "计算题核验样张", "course": "物理", "kind": "homework"},
                questions=questions or [question()])


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="campus-rendering-")
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_complex_tex_figures_and_catalog(self):
        figures = [
            {"type": "force", "origin": [0, 0], "body": "block", "axes": True,
             "forces": [{"label": "N", "vector": [0, 3]}, {"label": "mg", "vector": [0, -3]}, {"label": "F", "vector": [3, 0]}]},
            {"type": "geometry", "points": {"A": [0, 0], "B": [4, 0], "C": [0, 3]},
             "segments": [["A", "B"], ["B", "C"], ["C", "A"]],
             "arcs": [{"center": "A", "radius": .6, "start": 0, "end": 90, "label": "θ"}]},
            {"type": "function", "expression": "sin(x)", "x_range": [-math_pi(), math_pi()], "y_range": [-1.4, 1.4]},
        ]
        tex = [r"\begin{aligned}\vec a&=\frac{d\vec v}{dt}\\a_n&=\frac{v^2}{\rho}\end{aligned}",
               r"A=\begin{pmatrix}1&2\\3&4\end{pmatrix},\quad\det A=-2",
               r"f(x)=\begin{cases}x^2&x\geq0\\-x&x<0\end{cases}",
               r"\int_0^1 x^2\,dx=\frac13,\quad\sum_{n=1}^{\infty}\frac1{n^2}=\frac{\pi^2}6"]
        data = task([question(f"q{i}", [{"tex": tex[i]}, {"text": "结果的符号和单位已经核对。"}], [figure]) for i, figure in enumerate(figures)])
        data["questions"][0]["solution"]["final"] = {"tex": tex[-1]}
        original = copy.deepcopy(data)
        result = render_document(data, self.root / "out", {"font": "chenyu"})
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(data, original)
        with Image.open(result["clean_pages"][0]) as rendered:
            self.assertEqual(rendered.size, PAGE_SIZE)
            self.assertEqual(rendered.format, "PNG")
        with Image.open(result["upload_pages"][0]) as uploaded:
            self.assertEqual(uploaded.format, "JPEG")
            self.assertEqual(uploaded.size, PAGE_SIZE)
        self.assertTrue(Path(result["clean_pdf"]).is_file())
        self.assertEqual(len(manifest["math"]["formulas"]), 5)  # four step formulas plus shared final.
        self.assertTrue(manifest["fonts"]["fallbacks"])
        self.assertTrue(any(block["kind"] == "figure_and_steps" for page in manifest["pages"] for block in page["blocks"]))
        self.assertEqual({font["id"] for font in style_catalog()["fonts"]}, {"wenkai", "yozai", "chenyu"})

    def test_workbook_print_is_immutable_and_overflow_gets_continuation(self):
        source = self.root / "workbook.png"
        original = Image.new("RGB", (700, 900), "#f8f5ed")
        ImageDraw.Draw(original).rectangle([15, 20, 680, 170], fill="#345678")
        original.save(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        data = task([question(steps=[{"text": "这是第%d步，完整保留计算过程及单位。" % n} for n in range(15)], final={"tex": "F=ma"})])
        data["workbook"] = {"pages": [{"image": str(source), "page_number": 7,
            "regions": [{"question_id": "q1", "box": [45, 200, 600, 110]}], "protected_boxes": [[0, 0, 700, 180]]}]}
        result = render_document(data, self.root / "out")
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        clean = np.asarray(Image.open(result["clean_pages"][0]).convert("RGB"))
        before = np.asarray(original)
        outside = np.ones(before.shape[:2], dtype=bool)
        outside[200:310, 45:645] = False
        self.assertTrue(np.array_equal(clean[outside], before[outside]))
        self.assertTrue(np.array_equal(clean[:180], before[:180]))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertGreater(result["page_count"], 1)
        self.assertEqual(manifest["pages"][1]["kind"], "continuation")
        self.assertIn(7, manifest["pages"][1]["original_pages"])

    def test_tiny_or_protected_region_does_not_crop_content(self):
        source = self.root / "workbook.png"
        Image.new("RGB", (700, 900), "white").save(source)
        data = task()
        data["workbook"] = {"pages": [{"image": str(source), "regions": [{"question_id": "q1", "box": [20, 20, 40, 20]}]}]}
        result = render_document(data, self.root / "out")
        self.assertEqual(np.asarray(Image.open(result["clean_pages"][0])).min(), 255)
        self.assertGreater(result["page_count"], 1)

    def test_automatic_pagination_and_complete_formula_count(self):
        data = task([question(steps=[{"tex": rf"x_{{{i}}}=\frac{{{i}+1}}{{2}}"} for i in range(40)], final={"text": "以上计算步骤完整。"})])
        result = render_document(data, self.root / "out")
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertGreater(result["page_count"], 1)
        self.assertEqual(sum(block["kind"] == "formula" for page in manifest["pages"] for block in page["blocks"]), 40)
        for page in manifest["pages"]:
            for block in page["blocks"]:
                x, y, w, h = block["box"]
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x + w, page["size"][0])
                self.assertLessEqual(y + h, page["size"][1])

    def test_photo_requires_supplied_background_and_preserves_full_page(self):
        with self.assertRaisesRegex(ValueError, "requires an existing photo_background"):
            render_document(task(), self.root / "missing", {"mode": "photo"})
        background = self.root / "desk.png"
        Image.new("RGB", (1300, 1600), "#9f8266").save(background)
        result = render_document(task(), self.root / "photo", {"mode": "photo", "photo_background": str(background)})
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        with Image.open(result["upload_pages"][0]) as rendered:
            self.assertEqual(rendered.size, (1300, 1600))
        photo = manifest["pages"][0]["photo"]
        self.assertFalse(photo["image_service_called"])
        self.assertTrue(photo["simulated_photo"])
        self.assertTrue(all(0 < x < 1300 and 0 < y < 1600 for x, y in photo["corners"]))
        with self.assertRaisesRegex(ValueError, "non-mirrored convex"):
            render_document(task(), self.root / "mirror", {"mode": "photo", "photo_background": str(background), "photo_corners": [[50, 50], [50, 1400], [1200, 1400], [1200, 50]]})

    def test_invalid_tex_and_unverified_answers_are_rejected(self):
        bad = task([question(final={"tex": r"\frac{1}"})])
        with self.assertRaisesRegex(ValueError, "Formula rendering failed"):
            render_document(bad, self.root / "bad")
        bad["questions"][0]["review"]["status"] = "draft"
        with self.assertRaisesRegex(ValueError, "has not been verified"):
            render_document(bad, self.root / "unverified")
        self.assertFalse((self.root / "bad" / "upload").exists())

    def test_natural_handwriting_preserves_math_positions_and_structures(self):
        tex = r"A^{-1}=\frac{-13}{14},\quad x_2=\operatorname{diag}(1,2),\quad B=\begin{pmatrix}14&-32\\5&-16\end{pmatrix}"
        data = task([question(steps=[{"text": "逐字书写，保留多位数字与上下标。"}], final={"tex": tex})])
        ordinary = render_document(data, self.root / "ordinary", {"font": "chenyu"})
        natural = render_document(data, self.root / "natural", {"font": "chenyu", "handwriting": "natural"})
        original_manifest = json.loads(Path(ordinary["manifest"]).read_text(encoding="utf-8"))
        natural_manifest = json.loads(Path(natural["manifest"]).read_text(encoding="utf-8"))
        old_record = original_manifest["math"]["formulas"][0]
        new_record = natural_manifest["math"]["formulas"][0]
        self.assertEqual(original_manifest["profile"]["handwriting"], "off")
        self.assertEqual(old_record["size"], new_record["size"])
        self.assertEqual(old_record["tex"], new_record["tex"])
        self.assertGreater(new_record["handwritten_glyphs"], 0)
        original = list(ET.parse(old_record["svg"]).getroot().iter())
        handwritten = list(ET.parse(new_record["svg"]).getroot().iter())
        self.assertEqual(len(original), len(handwritten))
        for old, new in zip(original, handwritten):
            self.assertEqual(old.tag, new.tag)
            if new.get("data-handwriting") == "natural":
                self.assertEqual(old.get("data-c"), new.get("data-c"))
                self.assertTrue(new.get("transform").startswith(old.get("transform", "")))
                self.assertNotEqual(old.get("d"), new.get("d"))
            else:
                self.assertEqual(old.attrib, new.attrib)  # Includes fractions and stretched brackets.
            if old.get("data-c"):
                char = chr(int(old.get("data-c"), 16))
                char = "-" if char == "−" else unicodedata.normalize("NFKC", char)
                if char in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+-=":
                    self.assertEqual(new.get("data-handwriting"), "natural")
        with Image.open(ordinary["clean_pages"][0]) as old_image, Image.open(natural["clean_pages"][0]) as new_image:
            self.assertEqual(old_image.size, new_image.size)
            self.assertFalse(np.array_equal(np.asarray(old_image), np.asarray(new_image)))
        with self.assertRaisesRegex(ValueError, "handwriting must be"):
            render_document(data, self.root / "bad-style", {"handwriting": "unknown"})

    def test_function_parser_does_not_execute_python(self):
        self.assertAlmostEqual(_function("sin(x)**2+cos(x)**2")(1.234), 1)
        for expression in ["__import__('os').system('echo bad')", "x.__class__", "open('x')", "y+1", "[x for x in range(3)]"]:
            with self.assertRaises(ValueError):
                _function(expression)

    def test_ballpoint_keeps_thin_strokes_and_transparent_paper_intact(self):
        paper = Image.new("RGB", (240, 100), (180, 177, 169))
        ink = Image.new("RGBA", (200, 50))
        draw = ImageDraw.Draw(ink)
        draw.line((5, 20, 195, 20), fill="#202126", width=1)  # A thin minus/fraction line.
        draw.line((5, 30, 195, 30), fill="#202126", width=2)
        original = ink.tobytes()
        layer = _ballpoint_layer(paper, ink, 10, 10, 22)
        alpha, changed_alpha = np.asarray(ink)[:, :, 3], np.asarray(layer)[:, :, 3]
        self.assertEqual(ink.tobytes(), original)
        self.assertTrue(np.array_equal(alpha > 0, changed_alpha > 0))
        self.assertGreaterEqual(changed_alpha[alpha == 255].min(), 160)
        self.assertGreater(np.ptp(changed_alpha[alpha == 255]), 10)
        before = np.asarray(paper).copy()
        paper.paste(layer, (10, 10), layer)
        stroke_mask = np.zeros(before.shape[:2], dtype=bool)
        stroke_mask[10:60, 10:210] = alpha > 0
        self.assertTrue(np.array_equal(before[~stroke_mask], np.asarray(paper)[~stroke_mask]))
        lighter = _ballpoint_layer(Image.new("RGB", paper.size, "white"), ink, 10, 10, 22)
        self.assertTrue((np.asarray(layer)[:, :, :3][alpha > 0] <= np.asarray(lighter)[:, :, :3][alpha > 0]).all())


def math_pi():
    return 3.141592653589793


if __name__ == "__main__":
    unittest.main()
