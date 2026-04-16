from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int) -> ImageFont.ImageFont:
    for candidate in ("arialbd.ttf", "arial.ttf", "seguisb.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _status_style(status: str, source_kind: str | None) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    source = (source_kind or "").lower()
    if status == "uncertain":
        return (255, 170, 0), (80, 35, 0)
    if "vlm" in source:
        return (64, 196, 255), (14, 41, 63)
    return (255, 82, 82), (68, 18, 18)


def render_overlay(result_path: Path, image_path: Path, output_path: Path) -> Path:
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []

    with Image.open(image_path) as src:
        image = src.convert("RGBA")

    draw = ImageDraw.Draw(image)
    font = _load_font(max(16, min(28, int(min(image.width, image.height) * 0.017))))
    dup_counter = Counter((int(row.get("pageIndex", 0)), str(row.get("label") or "").strip()) for row in rows)

    for row in rows:
        label = str(row.get("label") or "").strip()
        center = row.get("center") or {}
        x = center.get("x")
        y = center.get("y")
        if not label or x is None or y is None:
            continue
        x = float(x)
        y = float(y)
        status = str(row.get("status") or "")
        source_kind = row.get("sourceKind")
        point_fill, badge_fill = _status_style(status, source_kind)
        radius = max(5, int(min(image.width, image.height) * 0.006))
        outline = max(2, radius // 3)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=point_fill, outline=(255, 255, 255), width=outline)

        text = label
        if dup_counter[(int(row.get("pageIndex", 0)), label)] > 1:
            text = f"{label}*"
        text_bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        text_x = min(max(8, x + radius + 6), image.width - text_w - 8)
        text_y = min(max(8, y - text_h - 4), image.height - text_h - 8)
        badge_box = (
            text_x - 6,
            text_y - 3,
            text_x + text_w + 6,
            text_y + text_h + 3,
        )
        draw.rounded_rectangle(badge_box, radius=8, fill=badge_fill, outline=point_fill, width=2)
        draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    render_overlay(Path(args.result_json), Path(args.image), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
