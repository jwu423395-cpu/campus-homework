"""Local configuration and read-only, capability-oriented environment checks."""

from __future__ import annotations

import ctypes
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

from .fileio import replace_file


ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = ROOT / "assets" / "fonts"
DEFAULT_PROFILE = {
    "paper": "ruled",
    "font": "wenkai",
    "mode": "accurate",
    "ink": "#20252b",
    "font_size": 36,
    "line_spacing": 1.45,
    "letter_spacing": 0,
    "margin": 90,
    "handwriting": "off",
    "ink_effect": "flat",
}
COVERAGE_SAMPLE = "大学物理简谐振动波长频率势能动能计算证明实验测量质量绳子图像学习通答案。繁體中文測試：學習與振動。"


def local_profile_path() -> Path:
    """Keep personal preferences outside the redistributable skill directory."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "campus-homework" / "profile.json"


def _validated_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("Profile must be a JSON object.")
    unknown = set(profile) - (set(DEFAULT_PROFILE) | {"photo_background", "photo_corners"})
    if unknown:
        raise ValueError("Unknown profile fields: " + ", ".join(sorted(unknown)))
    result = {**DEFAULT_PROFILE, **profile}
    for key, choices in (("paper", {"blank", "ruled", "grid", "cream"}),
                         ("mode", {"accurate", "photo"}),
                         ("handwriting", {"off", "natural"}),
                         ("ink_effect", {"flat", "ballpoint"})):
        if not isinstance(result[key], str) or result[key] not in choices:
            raise ValueError(f"Invalid {key}.")
    font = result["font"]
    if not isinstance(font, str) or not font:
        raise ValueError("Font must be a bundled font ID or a local font path.")
    if font not in {"wenkai", "yozai", "chenyu"}:
        font_path = Path(font).expanduser()
        if not font_path.is_file() or font_path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
            raise ValueError("Local font must be an existing TTF, OTF or TTC file.")
        result["font"] = str(font_path.resolve())
    if not isinstance(result["ink"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", result["ink"]):
        raise ValueError("Ink must use #RRGGBB.")
    for key in ("font_size", "line_spacing", "letter_spacing", "margin"):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} must be a finite number.")
        lower, upper = {"font_size": (20, 100), "line_spacing": (1, 3), "letter_spacing": (-2, 20), "margin": (25, 250)}[key]
        if not lower <= value <= upper:
            raise ValueError(f"Invalid {key}.")
    if result.get("photo_background") is not None:
        if not isinstance(result["photo_background"], str) or not Path(result["photo_background"]).expanduser().is_file():
            raise ValueError("Background must be an existing local image file.")
        result["photo_background"] = str(Path(result["photo_background"]).expanduser().resolve())
    if result.get("photo_corners") is not None:
        corners = result["photo_corners"]
        if (not isinstance(corners, list) or len(corners) != 4 or
            any(not isinstance(point, list) or len(point) != 2 or
                any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                    for value in point) for point in corners)):
            raise ValueError("photo_corners must contain four [x, y] coordinate pairs.")
    return result


def load_profile() -> dict:
    path = local_profile_path()
    if not path.exists():
        return dict(DEFAULT_PROFILE)
    return _validated_profile(json.loads(path.read_text(encoding="utf-8")))


def save_profile(profile: dict) -> Path:
    """Prefer atomic replace; explicitly warn on the Windows error-17 fallback."""
    data = _validated_profile(profile)
    path = local_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".profile-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def verify_fonts() -> dict:
    """Check bundled bytes, licenses and an explicit sample, without claiming full CJK coverage."""
    try:
        manifest = json.loads((FONT_DIR / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "fonts": {}, "error": str(exc), "fallback": "wenkai"}
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        TTFont = None
    reports = {}
    for font_id, entry in manifest.items():
        path = FONT_DIR / entry["file"]
        license_path = FONT_DIR / entry["license_file"]
        report = {"path": str(path), "exists": path.is_file(), "sha256_ok": False,
                  "license_ok": False, "coverage": {"status": "requires_fonttools"}}
        if path.is_file():
            report["sha256_ok"] = hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        if license_path.is_file():
            license_text = license_path.read_text(encoding="utf-8")
            report["license_ok"] = "SIL OPEN FONT LICENSE Version 1.1" in license_text and "TERMINATION" in license_text
        if path.is_file() and TTFont is not None:
            try:
                with TTFont(path, lazy=True) as font:
                    cmap = font.getBestCmap() or {}
                    missing = "".join(dict.fromkeys(ch for ch in COVERAGE_SAMPLE if ord(ch) not in cmap))
                    report["coverage"] = {"status": "checked", "sample": COVERAGE_SAMPLE,
                                          "mapped_codepoints": len(cmap), "missing_sample": missing}
            except Exception as exc:
                report["coverage"] = {"status": "invalid_font", "error": str(exc)}
        if path.is_file():
            try:
                from PIL import ImageFont
                probe = ImageFont.truetype(str(path), 32)
                probe.getlength("A")
                report["raw_freetype"] = {"status": "available"}
            except ImportError:
                report["raw_freetype"] = {"status": "requires_pillow"}
            except OSError as exc:
                report["raw_freetype"] = {"status": "requires_renderer_hint_workaround" if "too many function definitions" in str(exc) else "error", "reason": str(exc)}
        report["ok"] = report["exists"] and report["sha256_ok"] and report["license_ok"] and report["coverage"]["status"] != "invalid_font"
        reports[font_id] = report
    return {"ok": set(reports) == {"wenkai", "yozai", "chenyu"} and all(r["ok"] for r in reports.values()),
            "fonts": reports, "fallback": "wenkai",
            "coverage_note": "Only the displayed sample was checked. Missing glyphs require a checked fallback; no font is claimed to cover all Chinese characters."}


def _run(command: list[str], cwd: Path | None = None) -> dict:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=10,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"ok": result.returncode == 0, "output": result.stdout.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": type(exc).__name__}


def _interactive_desktop() -> dict:
    if platform.system() != "Windows":
        return {"status": "unavailable", "reason": "Windows desktop automation requires Windows."}
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        handle = user32.OpenInputDesktop(0, False, 0x0100)
        if handle:
            user32.CloseDesktop(handle)
        return {"status": "available" if handle else "unavailable",
                "reason": "Input desktop handle opened." if handle else "No accessible interactive input desktop."}
    except (AttributeError, OSError):
        return {"status": "unknown", "reason": "Could not query the Windows input desktop."}


def doctor() -> dict:
    locks = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.lstrip().startswith("#"):
            name, version = line.split("==", 1)
            locks[name] = version
    dependencies = {}
    for name, expected in locks.items():
        try:
            version = importlib.metadata.version(name)
            dependencies[name] = {"available": True, "version": version, "expected": expected,
                                  "matches_lock": version == expected}
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = {"available": False, "expected": expected, "matches_lock": False}
    python_ok = sys.version_info >= (3, 11)
    node_path = shutil.which("node")
    node = _run([node_path, "--version"]) if node_path else {"ok": False, "error": "not_found"}
    node["path"] = node_path
    node["minimum_major"] = 22
    version_match = re.match(r"^v(\d+)", node.get("output", ""))
    node["ok"] = node["ok"] and version_match is not None and int(version_match[1]) >= 22
    packages = {}
    package_file = ROOT / "package.json"
    if package_file.is_file():
        package_names = json.loads(package_file.read_text(encoding="utf-8")).get("dependencies", {})
        for name, expected in package_names.items():
            script = "try { console.log(require.resolve(" + json.dumps(name) + ")); } catch(e) { process.exit(1); }"
            check = _run([node_path, "-e", script], ROOT) if node_path else {"ok": False}
            metadata_path = ROOT / "node_modules" / name / "package.json"
            actual = json.loads(metadata_path.read_text(encoding="utf-8")).get("version") if metadata_path.is_file() else None
            packages[name] = {"available": check["ok"], "expected": expected, "version": actual,
                              "matches_lock": actual == expected}
    fonts = verify_fonts()
    desktop = _interactive_desktop()
    installed = python_ok and all(item["available"] and item["matches_lock"] for item in dependencies.values())
    renderer = installed and fonts["ok"] and node["ok"] and bool(packages) and all(item["available"] and item["matches_lock"] for item in packages.values())
    return {
        "checks": {"platform": {"system": platform.system(), "windows": platform.system() == "Windows"},
                   "interactive_desktop": desktop,
                   "python": {"ok": python_ok, "version": platform.python_version(), "executable": sys.executable},
                   "python_packages": dependencies, "node": node, "node_packages": packages, "fonts": fonts},
        "capabilities": {"local_render": {"available": renderer},
                         "desktop_automation": {"available": desktop["status"] == "available"},
                         "image_generation": {"status": "host_provided", "available": None,
                                              "reason": "Availability is supplied by the calling agent host; local doctor cannot inspect its image API or credentials."}},
        "profile_path": str(local_profile_path()),
    }


if __name__ == "__main__":
    print(json.dumps(doctor(), ensure_ascii=False, indent=2))
