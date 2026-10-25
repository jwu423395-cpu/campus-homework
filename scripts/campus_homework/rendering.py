"""Deterministic homework layout; photo mode only composites a supplied background."""
from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
PAGE_SIZE = (1654, 2339)  # A4, rounded to integral pixels at 200 dpi.
DPI = 200
DEFAULTS = dict(paper="ruled", font="wenkai", mode="accurate", ink="#20252b",
                font_size=36, line_spacing=1.45, letter_spacing=0, margin=90, handwriting="off",
                ink_effect="flat")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _font_records() -> dict:
    path = ROOT / "assets/fonts/manifest.json"
    if not path.exists():
        return {}
    records = json.loads(path.read_text(encoding="utf-8-sig"))
    records = records.get("fonts", records)
    if isinstance(records, list):
        records = {item["id"]: item for item in records}
    return {key: value for key, value in records.items() if isinstance(value, dict) and "file" in value}


def style_catalog() -> dict:
    """Return selectable presets, without downloading or inventing font assets."""
    return {
        "papers": [dict(id=key, name=name) for key, name in
                   [("ruled", "横线纸"), ("blank", "白纸"), ("grid", "方格纸"), ("cream", "米色纸")]],
        "fonts": [dict(id=key, **record, available=(ROOT / "assets/fonts" / record["file"]).is_file())
                  for key, record in _font_records().items()],
        "modes": ["accurate", "photo"], "defaults": DEFAULTS.copy(),
        "page": dict(size_pixels=list(PAGE_SIZE), dpi=DPI, format="A4"),
        "photo_requires_background": True,
    }


class _Fonts:
    def __init__(self, selected: str, size: int, spacing: float, handwriting: str = "off"):
        self.records = _font_records()
        self.size, self.spacing = size, spacing
        self.natural, self.drawn_characters = handwriting == "natural", 0
        self.fallbacks: dict[str, dict] = {}
        self.paths = []
        for font_id in [selected] + (["wenkai"] if selected != "wenkai" else []):
            path = (ROOT / "assets/fonts" / self.records[font_id]["file"]) if font_id in self.records else Path(font_id)
            if not path.is_file():
                if font_id == selected:
                    raise ValueError(f"Font is unavailable: {font_id}. Install the licensed font listed in assets/fonts/manifest.json.")
                continue
            path = path.resolve()
            if path in self.paths:
                continue
            self.paths.append(path)
        self.maps, self.loaded, self.buffers, self.families = [], {}, {}, []
        self.adjustments = []
        for path in self.paths:
            font = TTFont(str(path), fontNumber=0, lazy=True)
            self.maps.append(set(font.getBestCmap() or {}))
            self.families.append(font["name"].getDebugName(1))
            font.close()
        self.primary = str(self.paths[0])
        metrics = [self.font(index).getmetrics() for index in range(len(self.paths))]
        self.ascent = max(value[0] for value in metrics)
        self.height = max(math.ceil(size * 1.45), max(sum(value) for value in metrics) + 4)

    def font(self, index: int, size: int | None = None):
        key = (index, size or self.size)
        if key not in self.loaded:
            source = io.BytesIO(self.buffers[index]) if index in self.buffers else str(self.paths[index])
            try:
                loaded = ImageFont.truetype(source, key[1])
                loaded.getlength("A")  # Force FreeType's hint-program validation before layout.
            except OSError as error:
                if "too many function definitions" not in str(error):
                    raise
                # Some Chenyu releases have an invalid TrueType hint program. Preserve the
                # licensed source and its outlines; strip only hint instructions in memory.
                font = TTFont(str(self.paths[index]), fontNumber=0)
                font["glyf"].removeHinting()
                for table in ("fpgm", "prep", "cvt "):
                    if table in font:
                        del font[table]
                buffer = io.BytesIO()
                font.save(buffer)
                font.close()
                self.buffers[index] = buffer.getvalue()
                self.adjustments.append(dict(font=str(self.paths[index]), action="removed_invalid_truetype_hints_in_memory", original_file_unchanged=True, reason=str(error)))
                loaded = ImageFont.truetype(io.BytesIO(self.buffers[index]), key[1])
                loaded.getlength("A")
            self.loaded[key] = loaded
        return self.loaded[key]

    def index(self, char: str) -> int:
        if char.isspace():
            return 0
        for index, coverage in enumerate(self.maps):
            if ord(char) in coverage:
                if index:
                    self.fallbacks[char] = dict(character=char, codepoint=f"U+{ord(char):04X}", font=str(self.paths[index]))
                return index
        raise ValueError(f"Missing glyph {char!r} (U+{ord(char):04X}) in the selected font and Wenkai fallback; use TeX for mathematical accents/arrows.")

    def width(self, text: str, size: int | None = None) -> float:
        return sum(self.font(self.index(char), size).getlength(char) + self.spacing for char in text)

    def draw(self, canvas: Image.Image, xy: tuple, text: str, ink: str, size: int | None = None):
        draw, x, y = ImageDraw.Draw(canvas), *xy
        font_size = size or self.size
        ascent = max(self.font(i, font_size).getmetrics()[0] for i in range(len(self.paths)))
        for char in text:
            font = self.font(self.index(char), font_size)
            dx = dy = 0
            handwritten_font = font
            if self.natural and not char.isspace():
                seed = hashlib.sha256(f"{self.drawn_characters}:{char}:{font_size}".encode()).digest()
                dx = (seed[0] / 255 - .5) * font_size * .035
                dy = (seed[1] / 255 - .5) * font_size * .05
                handwritten_font = self.font(self.index(char), max(10, round(font_size * (1 + (seed[2] / 255 - .5) * .05))))
            draw.text((x + dx, y + ascent + dy), char, font=handwritten_font, fill=ink, anchor="ls")
            self.drawn_characters += 1
            x += font.getlength(char) + self.spacing

    def math_glyphs(self) -> dict:
        """Use installed font outlines; MathJax keeps all positioning and structure."""
        result = {}
        characters = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+-=×÷<>≤≥±∓·"
        for path in self.paths:
            with TTFont(str(path), fontNumber=0) as font:
                glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
                for char in characters:
                    if char in result or ord(char) not in cmap:
                        continue
                    glyph = glyphs[cmap[ord(char)]]
                    outline, bounds = SVGPathPen(glyphs), BoundsPen(glyphs)
                    glyph.draw(outline)
                    glyph.draw(bounds)
                    if bounds.bounds and glyph.width:
                        result[char] = dict(path=outline.getCommands(), bounds=list(bounds.bounds), advance=glyph.width)
        return result

    def lines(self, text: str, width: int) -> list[str]:
        if width < self.size * 2:
            raise ValueError("Writing area is too narrow for readable text.")
        lines = []
        for paragraph in str(text).split("\n"):
            line = ""
            for char in paragraph:
                if line and self.width(line + char) > width:
                    lines.append(line)
                    line = ""
                line += char
            lines.append(line)
        return lines


def _node_executable() -> str:
    found = os.environ.get("CAMPUS_HOMEWORK_NODE") or shutil.which("node")
    if not found:
        raise RuntimeError("Node.js is required for TeX rendering; install Node 18+ or set CAMPUS_HOMEWORK_NODE.")
    return found


class _Math:
    def __init__(self, questions: list[dict], fonts: _Fonts, profile: dict):
        self.items = {}
        formulas = []
        for question in questions:
            solution = question.get("solution", {})
            for item in [*solution.get("steps", []), solution.get("final", {})]:
                if isinstance(item, dict) and item.get("tex") and item["tex"] not in formulas:
                    formulas.append(item["tex"])
        if not formulas:
            return
        for tex in formulas:
            for char in tex:
                if "\u2e80" <= char <= "\ua000" or "\uf900" <= char <= "\ufaff":
                    fonts.index(char)  # Record CJK fallback inside TeX text runs as well.
        payload = dict(formulas=[dict(tex=tex, font_size=profile["font_size"], ink=profile["ink"]) for tex in formulas],
                       font_files=[str(path) for path in fonts.paths], font_family=fonts.families[0],
                       handwriting=profile["handwriting"], handwriting_glyphs=fonts.math_glyphs() if fonts.natural else {})
        try:
            result = subprocess.run([_node_executable(), str(ROOT / "scripts/mathjax-render.mjs")],
                                    input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                                    text=True, encoding="utf-8", timeout=25, cwd=ROOT)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Formula rendering exceeded the 25-second limit; simplify or split the TeX input.") from error
        if result.returncode:
            raise ValueError(result.stderr.strip() or "MathJax rendering failed; run npm ci in the skill directory.")
        data = json.loads(result.stdout)
        if len(data.get("formulas", [])) != len(formulas):
            raise RuntimeError("MathJax returned an incomplete formula batch.")
        for item in data["formulas"]:
            item["image"] = Image.open(io.BytesIO(base64.b64decode(item.pop("png")))).convert("RGBA")
            self.items[item["tex"]] = item

    def image(self, tex: str, width: int, size: int) -> Image.Image:
        image = self.items[tex]["image"].copy()
        if image.width > width:
            ratio = width / image.width
            if size * ratio < 20:
                raise ValueError("Formula cannot fit at readable size; split it into aligned steps or use a wider writing region.")
            image = image.resize((width, max(1, round(image.height * ratio))), Image.Resampling.LANCZOS)
        return image


@dataclass
class _Block:
    image: Image.Image
    kind: str
    source: dict


def _text_blocks(text, width, fonts, profile, source=None):
    height = max(fonts.height, math.ceil(profile["font_size"] * profile["line_spacing"]))
    blocks = []
    for line in fonts.lines(str(text), width):
        image = Image.new("RGBA", (width, height))
        fonts.draw(image, (0, 0), line, profile["ink"])
        blocks.append(_Block(image, "text", dict(source or {}, text=line)))
    return blocks


def _intersects(a, b):
    return a[0] < b[0] + b[2] and a[0] + a[2] > b[0] and a[1] < b[1] + b[3] and a[1] + a[3] > b[1]


def _box(box, size):
    if not isinstance(box, (list, tuple)) or len(box) != 4 or any(not isinstance(x, (float, int)) or not math.isfinite(x) for x in box):
        raise ValueError("A region/protected box must be [x, y, width, height] in source-image pixels.")
    x, y, width, height = (round(value) for value in box)
    if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > size[0] or y + height > size[1]:
        raise ValueError(f"Writing/protected box lies outside the original page: {box}.")
    return [x, y, width, height]


def _function(expression: str):
    """Evaluate a small arithmetic AST, never Python eval or arbitrary symbols."""
    functions = {name: getattr(math, name) for name in ["sin", "cos", "tan", "asin", "acos", "atan", "sqrt", "exp", "log", "log10", "sinh", "cosh", "tanh"]}
    functions["abs"] = abs
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError("Invalid function expression.") from error
    if len(list(ast.walk(tree))) > 150:
        raise ValueError("Function expression is too complex.")

    def visit(node, x):
        if isinstance(node, ast.Expression):
            return visit(node.body, x)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in ("x", "pi", "e"):
            return {"x": x, "pi": math.pi, "e": math.e}[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return (-1 if isinstance(node.op, ast.USub) else 1) * visit(node.operand, x)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            left, right = visit(node.left, x), visit(node.right, x)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div): return left / right
            if abs(right) > 20: raise ValueError("Function exponent exceeds the supported range.")
            return left ** right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions and len(node.args) == 1 and not node.keywords:
            return functions[node.func.id](visit(node.args[0], x))
        raise ValueError("Unsupported function syntax or symbol; only x, pi, e and documented arithmetic/functions are allowed.")

    # Validate every branch even when a sample would have a domain error.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda, ast.NamedExpr, ast.ListComp, ast.Dict, ast.List, ast.Tuple, ast.Compare, ast.BoolOp)):
            raise ValueError("Unsupported function syntax.")
        if isinstance(node, ast.Name) and node.id not in {"x", "pi", "e", *functions}:
            raise ValueError(f"Unknown function symbol: {node.id}.")
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in functions or len(node.args) != 1 or node.keywords):
            raise ValueError("Unsupported function call.")
    return lambda x: visit(tree, x)


def _figure(figure: dict, width: int, fonts: _Fonts, ink: str) -> _Block:
    kind = figure.get("type")
    if kind not in ("force", "geometry", "function"):
        raise ValueError(f"Unsupported figure type: {kind}.")
    height = max(210, round(width * .78))
    scale = 2
    image = Image.new("RGBA", (width * scale, height * scale))
    draw = ImageDraw.Draw(image)
    label_size = max(14, min(25, width // 18)) * scale
    points, lines, circles, arrows, arcs = {}, [], [], [], []
    if kind == "force":
        origin = tuple(figure.get("origin", [0, 0]))
        points = {"": origin}
        for force in figure.get("forces", []):
            vector = force["vector"]
            end = [origin[0] + vector[0], origin[1] + vector[1]]
            arrows.append(dict(start=origin, end=end, label=force.get("label", "")))
        if not arrows:
            raise ValueError("A force diagram needs at least one force vector.")
        all_points = [origin, *[arrow["end"] for arrow in arrows]]
    elif kind == "geometry":
        points = figure.get("points", {})
        def point(value): return points[value] if isinstance(value, str) else value
        for segment in figure.get("segments", []):
            if isinstance(segment, dict):
                lines.append(dict(start=point(segment["start"]), end=point(segment["end"]), label=segment.get("label", "")))
            else:
                lines.append(dict(start=point(segment[0]), end=point(segment[1]), label=""))
        circles = [dict(circle, center=point(circle["center"])) for circle in figure.get("circles", [])]
        arrows = [dict(start=point(item["start"]), end=point(item["end"]), label=item.get("label", "")) for item in figure.get("vectors", [])]
        arcs = [dict(item, center=point(item["center"])) for item in figure.get("arcs", [])]
        all_points = [*points.values(), *[item[end] for item in lines + arrows for end in ("start", "end")]]
        for item in circles + arcs:
            cx, cy = item["center"]
            radius = item["radius"]
            if radius <= 0: raise ValueError("Circle/arc radius must be positive.")
            all_points.extend([[cx - radius, cy - radius], [cx + radius, cy + radius]])
        if not all_points: raise ValueError("Geometry diagram is empty.")
    else:
        expression = figure.get("expression", "")
        fn = _function(expression)
        xmin, xmax = map(float, figure.get("x_range", [-5, 5]))
        if not math.isfinite(xmin + xmax) or xmin >= xmax: raise ValueError("Invalid function x_range.")
        values = []
        for x in np.linspace(xmin, xmax, min(2000, max(50, int(figure.get("samples", 500))))):
            try:
                y = fn(float(x))
                y = float(y) if not isinstance(y, complex) else math.nan
            except (ValueError, OverflowError, ZeroDivisionError):
                y = math.nan
            values.append((float(x), y if math.isfinite(y) else math.nan))
        finite = [y for _, y in values if math.isfinite(y)]
        if not finite: raise ValueError("Function has no real finite values in the plotting interval.")
        ymin, ymax = map(float, figure.get("y_range", [min(finite), max(finite)]))
        if ymin == ymax: ymin, ymax = ymin - 1, ymax + 1
        if not math.isfinite(ymin + ymax) or ymin >= ymax: raise ValueError("Invalid function y_range.")
        all_points = [[xmin, ymin], [xmax, ymax]]
    for point_value in all_points:
        if len(point_value) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in point_value):
            raise ValueError("Diagram coordinates must be pairs of finite numbers.")
    xs, ys = zip(*all_points)
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    if xmin == xmax: xmin, xmax = xmin - 1, xmax + 1
    if ymin == ymax: ymin, ymax = ymin - 1, ymax + 1
    pad = 45 * scale
    factor = min((width * scale - 2 * pad) / (xmax - xmin), (height * scale - 2 * pad) / (ymax - ymin))
    xoffset = (width * scale - (xmax - xmin) * factor) / 2
    yoffset = (height * scale - (ymax - ymin) * factor) / 2
    def xy(point): return (xoffset + (point[0] - xmin) * factor, height * scale - yoffset - (point[1] - ymin) * factor)
    def label(point, text):
        if text:
            label_width = fonts.width(text, label_size)
            x = min(max(3, point[0] + 6), image.width - label_width - 3)
            y = min(max(3, point[1] - label_size - 8), image.height - label_size * 1.5)
            fonts.draw(image, (x, y), text, ink, label_size)
    def arrow(start, end, color=ink):
        a, b = xy(start), xy(end)
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length < 1: raise ValueError("Zero-length diagram arrow.")
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        head = min(18 * scale, length / 3)
        draw.line([a, b], fill=color, width=3 * scale)
        draw.polygon([b, (b[0] - head * ux + head * .4 * uy, b[1] - head * uy - head * .4 * ux),
                      (b[0] - head * ux - head * .4 * uy, b[1] - head * uy + head * .4 * ux)], fill=color)
    if kind == "function":
        if ymin <= 0 <= ymax: arrow([xmin, 0], [xmax, 0], "#687783")
        if xmin <= 0 <= xmax: arrow([0, ymin], [0, ymax], "#687783")
        previous = None
        for x, y in values:
            current = xy((x, y)) if math.isfinite(y) and ymin <= y <= ymax else None
            if current is not None and previous is not None and abs(current[1] - previous[1]) < height * scale / 2:
                draw.line([previous, current], fill=ink, width=3 * scale)
            previous = current
        label(xy([xmax, min(max(0, ymin), ymax)]), "x")
        label(xy([min(max(0, xmin), xmax), ymax]), "y")
    if kind == "force":
        ox, oy = xy(origin)
        if figure.get("body", "point") == "block":
            draw.rectangle([ox - 24 * scale, oy - 18 * scale, ox + 24 * scale, oy + 18 * scale], outline=ink, width=2 * scale)
        else:
            draw.ellipse([ox - 5 * scale, oy - 5 * scale, ox + 5 * scale, oy + 5 * scale], fill=ink)
        if figure.get("axes", False):
            draw.line([xy([xmin, origin[1]]), xy([xmax, origin[1]])], fill="#a4afb7", width=scale)
            draw.line([xy([origin[0], ymin]), xy([origin[0], ymax])], fill="#a4afb7", width=scale)
    for item in circles:
        cx, cy = xy(item["center"])
        r = item["radius"] * factor
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ink, width=2 * scale)
    for item in lines:
        start, end = xy(item["start"]), xy(item["end"])
        draw.line([start, end], fill=ink, width=2 * scale)
        label(((start[0] + end[0]) / 2, (start[1] + end[1]) / 2), item["label"])
    for item in arcs:
        cx, cy = xy(item["center"])
        radius = item["radius"] * factor
        draw.arc([cx - radius, cy - radius, cx + radius, cy + radius], -item["end"], -item["start"], fill=ink, width=2 * scale)
        theta = math.radians((item["start"] + item["end"]) / 2)
        label((cx + radius * math.cos(theta), cy - radius * math.sin(theta)), item.get("label", ""))
    for name, point_value in points.items():
        px, py = xy(point_value)
        if name:
            draw.ellipse([px - 3 * scale, py - 3 * scale, px + 3 * scale, py + 3 * scale], fill=ink)
            label((px, py), name)
    for item in arrows:
        arrow(item["start"], item["end"])
        label(xy(item["end"]), item["label"])
    return _Block(image.resize((width, height), Image.Resampling.LANCZOS), "figure", dict(figure=figure, coordinate_bounds=[xmin, ymin, xmax, ymax], uniform_scale=factor / scale))


def _question_blocks(question, width, fonts, formulas, profile, include_stem=True):
    heading = question.get("label") or f"第{question.get('number', question['id'])}题"
    blocks = _text_blocks(heading, width, fonts, profile, {"role": "heading"})
    if include_stem and question.get("stem"):
        blocks += _text_blocks(question["stem"], width, fonts, profile, {"role": "stem"})
    solution = question.get("solution", {})
    figures = solution.get("figures", [])
    side_by_side = bool(figures and width >= 650)
    figure_width = round(width * .38) if side_by_side else width
    gap = 24 if side_by_side else 0
    text_width = width - figure_width - gap if side_by_side else width
    content = []
    for index, item in enumerate([*solution.get("steps", []), solution.get("final", {})]):
        if not item:
            continue
        source = {"step": index, "role": "final" if index == len(solution.get("steps", [])) else "step"}
        if item.get("text"):
            content += _text_blocks(item["text"], text_width, fonts, profile, source)
        if item.get("tex"):
            image = formulas.image(item["tex"], text_width, profile["font_size"])
            padded = Image.new("RGBA", (text_width, image.height + 18))
            padded.alpha_composite(image, (0, 8))
            content.append(_Block(padded, "formula", dict(source, tex=item["tex"], tex_sha256=_sha(item["tex"].encode()))))
        if not item.get("text") and not item.get("tex"):
            answer = item.get("option_ids", item.get("values", item.get("value")))
            if answer is not None:
                content += _text_blocks("答案：" + ("、".join(map(str, answer)) if isinstance(answer, list) else str(answer)), text_width, fonts, profile, source)
    figure_blocks = [_figure(item, figure_width, fonts, profile["ink"]) for item in figures]
    if not side_by_side:
        return blocks + figure_blocks + content
    left_height = sum(item.image.height + 14 for item in figure_blocks)
    right_height = 0
    first_content = []
    while content and (right_height < left_height or not first_content):
        item = content.pop(0)
        first_content.append(item)
        right_height += item.image.height
    image = Image.new("RGBA", (width, max(left_height, right_height)))
    y = 0
    for item in figure_blocks:
        image.alpha_composite(item.image, (0, y))
        y += item.image.height + 14
    y = 0
    for item in first_content:
        image.alpha_composite(item.image, (figure_width + gap, y))
        y += item.image.height
    blocks.append(_Block(image, "figure_and_steps", dict(layout="left_figure_right_steps", figures=[item.source for item in figure_blocks], steps=[item.source for item in first_content])))
    for item in content:
        image = Image.new("RGBA", (width, item.image.height))
        image.alpha_composite(item.image, (figure_width + gap, 0))
        blocks.append(_Block(image, item.kind, item.source))
    return blocks


def _paper(profile):
    image = Image.new("RGB", PAGE_SIZE, "#fbf7e9" if profile["paper"] == "cream" else "white")
    draw = ImageDraw.Draw(image)
    margin = profile["margin"]
    spacing = max(28, round(profile["font_size"] * profile["line_spacing"]))
    if profile["paper"] in ("ruled", "grid"):
        for y in range(margin + spacing * 2, PAGE_SIZE[1] - margin, spacing):
            draw.line([(margin, y), (PAGE_SIZE[0] - margin, y)], fill="#d9e1e5", width=1)
    if profile["paper"] == "grid":
        for x in range(margin, PAGE_SIZE[0] - margin, spacing):
            draw.line([(x, margin + spacing * 2), (x, PAGE_SIZE[1] - margin)], fill="#e4e9ec", width=1)
    return image


def _ballpoint_layer(canvas, image, x, y, font_size):
    """Vary ink coverage on existing strokes, using the underlying paper colour."""
    rgba = np.array(image.convert("RGBA"))
    alpha = rgba[:, :, 3].copy()
    paper_image = canvas.crop((x, y, x + image.width, y + image.height)).convert("RGB")
    paper = np.asarray(paper_image, dtype=np.float32)
    soft_paper = np.asarray(paper_image.filter(ImageFilter.GaussianBlur(.8)), dtype=np.float32)
    yy, xx = np.ogrid[:image.height, :image.width]
    u, v = (xx + x) / font_size, (yy + y) / font_size
    # Continuous pressure changes and fine paper texture do not punch holes in strokes.
    pressure = (.84 + .085 * np.sin(2.5 * u + 1.4 * v)
                + .045 * np.sin(.72 * u - 3.1 * v)
                + .02 * np.sin(.73 * (xx + x) + .51 * (yy + y))
                + (paper - soft_paper).mean(axis=2) * .001)
    pressure = np.clip(pressure, .68, .97)
    rgba[:, :, :3] = np.rint(rgba[:, :, :3].astype(np.float32) * paper / 255).astype(np.uint8)
    rgba[:, :, 3] = np.where(alpha > 0, np.maximum(1, np.rint(alpha * pressure)), 0).astype(np.uint8)
    return Image.fromarray(rgba)


def _paste(canvas, block, x, y, page, question_id, profile):
    ballpoint = profile["handwriting"] == "natural" and profile["ink_effect"] == "ballpoint"
    image = _ballpoint_layer(canvas, block.image, x, y, profile["font_size"]) if ballpoint else block.image
    canvas.paste(image, (x, y), image)
    page["blocks"].append(dict(kind=block.kind, question_id=question_id,
                               box=[x, y, block.image.width, block.image.height],
                               ink_effect="ballpoint" if ballpoint else "flat", **block.source))


def _flow(questions, queues, cursors, pages, records, fonts, profile, title, original_pages=None):
    margin, y, canvas, record = profile["margin"], 0, None, None
    bottom = PAGE_SIZE[1] - margin
    for question in questions:
        qid = question["id"]
        queue = queues[qid]
        if cursors[qid] >= len(queue): continue
        original = (original_pages or {}).get(qid, [])
        continuation = bool(original_pages is not None)
        question_label = question.get("label") or f"第{question.get('number', qid)}题"
        label = ("续页 | " + ("原第" + "、".join(map(str, original)) + "页 | " if original else "") + question_label) if continuation else title
        while cursors[qid] < len(queue):
            block = queue[cursors[qid]]
            if block.image.height > bottom - margin - fonts.height * 3:
                raise ValueError(f"A block in question {qid} exceeds one page; split the derivation/figure into smaller blocks.")
            if canvas is None or y + block.image.height > bottom:
                canvas = _paper(profile)
                record = dict(kind="continuation" if continuation else "answer", page_number=len(records) + 1,
                              size=list(PAGE_SIZE), dpi=DPI, blocks=[], original_pages=original)
                pages.append(canvas)
                records.append(record)
                heading = label if continuation else title + (f" | 第{question.get('number', qid)}题（续）" if cursors[qid] else "")
                for heading_block in _text_blocks(heading, PAGE_SIZE[0] - 2 * margin, fonts, profile):
                    if heading_block.image.height + margin > bottom: raise ValueError("Page title is too long.")
                    _paste(canvas, heading_block, margin, margin if not record["blocks"] else y, record, qid, profile)
                    y = (margin if len(record["blocks"]) == 1 else y) + heading_block.image.height
                y += 22
            x = margin
            _paste(canvas, block, x, y, record, qid, profile)
            y += block.image.height
            cursors[qid] += 1
        y += fonts.height // 2
        if continuation:
            canvas = None  # A continuation explicitly names its own question and source page.


def _photo(page: Image.Image, background: Image.Image, corners) -> tuple[Image.Image, list]:
    width, height = background.size
    if corners is None:
        # Fit the complete paper into a gentle trapezoid, with all four corners visible.
        paper_h = min(height * .82, width * .76 * page.height / page.width)
        paper_w = paper_h * page.width / page.height
        left, top = (width - paper_w) / 2, (height - paper_h) / 2
        corners = [[left + paper_w * .025, top], [left + paper_w, top + paper_h * .02],
                   [left + paper_w * .98, top + paper_h], [left, top + paper_h * .975]]
    target = np.asarray(corners, dtype=float)
    if target.shape != (4, 2) or not np.isfinite(target).all() or (target[:, 0] < 1).any() or (target[:, 0] > width - 2).any() or (target[:, 1] < 1).any() or (target[:, 1] > height - 2).any():
        raise ValueError("photo_corners must contain four visible in-frame points ordered top-left, top-right, bottom-right, bottom-left.")
    edges = np.roll(target, -1, axis=0) - target
    cross = edges[:, 0] * np.roll(edges[:, 1], -1) - edges[:, 1] * np.roll(edges[:, 0], -1)
    if not (cross > 0).all() or np.linalg.norm(edges, axis=1).min() < 20:
        raise ValueError("Photo corners must form a non-mirrored convex quadrilateral.")
    source = [[0, 0], [page.width - 1, 0], [page.width - 1, page.height - 1], [0, page.height - 1]]
    matrix, rhs = [], []
    for (x, y), (u, v) in zip(target, source):
        matrix.extend([[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]])
        rhs.extend([u, v])
    coefficients = np.linalg.solve(matrix, rhs)
    projected = page.convert("RGBA").transform(background.size, Image.Transform.PERSPECTIVE, coefficients,
                                               Image.Resampling.BICUBIC)
    shadow = Image.new("RGBA", background.size, (0, 0, 0, 0))
    mask = projected.getchannel("A").filter(ImageFilter.GaussianBlur(max(4, min(width, height) // 100))).point(lambda x: round(x * .25))
    shadow.putalpha(mask)
    output = Image.alpha_composite(background.convert("RGBA"), shadow)
    output = Image.alpha_composite(output, projected).convert("RGB")
    return output, target.tolist()


def render_document(task: dict, output_dir: Path, profile: dict | None = None) -> dict:
    """Render reviewed content. This does not solve questions or submit to any platform."""
    settings = dict(DEFAULTS, **(profile or {}))
    if settings["paper"] not in ("ruled", "blank", "grid", "cream") or settings["mode"] not in ("accurate", "photo"):
        raise ValueError("Unknown paper or mode.")
    if settings["handwriting"] not in ("off", "natural"):
        raise ValueError("handwriting must be off or natural.")
    if settings["ink_effect"] not in ("flat", "ballpoint"):
        raise ValueError("ink_effect must be flat or ballpoint.")
    for key in ("font_size", "margin"):
        settings[key] = int(settings[key])
    if not 20 <= settings["font_size"] <= 100 or not 25 <= settings["margin"] <= 250:
        raise ValueError("font_size must be 20–100 and margin 25–250 pixels.")
    if not 1 <= float(settings["line_spacing"]) <= 3 or not -2 <= float(settings["letter_spacing"]) <= 20:
        raise ValueError("Unsupported line_spacing or letter_spacing.")
    if task.get("schema_version") != 1 or not task.get("questions"):
        raise ValueError("Expected schema_version 1 with at least one question.")
    questions = task["questions"]
    qids = [question.get("id") for question in questions]
    if any(not isinstance(qid, str) or not qid for qid in qids) or len(set(qids)) != len(qids):
        raise ValueError("Question ids must be unique nonempty strings.")
    for question in questions:
        if question.get("review", {}).get("status") != "verified":
            raise ValueError(f"Question {question['id']} has not been verified; refuse to create an upload artifact.")
        if not isinstance(question.get("solution"), dict) or not question["solution"].get("final"):
            raise ValueError(f"Question {question['id']} needs a reviewed solution.final.")
    background, background_record = None, None
    if settings["mode"] == "photo":
        if not settings.get("photo_background"):
            raise ValueError("Photo mode requires an existing photo_background; no image service is called by this renderer.")
        background_path = Path(settings["photo_background"]).resolve()
        background = Image.open(background_path).convert("RGB")
        background_record = dict(path=str(background_path), sha256=_sha(background_path.read_bytes()), method="supplied_local_background", image_service_called=False)
    fonts = _Fonts(settings["font"], settings["font_size"], settings["letter_spacing"], settings["handwriting"])
    formulas = _Math(questions, fonts, settings)
    width = PAGE_SIZE[0] - 2 * settings["margin"]
    pages, records = [], []
    workbook = task.get("workbook", {}).get("pages", [])
    if not workbook:
        queues = {q["id"]: _question_blocks(q, width, fonts, formulas, settings) for q in questions}
        _flow(questions, queues, dict.fromkeys(qids, 0), pages, records, fonts, settings, task.get("assignment", {}).get("title", "作业"))
    else:
        originals, region_widths, original_pages = [], {}, {}
        base_dir = Path(task.get("base_dir", task.get("_base_dir", Path.cwd())))
        for entry in workbook:
            path = Path(entry["image"])
            if not path.is_absolute(): path = base_dir / path
            path = path.resolve()
            original = Image.open(path).convert("RGB")
            protected = [_box(box, original.size) for box in entry.get("protected_boxes", [])]
            regions = []
            for region in entry.get("regions", []):
                qid = region["question_id"]
                if qid not in qids: raise ValueError(f"Unknown workbook question_id: {qid}.")
                box = _box(region["box"], original.size)
                if any(_intersects(box, old["box"]) for old in regions):
                    raise ValueError("Workbook writing regions overlap; specify disjoint blank regions.")
                regions.append(dict(question_id=qid, box=box))
                if box[2] - 8 >= max(160, fonts.size * 6) and box[3] >= fonts.height + 8:
                    region_widths[qid] = min(region_widths.get(qid, width), box[2] - 8)
                original_pages.setdefault(qid, []).append(entry.get("page_number", len(originals) + 1))
            originals.append((entry, path, original, regions, protected))
        queues = {q["id"]: _question_blocks(q, region_widths.get(q["id"], width), fonts, formulas, settings, False) for q in questions}
        cursors = dict.fromkeys(qids, 0)
        for entry, path, original, regions, protected in originals:
            canvas = original.copy()
            record = dict(kind="workbook", page_number=entry.get("page_number", len(records) + 1), size=list(canvas.size), dpi=DPI,
                          source_image=str(path), source_sha256=_sha(path.read_bytes()), regions=regions, protected_boxes=protected,
                          original_print_unchanged=True, blocks=[])
            for region in regions:
                qid = region["question_id"]
                x, y, w, h = region["box"]
                cursor_y = y + 4
                while cursors[qid] < len(queues[qid]):
                    block = queues[qid][cursors[qid]]
                    occupied = [x + 4, cursor_y, block.image.width, block.image.height]
                    if block.image.width > w - 8 or cursor_y + block.image.height > y + h - 4 or any(_intersects(occupied, box) for box in protected):
                        break
                    _paste(canvas, block, x + 4, cursor_y, record, qid, settings)
                    cursor_y += block.image.height
                    cursors[qid] += 1
            # Defence in depth: even a changed future drawing routine may not alter protected print.
            for x, y, w, h in protected:
                canvas.paste(original.crop((x, y, x + w, y + h)), (x, y))
            pages.append(canvas)
            records.append(record)
        _flow(questions, queues, cursors, pages, records, fonts, settings,
              task.get("assignment", {}).get("title", "作业"), original_pages)
    if not pages:
        raise ValueError("Nothing could be rendered.")
    upload_pages = []
    for page, record in zip(pages, records):
        if background is not None:
            upload, corners = _photo(page, background, settings.get("photo_corners"))
            record["photo"] = dict(background_record, corners=corners, method="deterministic_four_corner_homography", simulated_photo=True)
        else:
            upload = page.copy()
        upload_pages.append(upload)
    output_dir = Path(output_dir).resolve()
    source_paths = {Path(record["source_image"]).resolve() for record in records if record.get("source_image")}
    if background_record:
        source_paths.add(Path(background_record["path"]))
    for i in range(len(pages)):
        for directory, suffix in (("clean", "png"), ("upload", "jpg")):
            if output_dir / directory / f"page-{i + 1:03d}.{suffix}" in source_paths:
                raise ValueError("Output would overwrite an immutable workbook source image.")
    for directory in ("clean", "upload", "formulas"):
        (output_dir / directory).mkdir(parents=True, exist_ok=True)
    clean_paths, upload_paths = [], []
    for i, (clean, upload, record) in enumerate(zip(pages, upload_pages, records), 1):
        clean_path = output_dir / "clean" / f"page-{i:03d}.png"
        upload_path = output_dir / "upload" / f"page-{i:03d}.jpg"
        clean.save(clean_path, dpi=(DPI, DPI))
        upload.convert("RGB").save(upload_path, quality=96, subsampling=0, dpi=(DPI, DPI))
        clean_paths.append(str(clean_path)); upload_paths.append(str(upload_path))
        record.update(output_index=i, clean=str(clean_path), upload=str(upload_path), clean_sha256=_sha(clean_path.read_bytes()), upload_sha256=_sha(upload_path.read_bytes()))
    clean_pdf, upload_pdf = output_dir / "clean.pdf", output_dir / "upload.pdf"
    pages[0].save(clean_pdf, save_all=True, append_images=pages[1:], resolution=DPI)
    upload_pages[0].save(upload_pdf, save_all=True, append_images=upload_pages[1:], resolution=DPI)
    formula_records = []
    for tex, item in formulas.items.items():
        digest = _sha(tex.encode())
        svg_path = output_dir / "formulas" / f"{digest}.svg"
        svg_path.write_text(item["svg"], encoding="utf-8")
        item["image"].save(svg_path.with_suffix(".png"))
        formula_records.append(dict(tex=tex, sha256=digest, svg=str(svg_path), size=[item["width"], item["height"]],
                                    handwritten_glyphs=item.get("handwritten_glyphs", 0)))
    manifest_path = output_dir / "layout_manifest.json"
    manifest = dict(schema_version=1, assignment=task.get("assignment", {}), profile=settings,
                    paper=dict(format="A4", size_pixels=list(PAGE_SIZE), dpi=DPI), pages=records,
                    fonts=dict(primary=fonts.primary, files=[dict(path=str(path), sha256=_sha(path.read_bytes())) for path in fonts.paths], fallbacks=list(fonts.fallbacks.values()), render_adjustments=fonts.adjustments),
                    math=dict(engine="MathJax 3.2.2 + resvg-js 2.6.2", formulas=formula_records),
                    renderer="deterministic-pillow", image_service_called=False,
                    review_note="Verified input is a rendering precondition, not a guarantee of mathematical correctness.")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dict(page_count=len(pages), clean_pages=clean_paths, upload_pages=upload_paths,
                clean_pdf=str(clean_pdf), upload_pdf=str(upload_pdf), manifest=str(manifest_path), manifest_path=str(manifest_path))
