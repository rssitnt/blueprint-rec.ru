from __future__ import annotations

import math
import re
from dataclasses import dataclass

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    import easyocr  # type: ignore
    from rapidocr_onnxruntime import RapidOCR  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None
    np = None
    easyocr = None
    RapidOCR = None

from PIL import Image, ImageDraw, ImageFilter, ImageOps

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover
    RESAMPLE_LANCZOS = Image.LANCZOS


DASH_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2212"
LABEL_PATTERN = re.compile(r"^[0-9]+(?:[A-ZА-ЯЁ]|(?:-[0-9A-ZА-ЯЁ]+(?:\([0-9]+\))?))*$")
LETTER_FIXES = str.maketrans(
    {
        "А": "A",
        "В": "B",
        "С": "C",
        "Е": "E",
        "Н": "H",
        "К": "K",
        "М": "M",
        "О": "O",
        "Р": "P",
        "Т": "T",
        "Х": "X",
        "У": "Y",
    }
)
MAX_ADAPTIVE_BW_PIXELS = 4_200_000
MAX_ADAPTIVE_BW_SIDE = 2400
OCR_CONFUSION_MAP = str.maketrans(
    {
        "I": "1",
        "L": "1",
        "|": "1",
        "!": "1",
        "O": "0",
        "Q": "0",
        "D": "0",
        "B": "8",
    }
)


@dataclass
class CandidateSuggestion:
    label: str | None
    confidence: float | None
    source: str | None


@dataclass
class DocumentTextRegion:
    bbox_x: float
    bbox_y: float
    bbox_width: float
    bbox_height: float
    label: str
    confidence: float
    source: str


class DrawingCandidateRecognizer:
    def __init__(self) -> None:
        self._engine = None
        self._easy_reader = None

    def recognize(self, crop: Image.Image, kind: str) -> CandidateSuggestion:
        if RapidOCR is None or np is None or cv2 is None:
            return CandidateSuggestion(label=None, confidence=None, source=None)

        engine = self._get_engine()
        if engine is None:
            return CandidateSuggestion(label=None, confidence=None, source=None)

        votes: list[tuple[str, float, float, str]] = []

        for variant_name, variant in self._build_variants(crop, kind):
            try:
                result, _ = engine(np.array(variant.convert("RGB")))
            except Exception:
                continue

            for item in result or []:
                if not isinstance(item, (list, tuple)) or len(item) < 3:
                    continue

                raw_text = str(item[1] or "").strip()
                raw_confidence = float(item[2] or 0.0)
                if raw_confidence <= 0:
                    continue

                for label in self._token_candidates(raw_text):
                    if not self._is_plausible_label(label, kind):
                        continue

                    score = self._score_label(label, raw_text, raw_confidence, kind, variant_name)
                    votes.append((label, score, raw_confidence, variant_name))

        best_label, best_confidence, best_source = self._resolve_label_votes(votes) if votes else (None, 0.0, None)

        if self._should_use_easyocr_fallback(crop, kind, best_label, best_confidence):
            easy_votes = self._collect_easyocr_votes(crop, kind)
            override = self._select_easyocr_override(kind, best_label, best_confidence, easy_votes)
            if override is not None:
                return CandidateSuggestion(
                    label=override[0],
                    confidence=max(0.0, min(1.0, round(override[1], 4))),
                    source=override[2],
                )
            votes.extend(easy_votes)

        if not votes:
            return CandidateSuggestion(label=None, confidence=None, source=None)

        best_label, best_confidence, best_source = self._resolve_label_votes(votes)
        return CandidateSuggestion(
            label=best_label,
            confidence=max(0.0, min(1.0, round(best_confidence, 4))),
            source=best_source,
        )

    def detect_document_text(self, image: Image.Image, include_tiles: bool = True) -> list[DocumentTextRegion]:
        if RapidOCR is None or np is None or cv2 is None:
            return []

        engine = self._get_engine()
        if engine is None:
            return []

        regions: list[DocumentTextRegion] = []
        for variant_name, variant, scale, offset_x, offset_y in self._iter_page_ocr_views(image, include_tiles=include_tiles):
            try:
                result, _ = engine(np.array(variant.convert("RGB")))
            except Exception:
                continue

            for item in result or []:
                if not isinstance(item, (list, tuple)) or len(item) < 3:
                    continue
                box = item[0]
                raw_text = str(item[1] or "").strip()
                raw_confidence = float(item[2] or 0.0)
                if raw_confidence <= 0:
                    continue
                try:
                    xs = [float(point[0]) for point in box]
                    ys = [float(point[1]) for point in box]
                except Exception:
                    continue
                if not xs or not ys:
                    continue

                bbox_x = offset_x + min(xs) / scale
                bbox_y = offset_y + min(ys) / scale
                bbox_width = (max(xs) - min(xs)) / scale
                bbox_height = (max(ys) - min(ys)) / scale
                if bbox_width < 4 or bbox_height < 4:
                    continue

                for label in self._token_candidates(raw_text):
                    if not self._is_plausible_label(label, "text"):
                        continue
                    score = self._score_label(label, raw_text, raw_confidence, "text", variant_name)
                    confidence = max(0.0, min(1.0, round(min(score, 1.0), 4)))
                    regions.append(
                        DocumentTextRegion(
                            bbox_x=round(bbox_x, 2),
                            bbox_y=round(bbox_y, 2),
                            bbox_width=round(bbox_width, 2),
                            bbox_height=round(bbox_height, 2),
                            label=label,
                            confidence=confidence,
                            source=variant_name,
                        )
                    )

        resolved = self._resolve_region_hypotheses(regions)
        resolved.sort(key=lambda item: (item.bbox_y, item.bbox_x))
        return resolved[:96]

    def _iter_page_ocr_views(self, image: Image.Image, include_tiles: bool = True):
        min_side = min(image.size)
        gray = ImageOps.grayscale(image.convert("RGB"))
        gray_auto = ImageOps.autocontrast(gray, cutoff=1)
        sharp = gray_auto.filter(ImageFilter.UnsharpMask(radius=1.4, percent=180, threshold=2))
        base_scale = 3 if min_side <= 1200 else 2
        boost_low_res = min_side <= 1100

        yield (
            "page-sharp",
            sharp.resize((sharp.width * base_scale, sharp.height * base_scale), RESAMPLE_LANCZOS),
            float(base_scale),
            0.0,
            0.0,
        )
        yield (
            "page-bw",
            Image.fromarray(
                cv2.threshold(
                    np.array(gray_auto.resize((gray_auto.width * base_scale, gray_auto.height * base_scale), RESAMPLE_LANCZOS)),
                    0,
                    255,
                    cv2.THRESH_BINARY + cv2.THRESH_OTSU,
                )[1]
            ),
            float(base_scale),
            0.0,
            0.0,
        )
        if boost_low_res:
            yield (
                "page-adapt",
                self._adaptive_bw(gray_auto, scale=5),
                5.0,
                0.0,
                0.0,
            )

        if min_side < 500 or not include_tiles:
            return

        tile_size = 560 if min_side <= 1500 else 720
        step = max(240, int(tile_size * 0.68))
        max_x = max(0, image.width - tile_size)
        max_y = max(0, image.height - tile_size)

        x_positions = sorted({0, *range(0, max_x + 1, step), max_x})
        y_positions = sorted({0, *range(0, max_y + 1, step), max_y})

        for top in y_positions:
            for left in x_positions:
                right = min(image.width, left + tile_size)
                bottom = min(image.height, top + tile_size)
                crop = image.crop((left, top, right, bottom))
                crop_gray = ImageOps.grayscale(crop)
                crop_auto = ImageOps.autocontrast(crop_gray, cutoff=1)
                crop_sharp = crop_auto.filter(ImageFilter.UnsharpMask(radius=1.6, percent=190, threshold=2))
                crop_scale = 3 if min(crop.size) <= 700 else 2
                yield (
                    f"tile-sharp-{left}-{top}",
                    crop_sharp.resize((crop_sharp.width * crop_scale, crop_sharp.height * crop_scale), RESAMPLE_LANCZOS),
                    float(crop_scale),
                    float(left),
                    float(top),
                )
                yield (
                    f"tile-bw-{left}-{top}",
                    Image.fromarray(
                        cv2.threshold(
                            np.array(crop_auto.resize((crop_auto.width * crop_scale, crop_auto.height * crop_scale), RESAMPLE_LANCZOS)),
                            0,
                            255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
                        )[1]
                    ),
                    float(crop_scale),
                    float(left),
                    float(top),
                )
                if boost_low_res:
                    yield (
                        f"tile-adapt-{left}-{top}",
                        self._adaptive_bw(crop_auto, scale=5),
                        5.0,
                        float(left),
                        float(top),
                    )

    def _get_engine(self):
        if self._engine is None and RapidOCR is not None:
            try:
                self._engine = RapidOCR()
            except Exception:
                self._engine = False
        return None if self._engine is False else self._engine

    def _get_easy_reader(self):
        if self._easy_reader is None and easyocr is not None:
            try:
                self._easy_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            except Exception:
                self._easy_reader = False
        return None if self._easy_reader is False else self._easy_reader

    @staticmethod
    def _should_use_easyocr_fallback(
        crop: Image.Image,
        kind: str,
        best_label: str | None,
        best_confidence: float,
    ) -> bool:
        if kind not in {"circle", "box"}:
            return False
        if max(crop.size) > 64:
            return False
        if not best_label:
            return True
        if best_label.isdigit() and len(best_label) == 1:
            return True
        return best_confidence < 0.86

    def _collect_easyocr_votes(self, crop: Image.Image, kind: str) -> list[tuple[str, float, float, str]]:
        if easyocr is None or np is None:
            return []

        reader = self._get_easy_reader()
        if reader is None:
            return []

        votes: list[tuple[str, float, float, str]] = []
        for variant_name, variant in self._build_easyocr_variants(crop, kind):
            try:
                result = reader.readtext(
                    np.array(variant.convert("RGB")),
                    detail=1,
                    paragraph=False,
                    allowlist="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-()",
                    text_threshold=0.3,
                    low_text=0.05,
                    mag_ratio=2.0,
                )
            except Exception:
                continue

            for item in result or []:
                if not isinstance(item, (list, tuple)) or len(item) < 3:
                    continue
                raw_text = str(item[1] or "").strip()
                raw_confidence = float(item[2] or 0.0)
                if raw_confidence <= 0:
                    continue
                for label in self._token_candidates(raw_text):
                    if not self._is_plausible_label(label, kind):
                        continue
                    score = self._score_label(label, raw_text, raw_confidence, kind, variant_name)
                    votes.append((label, score, raw_confidence, variant_name))

        return votes

    def _select_easyocr_override(
        self,
        kind: str,
        best_label: str | None,
        best_confidence: float,
        easy_votes: list[tuple[str, float, float, str]],
    ) -> tuple[str, float, str | None] | None:
        if kind not in {"circle", "box"} or not easy_votes:
            return None

        easy_label, easy_confidence, easy_source = self._resolve_label_votes(easy_votes)
        current_label = (best_label or "").strip().upper()

        if not current_label and easy_confidence >= 0.72:
            return easy_label, easy_confidence, easy_source

        if (
            current_label.isdigit()
            and len(current_label) == 1
            and easy_label.startswith(current_label)
            and len(easy_label) > len(current_label)
            and easy_confidence >= 0.75
        ):
            return easy_label, easy_confidence, easy_source

        if (
            current_label.isdigit()
            and len(current_label) == 1
            and easy_label.isdigit()
            and len(easy_label) == 2
            and current_label in easy_label
            and easy_confidence >= 0.9
        ):
            return easy_label, easy_confidence, easy_source

        if (
            ("-" in easy_label or any(char.isalpha() for char in easy_label))
            and current_label
            and easy_label.startswith(current_label)
            and len(easy_label) > len(current_label)
            and easy_confidence >= 0.72
        ):
            return easy_label, easy_confidence, easy_source

        if best_confidence < 0.8 and easy_confidence >= best_confidence + 0.08:
            return easy_label, easy_confidence, easy_source

        return None

    def _build_variants(self, crop: Image.Image, kind: str) -> list[tuple[str, Image.Image]]:
        variants: list[tuple[str, Image.Image]] = []

        base = crop.convert("RGB")
        variants.append(("base", base))

        gray = ImageOps.grayscale(base)
        gray_auto = ImageOps.autocontrast(gray, cutoff=1)
        sharp = gray_auto.filter(ImageFilter.UnsharpMask(radius=1.6, percent=180, threshold=2))
        variants.append(("sharp2x", sharp.resize((sharp.width * 2, sharp.height * 2), RESAMPLE_LANCZOS)))

        binary = gray_auto.resize((gray_auto.width * 3, gray_auto.height * 3), RESAMPLE_LANCZOS)
        binary_np = np.array(binary)
        _, binary_bw = cv2.threshold(binary_np, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(("bw3x", Image.fromarray(binary_bw)))

        if kind == "circle":
            circle_variant = self._mask_circle(gray_auto)
            variants.append(
                (
                    "circle3x",
                    circle_variant.resize((circle_variant.width * 3, circle_variant.height * 3), RESAMPLE_LANCZOS),
                )
            )
            circle_inner = self._crop_circle_inner(circle_variant)
            variants.append(
                (
                    "circle-inner5x",
                    circle_inner.resize((circle_inner.width * 5, circle_inner.height * 5), RESAMPLE_LANCZOS),
                )
            )
            circle_inner_bw = circle_inner.resize((circle_inner.width * 6, circle_inner.height * 6), RESAMPLE_LANCZOS)
            circle_inner_np = np.array(circle_inner_bw)
            _, circle_inner_thresh = cv2.threshold(circle_inner_np, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            variants.append(("circle-inner-bw6x", Image.fromarray(circle_inner_thresh)))
        else:
            inner = self._trim_box(gray_auto)
            variants.append(("inner3x", inner.resize((inner.width * 3, inner.height * 3), RESAMPLE_LANCZOS)))

        return variants

    def _build_easyocr_variants(self, crop: Image.Image, kind: str) -> list[tuple[str, Image.Image]]:
        base = ImageOps.grayscale(crop.convert("RGB"))
        auto = ImageOps.autocontrast(base, cutoff=1)
        variants: list[tuple[str, Image.Image]] = []

        if kind == "circle":
            inner = self._crop_circle_inner(auto)
            variants.append(
                (
                    "easy-circle-inner6x",
                    inner.filter(ImageFilter.UnsharpMask(radius=1.4, percent=230, threshold=1)).resize(
                        (inner.width * 6, inner.height * 6),
                        RESAMPLE_LANCZOS,
                    ),
                )
            )
            variants.append(
                (
                    "easy-circle-inner-adapt8x",
                    self._adaptive_bw(inner, scale=8),
                )
            )
        elif kind == "box":
            inner = self._trim_box(auto)
            variants.append(
                (
                    "easy-box-inner5x",
                    inner.filter(ImageFilter.UnsharpMask(radius=1.2, percent=210, threshold=1)).resize(
                        (inner.width * 5, inner.height * 5),
                        RESAMPLE_LANCZOS,
                    ),
                )
            )

        return variants

    @staticmethod
    def _adaptive_bw(image: Image.Image, scale: int) -> Image.Image:
        target_width = max(1, image.width * scale)
        target_height = max(1, image.height * scale)
        target_pixels = target_width * target_height
        shrink_ratio = 1.0

        if target_pixels > MAX_ADAPTIVE_BW_PIXELS:
            shrink_ratio = min(shrink_ratio, math.sqrt(MAX_ADAPTIVE_BW_PIXELS / float(target_pixels)))
        max_side = max(target_width, target_height)
        if max_side > MAX_ADAPTIVE_BW_SIDE:
            shrink_ratio = min(shrink_ratio, MAX_ADAPTIVE_BW_SIDE / float(max_side))

        target_width = max(1, int(round(target_width * shrink_ratio)))
        target_height = max(1, int(round(target_height * shrink_ratio)))
        upscaled = image.resize((target_width, target_height), RESAMPLE_LANCZOS)

        try:
            array = np.array(upscaled)
            bw = cv2.adaptiveThreshold(
                array,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                11,
            )
        except MemoryError:
            array = np.array(upscaled)
            _, bw = cv2.threshold(array, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return Image.fromarray(bw)

    @staticmethod
    def _mask_circle(image: Image.Image) -> Image.Image:
        width, height = image.size
        mask = Image.new("L", (width, height), 0)
        cx = width / 2
        cy = height / 2
        radius = max(2, int(min(width, height) * 0.42))
        draw = ImageDraw.Draw(mask)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=255)
        canvas = Image.new("L", (width, height), 255)
        canvas.paste(image, mask=mask)
        return canvas

    @staticmethod
    def _trim_box(image: Image.Image) -> Image.Image:
        width, height = image.size
        inset = max(2, int(min(width, height) * 0.12))
        left = min(max(0, inset), max(0, width - 2))
        top = min(max(0, inset), max(0, height - 2))
        right = max(left + 2, width - inset)
        bottom = max(top + 2, height - inset)
        return image.crop((left, top, right, bottom))

    @staticmethod
    def _crop_circle_inner(image: Image.Image) -> Image.Image:
        width, height = image.size
        inset = max(2, int(min(width, height) * 0.23))
        left = min(max(0, inset), max(0, width - 2))
        top = min(max(0, inset), max(0, height - 2))
        right = max(left + 2, width - inset)
        bottom = max(top + 2, height - inset)
        return image.crop((left, top, right, bottom))

    @staticmethod
    def _normalize_token(text: str) -> str:
        token = (text or "").strip().upper().replace(" ", "")
        for dash_char in DASH_CHARS:
            token = token.replace(dash_char, "-")
        token = token.translate(LETTER_FIXES)
        token = token.replace("_", "-")
        token = re.sub(r"^[^0-9A-ZА-ЯЁ]+", "", token)
        token = re.sub(r"[^0-9A-ZА-ЯЁ(),./-]+$", "", token)
        token = token.replace("/", "-")
        return token

    def _token_candidates(self, raw_text: str) -> list[str]:
        base = self._normalize_token(raw_text)
        variants = [
            base,
            base.replace(".", ""),
            base.translate(OCR_CONFUSION_MAP),
            base.replace(".", "").translate(OCR_CONFUSION_MAP),
        ]

        out: list[str] = []
        seen: set[str] = set()

        def add(candidate: str) -> None:
            normalized = candidate.strip().upper()
            if not normalized or normalized in seen:
                return
            if LABEL_PATTERN.fullmatch(normalized):
                seen.add(normalized)
                out.append(normalized)

        for variant in variants:
            add(variant)
            for match in re.finditer(r"[0-9]+(?:[A-ZА-ЯЁ]|(?:-[0-9A-ZА-ЯЁ]+(?:\([0-9]+\))?))*", variant):
                add(match.group(0))
            bracketed = re.fullmatch(r"([0-9]+-[0-9]+)\([0-9]+\)", variant)
            if bracketed:
                add(bracketed.group(1))

        return out

    @staticmethod
    def _is_plausible_label(label: str, kind: str) -> bool:
        if not LABEL_PATTERN.fullmatch(label):
            return False
        if len(label) > 10:
            return False
        if not label[0].isdigit():
            return False
        if label.startswith("0"):
            return False
        if label.isdigit():
            if label.startswith("0"):
                return False
            if kind == "circle" and len(label) > 3:
                return False
            return True
        if re.fullmatch(r"\d+[A-ZА-ЯЁ]", label):
            digit_part = re.match(r"\d+", label)
            return bool(digit_part and len(digit_part.group(0)) <= 2)
        if re.fullmatch(r"\d+(?:-\d+[A-ZА-ЯЁ]?)*(?:\(\d+\))?", label):
            return True
        return False

    @staticmethod
    def _score_label(label: str, raw_text: str, confidence: float, kind: str, variant_name: str) -> float:
        raw_normalized = raw_text.strip().upper()
        score = confidence
        if raw_normalized == label:
            score += 0.18
        if variant_name in {"circle3x", "inner3x", "sharp3x"}:
            score += 0.06
        if "adapt" in variant_name:
            score += 0.05
        if variant_name.startswith("easy-"):
            score += 0.04
        if label.isdigit():
            if kind == "circle" and len(label) <= 2:
                score += 0.08
            if len(label) >= 3:
                score -= 0.04
        if re.search(r"[A-ZА-ЯЁ]", raw_normalized) and not re.search(r"[A-ZА-ЯЁ]", label):
            score -= 0.1
        if len(re.findall(r"\d+", raw_normalized)) >= 3:
            score -= 0.08
        return score

    @classmethod
    def _resolve_label_votes(cls, votes: list[tuple[str, float, float, str]]) -> tuple[str, float, str | None]:
        buckets: dict[str, dict[str, object]] = {}
        for label, score, raw_confidence, source in votes:
            bucket = buckets.setdefault(
                label,
                {
                    "score_sum": 0.0,
                    "best_score": -999.0,
                    "best_confidence": 0.0,
                    "best_source": None,
                    "support": 0,
                    "variant_count": set(),
                },
            )
            bucket["score_sum"] = float(bucket["score_sum"]) + score
            bucket["support"] = int(bucket["support"]) + 1
            cast_variants = bucket["variant_count"]
            assert isinstance(cast_variants, set)
            cast_variants.add(source)
            if score > float(bucket["best_score"]):
                bucket["best_score"] = score
                bucket["best_confidence"] = raw_confidence
                bucket["best_source"] = source

        def source_weight(source_name: str) -> float:
            normalized = (source_name or "").lower()
            if "bw" in normalized:
                return 0.06
            if "adapt" in normalized:
                return 0.05
            if "sharp" in normalized:
                return 0.04
            if "circle" in normalized or "inner" in normalized:
                return 0.03
            return 0.0

        def rank(item: tuple[str, dict[str, object]]) -> tuple[float, float, int, int]:
            label, bucket = item
            variant_count = len(bucket["variant_count"])  # type: ignore[arg-type]
            score = float(bucket["score_sum"]) + float(bucket["best_score"]) * 0.45 + min(int(bucket["support"]), 5) * 0.1
            if "-" in label:
                score += 0.16
            elif any(char.isalpha() for char in label):
                score += 0.08
            elif label.isdigit() and len(label) == 1:
                score -= 0.04
            score += min(variant_count, 4) * 0.04
            score += source_weight(str(bucket["best_source"] or ""))
            return (score, float(bucket["best_confidence"]), variant_count, len(label))

        best_label, best_bucket = max(buckets.items(), key=rank)
        return (
            best_label,
            float(best_bucket["best_confidence"]),
            str(best_bucket["best_source"]) if best_bucket["best_source"] is not None else None,
        )

    def _resolve_region_hypotheses(self, regions: list[DocumentTextRegion]) -> list[DocumentTextRegion]:
        if not regions:
            return []

        clusters: list[list[DocumentTextRegion]] = []
        for region in sorted(regions, key=lambda item: item.confidence, reverse=True):
            cluster = next(
                (
                    existing_cluster
                    for existing_cluster in clusters
                    if any(self._same_region_hypothesis(region, existing) for existing in existing_cluster)
                ),
                None,
            )
            if cluster is None:
                clusters.append([region])
            else:
                cluster.append(region)

        collapsed = [self._pick_best_region_from_cluster(cluster) for cluster in clusters]

        deduped: list[DocumentTextRegion] = []
        for region in sorted(collapsed, key=lambda item: item.confidence, reverse=True):
            duplicate_index = next(
                (
                    index
                    for index, existing in enumerate(deduped)
                    if existing.label == region.label
                    and (
                        self._bbox_overlap(existing, region) >= 0.22
                        or self._text_region_nearby(existing, region)
                    )
                ),
                None,
            )
            if duplicate_index is None:
                deduped.append(region)
                continue
            if region.confidence > deduped[duplicate_index].confidence:
                deduped[duplicate_index] = region

        return deduped

    @classmethod
    def _same_region_hypothesis(cls, left: DocumentTextRegion, right: DocumentTextRegion) -> bool:
        overlap = cls._bbox_overlap(left, right)
        if overlap >= 0.5:
            return True

        left_center_x = left.bbox_x + left.bbox_width / 2
        left_center_y = left.bbox_y + left.bbox_height / 2
        right_center_x = right.bbox_x + right.bbox_width / 2
        right_center_y = right.bbox_y + right.bbox_height / 2
        distance = ((left_center_x - right_center_x) ** 2 + (left_center_y - right_center_y) ** 2) ** 0.5

        avg_width = (left.bbox_width + right.bbox_width) / 2
        avg_height = (left.bbox_height + right.bbox_height) / 2
        width_ratio = min(left.bbox_width, right.bbox_width) / max(left.bbox_width, right.bbox_width, 1.0)
        height_ratio = min(left.bbox_height, right.bbox_height) / max(left.bbox_height, right.bbox_height, 1.0)

        if width_ratio < 0.6 or height_ratio < 0.6:
            return False
        if distance > max(6.0, min(avg_width, avg_height) * 0.45):
            return False

        left_delta = abs(left.bbox_x - right.bbox_x)
        top_delta = abs(left.bbox_y - right.bbox_y)
        return left_delta <= max(5.0, avg_width * 0.25) and top_delta <= max(5.0, avg_height * 0.25)

    @classmethod
    def _pick_best_region_from_cluster(cls, cluster: list[DocumentTextRegion]) -> DocumentTextRegion:
        label_groups: dict[str, list[DocumentTextRegion]] = {}
        for region in cluster:
            label_groups.setdefault(region.label, []).append(region)

        def source_score(source: str) -> float:
            normalized = (source or "").lower()
            if "tile-bw" in normalized or normalized.startswith("page-bw"):
                return 0.08
            if "bw" in normalized:
                return 0.06
            if "sharp" in normalized:
                return 0.04
            return 0.0

        def label_score(label: str, members: list[DocumentTextRegion]) -> tuple[float, float, float, int]:
            support = len(members)
            best_confidence = max(member.confidence for member in members)
            total_confidence = sum(member.confidence for member in members)
            best_source_score = max(source_score(member.source) for member in members)
            score = total_confidence + best_confidence * 0.55 + min(support, 3) * 0.12
            if "-" in label:
                score += 0.14
            elif any(char.isalpha() for char in label):
                score += 0.08
            elif label.isdigit() and len(label) == 1:
                score -= 0.05
            score += best_source_score
            score += min(len(label), 8) * 0.01
            return (score, best_source_score, best_confidence, len(label))

        best_label, best_members = max(
            label_groups.items(),
            key=lambda item: label_score(item[0], item[1]),
        )
        weight_total = sum(max(member.confidence, 0.05) for member in best_members)
        best_member = max(best_members, key=lambda item: item.confidence)
        if weight_total <= 0:
            return best_member

        def weighted_average(attr: str) -> float:
            return sum(getattr(member, attr) * max(member.confidence, 0.05) for member in best_members) / weight_total

        confidence = min(
            0.99,
            max(best_member.confidence, round(sum(member.confidence for member in best_members) / len(best_members), 4))
            + max(0.0, len(best_members) - 1) * 0.03,
        )
        source = best_member.source if len(best_members) == 1 else f"{best_member.source}|cluster-{len(best_members)}"
        return DocumentTextRegion(
            bbox_x=round(weighted_average("bbox_x"), 2),
            bbox_y=round(weighted_average("bbox_y"), 2),
            bbox_width=round(weighted_average("bbox_width"), 2),
            bbox_height=round(weighted_average("bbox_height"), 2),
            label=best_label,
            confidence=round(confidence, 4),
            source=source,
        )

    @staticmethod
    def _bbox_overlap(left: DocumentTextRegion, right: DocumentTextRegion) -> float:
        left_x2 = left.bbox_x + left.bbox_width
        left_y2 = left.bbox_y + left.bbox_height
        right_x2 = right.bbox_x + right.bbox_width
        right_y2 = right.bbox_y + right.bbox_height
        inter_x1 = max(left.bbox_x, right.bbox_x)
        inter_y1 = max(left.bbox_y, right.bbox_y)
        inter_x2 = min(left_x2, right_x2)
        inter_y2 = min(left_y2, right_y2)
        if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
            return 0.0
        intersection = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
        union = left.bbox_width * left.bbox_height + right.bbox_width * right.bbox_height - intersection
        if union <= 0:
            return 0.0
        return intersection / union

    @staticmethod
    def _text_region_nearby(left: DocumentTextRegion, right: DocumentTextRegion) -> bool:
        left_center_x = left.bbox_x + left.bbox_width / 2
        left_center_y = left.bbox_y + left.bbox_height / 2
        right_center_x = right.bbox_x + right.bbox_width / 2
        right_center_y = right.bbox_y + right.bbox_height / 2
        distance = ((left_center_x - right_center_x) ** 2 + (left_center_y - right_center_y) ** 2) ** 0.5
        gate = max(10.0, min(left.bbox_width, left.bbox_height, right.bbox_width, right.bbox_height) * 0.9)
        return distance <= gate
