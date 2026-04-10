from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None
    np = None


@dataclass
class RawCandidate:
    kind: str
    center_x: float
    center_y: float
    bbox_x: float
    bbox_y: float
    bbox_width: float
    bbox_height: float
    score: float


class DrawingCandidateDetector:
    def detect(self, image_path: Path) -> list[RawCandidate]:
        if cv2 is None or np is None:
            return []

        gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            return []

        original_height, original_width = gray.shape[:2]
        upscale = 2 if min(original_width, original_height) <= 1400 else 1
        working = (
            cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
            if upscale > 1
            else gray
        )

        blurred = cv2.GaussianBlur(working, (5, 5), 0)
        _, threshold = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        candidates = [
            *self._detect_circles(working, blurred, threshold, upscale),
            *self._detect_boxes(working, threshold, upscale),
            *self._detect_text_clusters(working, threshold, upscale),
        ]
        candidates = self._dedupe_candidates(candidates)
        candidates = self._limit_candidates_spatially(candidates, original_height)
        candidates.sort(key=lambda item: (item.center_y, item.center_x))
        return candidates

    @staticmethod
    def _limit_candidates_spatially(
        candidates: list[RawCandidate],
        image_height: int,
        max_total: int = 420,
        band_count: int = 6,
    ) -> list[RawCandidate]:
        if len(candidates) <= max_total or image_height <= 0:
            return candidates

        band_count = max(1, band_count)
        band_height = max(1.0, image_height / band_count)
        bands: list[list[RawCandidate]] = [[] for _ in range(band_count)]
        for candidate in candidates:
            band_index = min(band_count - 1, max(0, int(candidate.center_y / band_height)))
            bands[band_index].append(candidate)

        for band in bands:
            band.sort(key=lambda item: item.score, reverse=True)

        limited: list[RawCandidate] = []
        cursor = 0
        while len(limited) < max_total:
            picked_any = False
            for band in bands:
                if cursor < len(band):
                    limited.append(band[cursor])
                    picked_any = True
                    if len(limited) >= max_total:
                        break
            if not picked_any:
                break
            cursor += 1

        return limited

    def _detect_circles(self, working, blurred, threshold, upscale: int) -> list[RawCandidate]:
        min_side = min(working.shape[:2])
        min_radius = max(10, int(min_side * 0.008))
        max_radius = max(min_radius + 6, int(min_side * 0.034))
        min_distance = max(18, int(min_side * 0.018))
        try:
            circles = cv2.HoughCircles(
                blurred,
                cv2.HOUGH_GRADIENT,
                dp=1.15,
                minDist=min_distance,
                param1=100,
                param2=17,
                minRadius=min_radius,
                maxRadius=max_radius,
            )
        except cv2.error:
            return []

        if circles is None:
            return []

        result: list[RawCandidate] = []
        for circle in np.round(circles[0]).astype("int"):
            x, y, radius = int(circle[0]), int(circle[1]), int(circle[2])
            bbox_x = max(0, x - radius)
            bbox_y = max(0, y - radius)
            bbox_w = min(working.shape[1] - bbox_x, radius * 2)
            bbox_h = min(working.shape[0] - bbox_y, radius * 2)
            if bbox_w < 12 or bbox_h < 12:
                continue

            roi = threshold[bbox_y : bbox_y + bbox_h, bbox_x : bbox_x + bbox_w]
            ring_score = self._ring_score(roi)
            if ring_score < 0.06:
                continue

            result.append(
                RawCandidate(
                    kind="circle",
                    center_x=x / upscale,
                    center_y=y / upscale,
                    bbox_x=bbox_x / upscale,
                    bbox_y=bbox_y / upscale,
                    bbox_width=bbox_w / upscale,
                    bbox_height=bbox_h / upscale,
                    score=float(radius * 0.7 + ring_score * 240),
                )
            )
        return result

    def _detect_boxes(self, working, threshold, upscale: int) -> list[RawCandidate]:
        contours, _ = cv2.findContours(threshold, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        min_side = min(working.shape[:2])
        min_size = max(14, int(min_side * 0.016))
        max_size = max(min_size + 10, int(min_side * 0.09))
        candidates: list[RawCandidate] = []

        for contour in contours:
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            approx = cv2.approxPolyDP(contour, 0.045 * perimeter, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue

            x, y, w, h = cv2.boundingRect(approx)
            if w < min_size or h < min_size or w > max_size or h > max_size:
                continue

            aspect_ratio = w / float(h)
            if aspect_ratio < 0.6 or aspect_ratio > 1.6:
                continue

            area = cv2.contourArea(contour)
            box_area = w * h
            if box_area <= 0:
                continue

            fill_ratio = area / float(box_area)
            if fill_ratio < 0.45 or fill_ratio > 0.95:
                continue

            roi = threshold[y : y + h, x : x + w]
            border_score = self._box_border_score(roi)
            if border_score < 0.12:
                continue

            candidates.append(
                RawCandidate(
                    kind="box",
                    center_x=(x + w / 2) / upscale,
                    center_y=(y + h / 2) / upscale,
                    bbox_x=x / upscale,
                    bbox_y=y / upscale,
                    bbox_width=w / upscale,
                    bbox_height=h / upscale,
                    score=float(min(w, h) * 0.65 + border_score * 220),
                )
            )

        return candidates

    def _detect_text_clusters(self, working, threshold, upscale: int) -> list[RawCandidate]:
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(threshold, connectivity=8)
        min_side = min(working.shape[:2])
        min_char_h = max(8, int(min_side * 0.008))
        max_char_h = max(min_char_h + 8, int(min_side * 0.045))
        max_char_w = max(min_char_h + 16, int(min_side * 0.08))

        glyphs: list[dict[str, float]] = []
        for index in range(1, num_labels):
            x = int(stats[index, cv2.CC_STAT_LEFT])
            y = int(stats[index, cv2.CC_STAT_TOP])
            w = int(stats[index, cv2.CC_STAT_WIDTH])
            h = int(stats[index, cv2.CC_STAT_HEIGHT])
            area = int(stats[index, cv2.CC_STAT_AREA])
            if area < 10:
                continue
            if h < min_char_h or h > max_char_h:
                continue
            if w < 2 or w > max_char_w:
                continue
            fill_ratio = area / float(max(w * h, 1))
            if fill_ratio < 0.08 or fill_ratio > 0.92:
                continue
            cx, cy = centroids[index]
            glyphs.append(
                {
                    "x": float(x),
                    "y": float(y),
                    "w": float(w),
                    "h": float(h),
                    "area": float(area),
                    "cx": float(cx),
                    "cy": float(cy),
                }
            )

        if not glyphs:
            return []

        glyphs.sort(key=lambda item: (item["cy"], item["cx"]))
        clusters: list[list[dict[str, float]]] = []
        used = [False] * len(glyphs)

        for index, glyph in enumerate(glyphs):
            if used[index]:
                continue
            cluster = [glyph]
            used[index] = True
            changed = True
            while changed:
                changed = False
                cluster_left = min(item["x"] for item in cluster)
                cluster_top = min(item["y"] for item in cluster)
                cluster_right = max(item["x"] + item["w"] for item in cluster)
                cluster_bottom = max(item["y"] + item["h"] for item in cluster)
                cluster_height = cluster_bottom - cluster_top
                for other_index, other in enumerate(glyphs):
                    if used[other_index]:
                        continue
                    vertical_gate = max(cluster_height, other["h"]) * 0.7
                    horizontal_gap = min(
                        abs(other["x"] - cluster_right),
                        abs(cluster_left - (other["x"] + other["w"])),
                    )
                    same_row = abs(other["cy"] - (cluster_top + cluster_bottom) / 2) <= vertical_gate
                    overlaps_x = not (other["x"] + other["w"] < cluster_left or other["x"] > cluster_right)
                    close_enough = horizontal_gap <= max(18.0, cluster_height * 1.3) or overlaps_x
                    if same_row and close_enough:
                        cluster.append(other)
                        used[other_index] = True
                        changed = True

            if 1 <= len(cluster) <= 8:
                clusters.append(cluster)

        candidates: list[RawCandidate] = []
        max_text_width = max(40, int(min_side * 0.2))
        max_text_height = max(18, int(min_side * 0.06))
        for cluster in clusters:
            left = min(item["x"] for item in cluster)
            top = min(item["y"] for item in cluster)
            right = max(item["x"] + item["w"] for item in cluster)
            bottom = max(item["y"] + item["h"] for item in cluster)
            width = right - left
            height = bottom - top
            if width < 8 or height < min_char_h:
                continue
            if width > max_text_width or height > max_text_height:
                continue

            area_sum = sum(item["area"] for item in cluster)
            fill_ratio = area_sum / float(max(width * height, 1))
            if fill_ratio < 0.06 or fill_ratio > 0.75:
                continue

            score = float(len(cluster) * 45 + min(width, 120) * 0.6 + fill_ratio * 140)
            candidates.append(
                RawCandidate(
                    kind="text",
                    center_x=(left + width / 2) / upscale,
                    center_y=(top + height / 2) / upscale,
                    bbox_x=left / upscale,
                    bbox_y=top / upscale,
                    bbox_width=width / upscale,
                    bbox_height=height / upscale,
                    score=score,
                )
            )

        return candidates

    @staticmethod
    def _ring_score(roi) -> float:
        height, width = roi.shape[:2]
        if height < 8 or width < 8:
            return 0.0

        center_x = width / 2
        center_y = height / 2
        radius = min(width, height) / 2
        yy, xx = np.ogrid[:height, :width]
        distances = np.sqrt((xx - center_x) ** 2 + (yy - center_y) ** 2) / max(radius, 1)
        ring_mask = (distances >= 0.72) & (distances <= 1.12)
        inner_mask = distances <= 0.64
        if not ring_mask.any() or not inner_mask.any():
            return 0.0
        ring_mean = float(roi[ring_mask].mean()) / 255.0
        inner_mean = float(roi[inner_mask].mean()) / 255.0
        return max(0.0, ring_mean * 0.8 + inner_mean * 0.45)

    @staticmethod
    def _box_border_score(roi) -> float:
        height, width = roi.shape[:2]
        if height < 8 or width < 8:
            return 0.0

        border = max(1, int(min(width, height) * 0.12))
        top = roi[:border, :]
        bottom = roi[height - border :, :]
        left = roi[:, :border]
        right = roi[:, width - border :]
        center = roi[border : height - border, border : width - border]
        if center.size == 0:
            return 0.0

        border_mean = float(np.concatenate([top.ravel(), bottom.ravel(), left.ravel(), right.ravel()]).mean()) / 255.0
        center_mean = float(center.mean()) / 255.0
        return max(0.0, border_mean * 0.7 + center_mean * 0.3)

    def _dedupe_candidates(self, candidates: list[RawCandidate]) -> list[RawCandidate]:
        deduped: list[RawCandidate] = []
        for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
            duplicate = False
            for existing in deduped:
                overlap = self._iou(candidate, existing)
                center_distance = ((candidate.center_x - existing.center_x) ** 2 + (candidate.center_y - existing.center_y) ** 2) ** 0.5
                size_gate = max(
                    min(candidate.bbox_width, candidate.bbox_height),
                    min(existing.bbox_width, existing.bbox_height),
                ) * 0.45
                if overlap > 0.36 or center_distance < max(8, size_gate):
                    duplicate = True
                    break
            if not duplicate:
                deduped.append(candidate)
        return deduped

    @staticmethod
    def _iou(left: RawCandidate, right: RawCandidate) -> float:
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
        left_area = left.bbox_width * left.bbox_height
        right_area = right.bbox_width * right.bbox_height
        union = left_area + right_area - intersection
        if union <= 0:
            return 0.0
        return intersection / union
