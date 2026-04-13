from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


ROOT = Path(r"C:\projects\sites\blueprint-rec-2")
LEGACY_ROOT = ROOT
LEGACY_SCRIPT = ROOT / "scripts" / "run_v3_number_pipeline.py"
PADDLE_PYTHON = ROOT / ".venvs" / "paddle-compare" / "Scripts" / "python.exe"
OUT_ROOT = ROOT / ".codex-smoke" / "paddle-compare"
TEST_IMAGES = [
    ROOT / "blueprints-test" / "image001.png",
    ROOT / "blueprints-test" / "page4.png",
    ROOT / "blueprints-test" / "test1.jpg",
]

SIMPLE_LABEL_RE = re.compile(r"^[0-9]{1,4}$")
COMPOUND_LABEL_RE = re.compile(r"^[0-9]{1,4}(?:-[0-9]{1,4})+(?:\([0-9]{1,4}\))?$")
OCR_FIX_MAP = str.maketrans(
    {
        "O": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "L": "1",
        "T": "1",
        "Z": "2",
        "S": "5",
        "B": "8",
        "G": "6",
    }
)


def parse_numeric_label(text: str, label_min: int = 1, label_max: int = 250) -> str | None:
    raw = str(text or "").strip().upper()
    if not raw:
        return None
    fixed = raw.translate(OCR_FIX_MAP)
    fixed = fixed.replace("—", "-").replace("–", "-").replace("−", "-").replace("_", "-")
    fixed = fixed.replace("[", "(").replace("]", ")").replace("{", "(").replace("}", ")")
    fixed = re.sub(r"\s+", "", fixed)
    fixed = fixed.rstrip(".,;:")
    if not fixed or "." in fixed:
        return None
    if COMPOUND_LABEL_RE.fullmatch(fixed):
        return fixed
    if not SIMPLE_LABEL_RE.fullmatch(fixed):
        return None
    value = int(fixed)
    if value < label_min or value > label_max:
        return None
    return str(value)


def load_openrouter_key() -> str | None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    for env_path in [
        ROOT / "services" / "inference" / ".env.local",
        ROOT / ".env",
    ]:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if re.match(r"^\s*OPENROUTER_API_KEY\s*=", line):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


def ensure_dirs(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def run_current_pipeline(image_path: Path, out_dir: Path) -> dict[str, Any]:
    ensure_dirs(out_dir)
    env = os.environ.copy()
    key = load_openrouter_key()
    if key:
        env["OPENROUTER_API_KEY"] = key
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(LEGACY_SCRIPT), "--image", str(image_path), "--out-dir", str(out_dir)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed = time.perf_counter() - started
    (out_dir / "run.stdout.log").write_text(proc.stdout, encoding="utf-8")
    (out_dir / "run.stderr.log").write_text(proc.stderr, encoding="utf-8")
    json_path = out_dir / "markers_v3.json"
    payload = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else {}
    payload["_meta"] = {
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
    }
    return payload


def run_paddle(image_path: Path, out_dir: Path) -> dict[str, Any]:
    ensure_dirs(out_dir)
    code = f"""
import json
from pathlib import Path
from paddleocr import PaddleOCR

ocr = PaddleOCR(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    lang='en',
    device='cpu',
    enable_mkldnn=False,
    enable_hpi=False,
)
result = ocr.predict(input=r'{str(image_path)}')
Path(r'{str(out_dir / "raw_result.json")}').write_text(json.dumps(result, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
print('PADDLE_DONE')
"""
    env = os.environ.copy()
    env["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    started = time.perf_counter()
    proc = subprocess.run(
        [str(PADDLE_PYTHON), "-c", code],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed = time.perf_counter() - started
    (out_dir / "run.stdout.log").write_text(proc.stdout, encoding="utf-8")
    (out_dir / "run.stderr.log").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "elapsed_seconds": elapsed,
            "error": proc.stderr.strip() or proc.stdout.strip(),
            "labels": [],
            "items": [],
        }
    raw_path = out_dir / "raw_result.json"
    if not raw_path.exists():
        return {
            "ok": False,
            "returncode": proc.returncode,
            "elapsed_seconds": elapsed,
            "error": proc.stderr.strip() or proc.stdout.strip() or "raw_result.json not created",
            "labels": [],
            "items": [],
        }
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    entry = raw[0] if raw else {}
    texts = entry.get("rec_texts", []) or []
    scores = entry.get("rec_scores", []) or []
    polys = entry.get("rec_polys", []) or []
    items: list[dict[str, Any]] = []
    for idx, text in enumerate(texts):
        label = parse_numeric_label(str(text))
        if not label:
            continue
        poly = polys[idx] if idx < len(polys) else None
        score = float(scores[idx]) if idx < len(scores) else 0.0
        bbox = None
        if isinstance(poly, list) and poly:
            xs = [float(pt[0]) for pt in poly]
            ys = [float(pt[1]) for pt in poly]
            bbox = {
                "x0": min(xs),
                "y0": min(ys),
                "x1": max(xs),
                "y1": max(ys),
            }
        items.append({"label": label, "score": score, "poly": poly, "bbox": bbox})
    return {
        "ok": True,
        "elapsed_seconds": elapsed,
        "raw_count": len(texts),
        "labels": [item["label"] for item in items],
        "items": items,
        "raw_entry": entry,
    }


def render_paddle_overlay(image_path: Path, paddle: dict[str, Any], overlay_path: Path) -> None:
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    for item in paddle.get("items", []):
        poly = item.get("poly") or []
        if isinstance(poly, list) and len(poly) >= 4:
            pts = [(float(pt[0]), float(pt[1])) for pt in poly]
            draw.line(pts + [pts[0]], fill=(255, 0, 0), width=2)
            x0 = min(p[0] for p in pts)
            y0 = min(p[1] for p in pts)
            draw.text((x0, max(0.0, y0 - 14.0)), str(item["label"]), fill=(255, 0, 0))
    img.save(overlay_path)


def summarize(current: dict[str, Any], paddle: dict[str, Any]) -> dict[str, Any]:
    allowed = list(((current.get("run_debug") or {}).get("allowed_labels") or []))
    current_found = [str(marker.get("label")) for marker in current.get("markers", []) if marker.get("label")]
    paddle_found = list(paddle.get("labels", []))
    current_counter = Counter(current_found)
    paddle_counter = Counter(paddle_found)
    return {
        "allowed_labels": allowed,
        "current_found": current_found,
        "paddle_found": paddle_found,
        "current_missing_vs_allowed": [label for label in allowed if current_counter[label] == 0],
        "paddle_missing_vs_allowed": [label for label in allowed if paddle_counter[label] == 0],
        "current_extra_vs_allowed": [label for label in current_found if allowed and label not in allowed],
        "paddle_extra_vs_allowed": [label for label in paddle_found if allowed and label not in allowed],
        "current_counts": dict(current_counter),
        "paddle_counts": dict(paddle_counter),
        "current_elapsed_seconds": ((current.get("_meta") or {}).get("elapsed_seconds")),
        "paddle_elapsed_seconds": paddle.get("elapsed_seconds"),
        "current_marker_count": len(current_found),
        "paddle_marker_count": len(paddle_found),
    }


def main() -> None:
    ensure_dirs(OUT_ROOT)
    report: dict[str, Any] = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": []}
    md_lines = [
        "# PaddleOCR vs Current Local Path",
        "",
        "Сравнение локального движка PaddleOCR с текущим Gemini-first контуром.",
        "",
    ]
    for image_path in TEST_IMAGES:
        stem = image_path.stem
        case_dir = OUT_ROOT / stem
        current_dir = case_dir / "current"
        paddle_dir = case_dir / "paddle"
        current = run_current_pipeline(image_path, current_dir)
        paddle = run_paddle(image_path, paddle_dir)
        if paddle.get("ok"):
            render_paddle_overlay(image_path, paddle, paddle_dir / "overlay.png")
        summary = summarize(current, paddle)
        case = {
            "image": str(image_path),
            "current_json": str(current_dir / "markers_v3.json"),
            "current_overlay": str(current_dir / "markers_v3.overlay.png"),
            "paddle_overlay": str(paddle_dir / "overlay.png"),
            "summary": summary,
            "paddle_ok": paddle.get("ok", False),
            "paddle_error": paddle.get("error"),
        }
        report["results"].append(case)
        md_lines.extend(
            [
                f"## {image_path.name}",
                "",
                f"- allowed_labels: {', '.join(summary['allowed_labels']) if summary['allowed_labels'] else 'none'}",
                f"- current_found: {', '.join(summary['current_found']) if summary['current_found'] else 'none'}",
                f"- paddle_found: {', '.join(summary['paddle_found']) if summary['paddle_found'] else 'none'}",
                f"- current_missing_vs_allowed: {', '.join(summary['current_missing_vs_allowed']) if summary['current_missing_vs_allowed'] else 'none'}",
                f"- paddle_missing_vs_allowed: {', '.join(summary['paddle_missing_vs_allowed']) if summary['paddle_missing_vs_allowed'] else 'none'}",
                f"- current_elapsed_seconds: {summary['current_elapsed_seconds']}",
                f"- paddle_elapsed_seconds: {summary['paddle_elapsed_seconds']}",
                f"- paddle_ok: {paddle.get('ok', False)}",
                f"- paddle_error: {paddle.get('error') or 'none'}",
                "",
            ]
        )
    json_path = OUT_ROOT / "compare_report.json"
    md_path = OUT_ROOT / "compare_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(str(json_path))
    print(str(md_path))


if __name__ == "__main__":
    main()
