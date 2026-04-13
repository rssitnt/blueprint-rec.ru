from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Callable

import requests
from PIL import Image, ImageDraw


ROOT = Path(r"C:/projects/sites/blueprint-rec-2")
ENV_PATHS = [
    ROOT / "services" / "inference" / ".env.local",
    ROOT / ".env.local",
    ROOT / ".env",
]
IMAGE_PATH = ROOT / "blueprints-test" / "test1.jpg"
OUT_DIR = ROOT / ".codex-smoke" / "gemini-model-compare" / IMAGE_PATH.stem

PROMPT = (
    "You are reading a technical exploded-view drawing. "
    "Return every visible part-callout number shown on the drawing. "
    "Only return labels that are actually visible in the image. "
    "Repeated labels are allowed if they appear multiple times. "
    "Exclude page numbers, dimensions, non-callout text, and guesses. "
    'Return JSON only in this exact shape: {"labels":["1","2","29A","30A"]}.'
)

LABEL_RE = re.compile(r"^[0-9]{1,4}(?:[A-Z])?(?:-[0-9]{1,4}(?:\([0-9]{1,4}\))?)?$")


def load_env_value(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    for env_path in ENV_PATHS:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if re.match(rf"^\s*{re.escape(name)}\s*=", line):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


def image_to_data_uri(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def image_to_base64(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def parse_labels(output_text: str | None) -> list[str]:
    if not output_text:
        return []
    match = re.search(r"\{.*\}", str(output_text), re.DOTALL)
    raw_json = match.group(0) if match else str(output_text)
    try:
        payload = json.loads(raw_json)
    except Exception:
        return []
    labels = payload.get("labels")
    if not isinstance(labels, list):
        return []
    normalized: list[str] = []
    for item in labels:
        value = str(item or "").strip().upper().replace(" ", "")
        value = value.replace("—", "-").replace("–", "-").replace("−", "-")
        if not value or not LABEL_RE.fullmatch(value):
            continue
        normalized.append(value)
    return normalized


def call_gemini_direct(model: str, image: Image.Image, prompt: str) -> dict:
    api_key = load_env_value("GEMINI_API_KEY")
    if not api_key:
        return {"ok": False, "error": "missing GEMINI_API_KEY", "labels": [], "raw_text": ""}
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": image_to_base64(image),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    started = time.perf_counter()
    try:
        response = requests.post(url, json=body, timeout=90)
        response.raise_for_status()
        payload = response.json()
        output_text = (
            payload.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "labels": [], "raw_text": "", "elapsed_seconds": time.perf_counter() - started}
    return {
        "ok": True,
        "error": None,
        "labels": parse_labels(output_text),
        "raw_text": output_text,
        "elapsed_seconds": time.perf_counter() - started,
    }


def call_openrouter(model: str, image: Image.Image, prompt: str) -> dict:
    api_key = load_env_value("OPENROUTER_API_KEY")
    if not api_key:
        return {"ok": False, "error": "missing OPENROUTER_API_KEY", "labels": [], "raw_text": ""}
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "Return only valid JSON with a top-level labels array.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_to_data_uri(image)}},
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://blueprint-rec.ru",
        "X-Title": "blueprint-rec-2",
    }
    started = time.perf_counter()
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        output_text = payload["choices"][0]["message"]["content"]
    except Exception as exc:
        return {"ok": False, "error": str(exc), "labels": [], "raw_text": "", "elapsed_seconds": time.perf_counter() - started}
    return {
        "ok": True,
        "error": None,
        "labels": parse_labels(output_text),
        "raw_text": output_text,
        "elapsed_seconds": time.perf_counter() - started,
    }


def render_overlay(image: Image.Image, title: str, labels: list[str], overlay_path: Path) -> None:
    canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")
    line = ", ".join(labels) if labels else "none"
    wrapped: list[str] = []
    current = ""
    for chunk in [title, line]:
        if chunk is title:
            wrapped.append(chunk)
            continue
        words = [part.strip() for part in chunk.split(",")]
        current = ""
        for word in words:
            piece = word if not current else f"{current}, {word}"
            if len(piece) > 42 and current:
                wrapped.append(current)
                current = word
            else:
                current = piece
        if current:
            wrapped.append(current)
    box_height = 18 + len(wrapped) * 24
    draw.rounded_rectangle((16, 16, canvas.width - 16, 16 + box_height), radius=18, fill=(24, 14, 10, 224), outline=(214, 140, 92, 255), width=2)
    y = 28
    for index, row in enumerate(wrapped):
        fill = (255, 244, 232, 255) if index == 0 else (242, 208, 178, 255)
        draw.text((30, y), row, fill=fill)
        y += 24
    canvas.save(overlay_path)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.open(IMAGE_PATH).convert("RGB")
    runners: list[tuple[str, Callable[[str, Image.Image, str], dict], str]] = [
        ("gemini-2.5-flash-direct", call_gemini_direct, "gemini-2.5-flash"),
        ("gemini-3.1-flash-image-preview", call_openrouter, "google/gemini-3.1-flash-image-preview"),
        ("gemini-3.1-pro-preview", call_openrouter, "google/gemini-3.1-pro-preview"),
    ]

    report: dict[str, object] = {
        "image": str(IMAGE_PATH),
        "prompt": PROMPT,
        "results": [],
    }

    for slug, runner, model in runners:
        result_dir = OUT_DIR / slug
        result_dir.mkdir(parents=True, exist_ok=True)
        result = runner(model, image, PROMPT)
        overlay_path = result_dir / "overlay.png"
        render_overlay(image, slug, result.get("labels", []), overlay_path)
        (result_dir / "raw_text.txt").write_text(str(result.get("raw_text", "")), encoding="utf-8")
        (result_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        report["results"].append(
            {
                "slug": slug,
                "model": model,
                "labels": result.get("labels", []),
                "ok": result.get("ok", False),
                "error": result.get("error"),
                "elapsed_seconds": result.get("elapsed_seconds"),
                "overlay": str(overlay_path),
            }
        )

    report_path = OUT_DIR / "compare_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
