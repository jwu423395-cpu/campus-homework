"""Rebuild original synthetic examples and the compact 3-font x 4-paper gallery."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont
import sympy as sp

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campus_homework.environment import DEFAULT_PROFILE, FONT_DIR
from campus_homework.model import require_valid, match_options
from campus_homework.rendering import render_document, style_catalog

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
SAMPLES = ROOT / "assets" / "samples"


def dump(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reviewed(method: str):
    return {"status": "verified", "reviewer": "Codex synthetic-example mathematical cross-check",
            "method": method, "checks": dict.fromkeys(["conditions", "answer", "symbols", "units", "language"], True)}


def question(qid, number, kind, stem, final, steps, method, **extra):
    return {"id": qid, "number": number, "type": kind, "stem": stem,
            "source": {"kind": "synthetic", "complete": True,
                       "description": "本项目原创脱敏演示题；不是实际平台题目、教材摘录或学生作答。"},
            "solution": {"final": final, "steps": steps}, "review": reviewed(method), **extra}


def assignment(key, title, questions):
    task = {"schema_version": 1, "assignment": {"id": key, "title": title, "course": "原创综合练习", "kind": "homework"}, "questions": questions}
    require_valid(task, True)
    return task


def make_tasks():
    # These exact calculations are independent checks of the written solutions.
    x, t = sp.symbols("x t", real=True)
    assert sp.solve(sp.Eq(2*x-3, 7), x) == [5]
    assert sp.expand((-x)**2-x**2) == 0 and sp.diff(x**2, x) == 2*x
    assert (-2)**2 > (-1)**2
    assert 2**2/2 == 2  # Nonzero centripetal acceleration supplies the judgment counterexample.
    assert 3*4 == 12 and 2*(3+4) == 14
    a = sp.Rational(6) / sp.Rational(3, 2)
    v = sp.integrate(a, (t, 0, 3))
    displacement = sp.integrate(a*t, (t, 0, 3))
    assert (a, v, displacement) == (4, 12, 18)
    assert 6*displacement == sp.Rational(1, 2)*sp.Rational(3, 2)*v**2
    k, amplitude = sp.symbols("k amplitude", positive=True)
    assert sp.simplify(sp.Rational(1, 2)*k*x**2 + sp.Rational(1, 2)*k*(amplitude**2-x**2) - k*amplitude**2/2) == 0
    mixed = assignment("synthetic-mixed-v1", "原创脱敏题单 · 六类题型", [
        question("q1", 1, "single", "解方程 2x−3=7，x 等于多少？", {"option_ids": ["x-is-five"]},
                 [{"text": "移项得2x=10，再除以2，得x=5。"}], "SymPy solve得到5；代回2×5−3=7。选项按内容ID映射，不沿用字母。",
                 options=[{"id": "x-is-two", "label": "A", "text": "x=2"}, {"id": "x-is-five", "label": "B", "text": "x=5"},
                          {"id": "x-is-seven", "label": "C", "text": "x=7"}, {"id": "x-is-negative-five", "label": "D", "text": "x=−5"}]),
        question("q2", 2, "multiple", "设实函数f(x)=x²，定义域为全体实数。以下哪些说法正确？", {"option_ids": ["derivative", "even", "nonnegative"]},
                 [{"text": "导数为2x；f(−x)=f(x)，且平方非负。取−2<−1但4>1，可否定全域严格递增。"}],
                 "符号求导与奇偶恒等式展开核验；用−2、−1构造递增说法的反例；平方非负按实数平方定义核对。",
                 options=[{"id": "derivative", "label": "A", "text": "f'(x)=2x"}, {"id": "increasing", "label": "B", "text": "在整个实数域严格递增"},
                          {"id": "even", "label": "C", "text": "f是偶函数"}, {"id": "nonnegative", "label": "D", "text": "对所有实数x均有f(x)≥0"}]),
        question("q3", 3, "judgment", "匀速圆周运动的速率恒定，因此其加速度恒为零。", {"value": False},
                 [{"text": "速率恒定不代表速度矢量恒定，向心加速度大小为v²/r。"}],
                 "对非零速率和有限半径取v=2 m/s、r=2 m，向心加速度2 m/s²构成反例；核对速度方向随时间变化。"),
        question("q4", 4, "fill", "长方形的长为4 m、宽为3 m，其面积为____，周长为____。请按空格顺序填写并带单位。", {"values": ["12 m^2", "14 m"]},
                 [{"text": "面积为4×3=12平方米；周长为2×(4+3)=14米。"}],
                 "整数乘法和加法分别计算12与14；面积量纲为长度平方，周长量纲为长度；顺序与两空一致。", blank_count=2),
        question("q5", 5, "calculation", "质量1.5 kg的物块在光滑水平面上由静止开始，受恒定水平力6 N作用3 s。求加速度、末速度及位移。", {"tex": r"a=4\,\mathrm{m/s^2},\quad v=12\,\mathrm{m/s},\quad s=18\,\mathrm m"},
                 [{"text": "以受力方向为正方向，竖直方向重力与支持力平衡。"}, {"tex": r"a=\frac{F}{m}=4\,\mathrm{m/s^2}"},
                  {"tex": r"v=at=12\,\mathrm{m/s},\qquad s=\frac12at^2=18\,\mathrm m"}, {"text": "再用功与动能关系核对：6×18=1.5×12²/2=108 J。"}],
                 "以精确有理数独立积分a得到v=12、积分v(t)得到s=18，再用功动能定理核对108 J；逐项核对SI单位。"),
        question("q6", 6, "short", "理想弹簧振子以平衡位置为势能零点。为什么它经过平衡位置时速率最大？", {"text": "总机械能守恒。平衡位置的弹性势能最小且为零，动能因此最大；质量不变时，速率也最大。"},
                 [{"tex": r"E=\frac12kA^2,\quad E_p=\frac12kx^2,\quad E_k=\frac12k(A^2-x^2)"},
                  {"text": "在允许区间−A≤x≤A内，x²在x=0处最小，故动能在平衡位置最大。"}],
                 "符号展开验证Ep+Ek=kA²/2恒等于总能量；在|x|≤A内由x²≥0确定Ek的最大值。")
    ])
    live = [{"label": "A", "text": "x=7"}, {"label": "B", "text": "x=−5"}, {"label": "C", "text": "x=2"}, {"label": "D", "text": "x=5"}]
    assert match_options(mixed["questions"][0], live) == ["D"]

    assert sp.sqrt(3**2+4**2) == 5 and sp.Rational(1, 2)*3*4 == 6
    assert sp.integrate(sp.sin(x), (x, -sp.pi, sp.pi)) == 0
    assert all(sp.sin(root) == 0 for root in [-sp.pi, 0, sp.pi])
    assert sp.sin(sp.pi/2) == 1 and sp.sin(-sp.pi/2) == -1
    assert sp.simplify(sp.diff(sp.sqrt(1+x*x), x)-x/sp.sqrt(1+x*x)) == 0
    assert sp.integrate(x/sp.sqrt(1+x*x), (x, 0, 1)) == sp.sqrt(2)-1
    matrix = sp.Matrix([[1, 2], [0, 3]])
    assert matrix.det() == 3 and matrix.inv() == sp.Matrix([[1, -sp.Rational(2, 3)], [0, sp.Rational(1, 3)]])
    assert matrix*matrix.inv() == matrix.inv()*matrix == sp.eye(2)
    assert sp.limit(x*x, x, 0, dir="+") == sp.limit(-x, x, 0, dir="-") == 0
    assert sp.limit(x*x/x, x, 0, dir="+") == 0 and sp.limit(-x/x, x, 0, dir="-") == -1
    render = assignment("synthetic-render-v1", "原创演示 · 公式与三类作图", [
        question("force", 1, "calculation", "光滑水平面上的物块质量为1 kg，受水平向右的恒力10 N。取g=10 m/s²。画受力图并求支持力和加速度。", {"tex": r"N=10\,\mathrm N,\qquad \vec a=10\,\hat{\boldsymbol\imath}\,\mathrm{m/s^2}"},
                 [{"text": "取向右为x正向、向上为y正向。只考虑重力、支持力与外加水平力。"},
                  {"tex": r"\begin{aligned}\sum F_y&=N-mg=0\\\sum F_x&=ma_x=F\\a_x&=\frac{10}{1}=10\,\mathrm{m/s^2}\end{aligned}"}],
                 "分别列竖直与水平牛顿第二定律；独立代回N−mg=0、ma−F=0，单位N/kg=m/s²。", delivery="image"),
        question("triangle", 2, "calculation", "平面直角坐标以cm为单位，A(0,0)、B(4,0)、C(0,3)。画三角形，求BC长度与面积。", {"tex": r"BC=5\,\mathrm{cm},\qquad S=6\,\mathrm{cm^2}"},
                 [{"tex": r"BC=\sqrt{(4-0)^2+(0-3)^2}=5\,\mathrm{cm}"}, {"tex": r"S=\frac12\left|\det\begin{pmatrix}4&0\\0&3\end{pmatrix}\right|=6\,\mathrm{cm^2}"}],
                 "勾股定理与坐标距离公式各得5；底乘高和二维行列式法各得面积6。", delivery="image"),
        question("sine", 3, "calculation", "在闭区间[−π,π]上画y=sin x，写出零点与值域，并计算该区间上的定积分。角度以弧度计。", {"tex": r"x\in\{-\pi,0,\pi\},\qquad y\in[-1,1],\qquad\int_{-\pi}^{\pi}\sin x\,dx=0"},
                 [{"text": "定义域限定为闭区间[−π,π]，两个端点都属于定义域。"}, {"tex": r"\sin x=0\iff x=k\pi,\qquad -\pi\le x\le\pi"},
                  {"tex": r"\int_{-\pi}^{\pi}\sin x\,dx=[-\cos x]_{-\pi}^{\pi}=0"}],
                 "逐个代回三个零点并检查端点；±π/2达到值域上下界；原函数代入与奇函数对称性均给出积分0。", delivery="image"),
        question("calculus", 4, "calculation", "实函数f(x)=√(1+x²)。说明定义域、求导数与积分∫₀¹x/√(1+x²) dx。\n另对实参数曲线r(t)=(t,t²)求速度矢量。", {"tex": r"D_f=\mathbb R,\quad f'(x)=\frac{x}{\sqrt{1+x^2}},\quad I=\sqrt2-1,\quad\vec v(t)=\begin{pmatrix}1\\2t\end{pmatrix},\ t\in\mathbb R"},
                 [{"text": "1+x²始终大于零，根式与分母均有定义，故定义域为全体实数。"},
                  {"tex": r"\begin{aligned}f'(x)&=\frac{2x}{2\sqrt{1+x^2}}\\I&=\int_0^1\frac{x}{\sqrt{1+x^2}}\,dx=\left[\sqrt{1+x^2}\right]_0^1=\sqrt2-1\end{aligned}"},
                  {"tex": r"\vec v(t)=\frac{d\vec r}{dt}=\begin{pmatrix}\frac{d}{dt}t\\\frac{d}{dt}t^2\end{pmatrix}=\begin{pmatrix}1\\2t\end{pmatrix}"}],
                 "SymPy独立求导和精确积分与书面链式法则结果一致；两坐标分别求导；检查1+x²≥1排除分母零点。", delivery="image"),
        question("matrix-piecewise", 5, "calculation", "M=[[1,2],[0,3]]。求det M和M的逆矩阵。另设g(x)在x≥0时为x²、在x<0时为−x，说明定义域以及0处的连续性、可导性。", {"text": "det M=3，逆矩阵如下。g的定义域为全体实数，在0处连续，但左右导数分别为−1与0，因此不可导。", "tex": r"M^{-1}=\begin{pmatrix}1&-\frac23\\0&\frac13\end{pmatrix},\qquad D_g=\mathbb R"},
                 [{"tex": r"M=\begin{pmatrix}1&2\\0&3\end{pmatrix},\quad\det M=3,\quad MM^{-1}=M^{-1}M=I_2"},
                  {"tex": r"g(x)=\begin{cases}x^2,&x\ge0\\-x,&x<0\end{cases}"},
                  {"tex": r"\begin{aligned}g(0)&=0=\lim_{x\to0^-}g(x)=\lim_{x\to0^+}g(x)\\g'_-(0)&=-1,\qquad g'_+(0)=0\end{aligned}"}],
                 "SymPy求逆并以双向矩阵乘法验证单位阵；分段区间覆盖全体实数；左右函数极限均0，差商极限分别−1与0。", delivery="image")
    ])
    render["questions"][0]["solution"]["figures"] = [{"type": "force", "origin": [0, 0], "body": "block", "axes": True,
        "forces": [{"label": "N", "vector": [0, 10]}, {"label": "mg", "vector": [0, -10]}, {"label": "F", "vector": [10, 0]}]}]
    render["questions"][1]["solution"]["figures"] = [{"type": "geometry", "points": {"A": [0, 0], "B": [4, 0], "C": [0, 3]},
        "segments": [{"start": "A", "end": "B", "label": "4 cm"}, {"start": "A", "end": "C", "label": "3 cm"}, ["B", "C"]],
        "arcs": [{"center": "A", "radius": .45, "start": 0, "end": 90, "label": "90°"}]}]
    render["questions"][2]["solution"]["figures"] = [{"type": "function", "expression": "sin(x)", "x_range": [-math.pi, math.pi], "y_range": [-1.4, 1.4]}]

    workbook = assignment("synthetic-workbook-v1", "原创练习册 · 原第7页", [
        question("w1", 1, "calculation", "直角三角形的两条直角边分别为3 cm与4 cm。求斜边及面积。", {"text": "斜边为5 cm，面积为6 cm²。"},
                 [{"tex": r"c=\sqrt{3^2+4^2}=5\,\mathrm{cm},\qquad S=\frac12\cdot3\cdot4=6\,\mathrm{cm^2}"}],
                 "坐标距离法与勾股公式得5；底乘高面积6，量纲分别cm与cm²。", delivery="image"),
        question("w2", 2, "calculation", "光滑水平面上质量2 kg的物块，由静止开始受水平恒力6 N作用4 s。求加速度、末速度、位移，并用功与动能核对。", {"tex": r"a=3\,\mathrm{m/s^2},\quad v=12\,\mathrm{m/s},\quad s=24\,\mathrm m,\quad W=144\,\mathrm J"},
                 [{"text": "取恒力方向为正；竖直方向重力与支持力平衡。"}, {"tex": r"a=\frac Fm=\frac62=3\,\mathrm{m/s^2}"},
                  {"tex": r"v=v_0+at=0+3\times4=12\,\mathrm{m/s}"}, {"tex": r"s=v_0t+\frac12at^2=\frac12\times3\times4^2=24\,\mathrm m"},
                  {"text": "以平均速度法独立复算位移：(0+12)×4/2=24 m。"}, {"tex": r"W=Fs=6\times24=144\,\mathrm J=\frac12mv^2-0"},
                  {"text": "两个方法所得位移一致，且外力做功等于动能增加。"}],
                 "精确有理数计算a=3、v=12、s=24；独立平均速度法得到24；功6×24与动能2×12²/2均144 J。", delivery="image")
    ])
    assert sp.Rational(6, 2)*4 == 12 and sp.Rational(1, 2)*3*4**2 == 24 and 6*24 == sp.Rational(1, 2)*2*12**2
    workbook["workbook"] = {"pages": [{"image": "workbook-source.png", "page_number": 7,
        "regions": [{"question_id": "w1", "box": [75, 640, 1050, 365]}, {"question_id": "w2", "box": [75, 1360, 1050, 95]}],
        "protected_boxes": [[0, 0, 1200, 625], [0, 1020, 1200, 330], [0, 1475, 1200, 225]]}]}
    for item in workbook["questions"]:
        item["source"].update(page=7, evidence_path="workbook-source.png")
    for name, data in [("mixed-task.json", mixed), ("render-task.json", render), ("workbook-task.json", workbook), ("live-options.json", live)]:
        dump(EXAMPLES / name, data)
    dump(EXAMPLES / "photo-profile.json", {**DEFAULT_PROFILE, "paper": "blank", "font": "chenyu",
                                           "mode": "photo", "handwriting": "natural",
                                           "ink": "#202126", "ink_effect": "ballpoint"})
    return render, workbook


def workbook_source():
    canvas = Image.new("RGB", (1200, 1700), "#fffdf6")
    draw = ImageDraw.Draw(canvas)
    font_path = FONT_DIR / "LXGWWenKai-Regular.ttf"
    font = ImageFont.truetype(str(font_path), 34)
    small = ImageFont.truetype(str(font_path), 26)
    heading = ImageFont.truetype(str(font_path), 44)
    draw.text((65, 45), "原创练习册 · 函数与力学", font=heading, fill="#263541")
    draw.text((65, 110), "演示页：全部题目自编，不含真实课程或个人资料。", font=small, fill="#59636b")
    draw.line((65, 157, 1135, 157), fill="#a8b2b9", width=2)
    for y, text in [(190, "1. 直角三角形的两条直角边为3 cm与4 cm。"), (237, "   求斜边及面积。")]:
        draw.text((65, y), text, font=font, fill="#263541")
    draw.line([(200, 525), (520, 525), (200, 315), (200, 525)], fill="#384b59", width=4)
    draw.rectangle((200, 505, 220, 525), outline="#384b59", width=2)
    for xy, label in [((345, 535), "4 cm"), ((105, 400), "3 cm"), ((169, 533), "A"), ((530, 520), "B"), ((173, 275), "C")]:
        draw.text(xy, label, font=small, fill="#263541")
    draw.text((65, 586), "第1题答题空白区（框内可写）", font=small, fill="#687783")
    draw.text((65, 1070), "2. 光滑水平面上质量2 kg的物块从静止开始，", font=font, fill="#263541")
    draw.text((65, 1117), "   受水平恒力6 N作用4 s。求加速度、末速度、", font=font, fill="#263541")
    draw.text((65, 1164), "   位移，并用功与动能核对。", font=font, fill="#263541")
    draw.text((65, 1240), "提示：空白不足时使用标注原页码与题号的续页。", font=small, fill="#687783")
    draw.text((65, 1315), "第2题答题空白区（仅容纳起始内容）", font=small, fill="#687783")
    for left, top, right, bottom in [(60, 625, 1140, 1020), (60, 1350, 1140, 1475)]:
        for x in range(left, right, 18):
            draw.line((x, top, min(x+8, right), top), fill="#b8c3c9", width=1)
            draw.line((x, bottom, min(x+8, right), bottom), fill="#b8c3c9", width=1)
        for y in range(top, bottom, 18):
            draw.line((left, y, left, min(y+8, bottom)), fill="#b8c3c9", width=1)
            draw.line((right, y, right, min(y+8, bottom)), fill="#b8c3c9", width=1)
    draw.text((460, 1605), "— 第 7 页 —", font=small, fill="#59636b")
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    canvas.save(EXAMPLES / "workbook-source.png", dpi=(200, 200))


def gallery(render_task, photo_background: Path | None):
    SAMPLES.mkdir(parents=True, exist_ok=True)
    catalog = style_catalog()
    font_ids = [item["id"] for item in catalog["fonts"]]
    papers = ["blank", "ruled", "grid", "cream"]
    cards, records = {}, []
    preview = assignment("synthetic-style-preview", "中文手写与公式预览", [deepcopy(render_task["questions"][1])])
    q = preview["questions"][0]
    q["stem"] = "字体预览：简谐振动与实验测量。0123456789"
    q["solution"]["steps"] = [{"text": "保持题意、数字和单位准确；图形与推导并列。"},
        {"tex": r"c=\sqrt{3^2+4^2}=5,\quad S=\frac12\times3\times4=6"},
        {"tex": r"\int_0^1x\,dx=\frac12,\quad \vec v=\frac{d\vec r}{dt}"}]
    q["solution"]["final"] = {"text": "模拟手写演示；后备字体按字记录。"}
    with tempfile.TemporaryDirectory(prefix="campus-sample-build-") as directory:
        temporary = Path(directory)
        for fi, font_id in enumerate(font_ids, 1):
            for pi, paper in enumerate(papers, 1):
                profile = {**DEFAULT_PROFILE, "font": font_id, "paper": paper, "font_size": 42,
                           "handwriting": "natural", "ink": "#202126", "ink_effect": "ballpoint"}
                result = render_document(preview, temporary / f"{font_id}-{paper}", profile)
                page = Image.open(result["clean_pages"][0]).convert("RGB")
                name = f"F{fi}-P{pi}-{font_id}-{paper}.jpg"
                page.thumbnail((700, 990), Image.Resampling.LANCZOS)
                page.save(SAMPLES / name, quality=86, optimize=True, dpi=(84, 84))
                cards[fi, pi] = Image.open(result["clean_pages"][0]).convert("RGB").crop((0, 0, 1654, 1600)).resize((405, 392), Image.Resampling.LANCZOS)
                manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
                records.append({"file": name, "font": font_id, "paper": paper, "page_count": result["page_count"],
                    "primary_font_sha256": manifest["fonts"]["files"][0]["sha256"],
                    "fallback_characters": [item["character"] for item in manifest["fonts"]["fallbacks"]],
                    "render_adjustments": [item["action"] for item in manifest["fonts"].get("render_adjustments", [])],
                    "formula_count": len(manifest["math"]["formulas"]),
                    "handwritten_math_glyphs": sum(item["handwritten_glyphs"] for item in manifest["math"]["formulas"]),
                    "handwriting": "natural", "ink": profile["ink"], "ink_effect": profile["ink_effect"],
                    "mode": "accurate", "simulated_handwriting": True})
        if photo_background:
            photo_background = photo_background.expanduser().resolve(strict=True)
            profile = {**json.loads((EXAMPLES / "photo-profile.json").read_text(encoding="utf-8")), "photo_background": str(photo_background)}
            result = render_document(preview, temporary / "photo", profile)
            image = Image.open(result["upload_pages"][0]).convert("RGB")
            image.thumbnail((1000, 1500), Image.Resampling.LANCZOS)
            image.save(SAMPLES / "photo-preview.jpg", quality=88, optimize=True)
            records.append({"file": "photo-preview.jpg", "mode": "photo", "font": profile["font"],
                            "handwriting": profile["handwriting"], "simulated_photo": True,
                            "ink": profile["ink"], "ink_effect": profile["ink_effect"],
                            "background_sha256": hashlib.sha256(photo_background.read_bytes()).hexdigest(), "image_service_called_by_generator": False})
    contact = Image.new("RGB", (1295, 1890), "#e9edf0")
    draw = ImageDraw.Draw(contact)
    label_font = ImageFont.truetype(str(FONT_DIR / "LXGWWenKai-Regular.ttf"), 28)
    draw.text((24, 15), "3款字体 × 4种纸张 · 中文 / 数字 / 公式 / 小图", font=label_font, fill="#263541")
    names = {"blank": "白纸", "ruled": "横线纸", "grid": "方格纸", "cream": "米色纸"}
    for pi, paper in enumerate(papers, 1):
        for fi, font_id in enumerate(font_ids, 1):
            x, y = 20+(fi-1)*425, 65+(pi-1)*450
            draw.rounded_rectangle((x-3, y-3, x+408, y+432), radius=10, fill="white")
            contact.paste(cards[fi, pi], (x, y+38))
            draw.text((x+8, y+1), f"F{fi} {font_id} / P{pi} {names[paper]}", font=label_font, fill="#263541")
    contact.save(SAMPLES / "contact_sheet.jpg", quality=88, optimize=True)
    dump(SAMPLES / "manifest.json", {"synthetic": True, "handwriting": "natural",
                                    "ink": "#202126", "ink_effect": "ballpoint",
                                    "generator": "scripts/make_samples.py", "samples": records,
                                    "note": "MathJax保留公式结构和定位；常见数字、拉丁字母及运算符使用所选手写字体轮廓。墨迹覆盖率与纸面融合为程序模拟。Chenyu缺字回退及内存去hint记录在各样张条目。"})
    total = sum(path.stat().st_size for path in SAMPLES.rglob("*") if path.is_file())
    if total >= 8_000_000:
        raise RuntimeError(f"Sample gallery exceeds 8 MB: {total} bytes")
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photo-background", type=Path, help="Existing local background; no placeholder path or external API is used.")
    parser.add_argument("--fixtures-only", action="store_true", help="Rebuild synthetic task JSON and the original workbook page only.")
    args = parser.parse_args()
    render_task, _ = make_tasks()
    workbook_source()
    if not args.fixtures_only:
        size = gallery(render_task, args.photo_background)
        print(json.dumps({"gallery_bytes": size, "samples": str(SAMPLES), "synthetic": True}))
    else:
        print("Synthetic task fixtures and original workbook page rebuilt; mathematical checks passed.")


if __name__ == "__main__":
    main()
