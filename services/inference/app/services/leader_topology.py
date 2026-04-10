from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from PIL import Image

from ..models.schemas import CalloutCandidate, CandidateKind

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None
    np = None


@dataclass
class ShapeTopologyObservation:
    candidate_id: str
    topology_score: float
    topology_source: str
    leader_anchor_x: float | None = None
    leader_anchor_y: float | None = None


class LeaderTopologyAnalyzer:
    def analyze(
        self,
        image: Image.Image,
        shape_candidates: list[CalloutCandidate],
        header_cutoff: float | None = None,
    ) -> dict[str, ShapeTopologyObservation]:
        if cv2 is None or np is None or not shape_candidates:
            return {}

        try:
            gray = np.array(image.convert("L"))
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            edges = cv2.Canny(blurred, 70, 180, apertureSize=3)
            lines = cv2.HoughLinesP(
                edges,
                rho=1,
                theta=np.pi / 180.0,
                threshold=18,
                minLineLength=max(18, int(min(image.width, image.height) * 0.012)),
                maxLineGap=6,
            )
        except Exception:
            return {}
        if lines is None:
            return {}

        segments = [tuple(map(float, segment[0])) for segment in lines if len(segment) > 0]
        observations: dict[str, ShapeTopologyObservation] = {}
        for candidate in shape_candidates:
            if header_cutoff is not None and candidate.center_y <= header_cutoff:
                continue
            if candidate.kind not in {CandidateKind.CIRCLE, CandidateKind.BOX}:
                continue

            observation = self._best_observation_for_shape(candidate, segments)
            if observation is not None:
                observations[candidate.candidate_id] = observation
        return observations

    def _best_observation_for_shape(
        self,
        candidate: CalloutCandidate,
        segments: list[tuple[float, float, float, float]],
    ) -> ShapeTopologyObservation | None:
        best_score = 0.0
        best_anchor: tuple[float, float] | None = None

        for x1, y1, x2, y2 in segments:
            score, anchor = self._score_segment_for_shape(candidate, x1, y1, x2, y2)
            if score > best_score:
                best_score = score
                best_anchor = anchor

        if best_score < 0.36:
            return None

        return ShapeTopologyObservation(
            candidate_id=candidate.candidate_id,
            topology_score=round(min(best_score, 1.0), 4),
            topology_source="leader-topology:hough",
            leader_anchor_x=None if best_anchor is None else round(best_anchor[0], 2),
            leader_anchor_y=None if best_anchor is None else round(best_anchor[1], 2),
        )

    def _score_segment_for_shape(
        self,
        candidate: CalloutCandidate,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> tuple[float, tuple[float, float] | None]:
        if candidate.kind == CandidateKind.CIRCLE:
            return self._score_segment_for_circle(candidate, x1, y1, x2, y2)
        return self._score_segment_for_box(candidate, x1, y1, x2, y2)

    @staticmethod
    def _score_segment_for_circle(
        candidate: CalloutCandidate,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> tuple[float, tuple[float, float] | None]:
        radius = min(candidate.bbox_width, candidate.bbox_height) / 2.0
        if radius <= 0:
            return 0.0, None

        center_x = candidate.center_x
        center_y = candidate.center_y
        d1 = hypot(x1 - center_x, y1 - center_y)
        d2 = hypot(x2 - center_x, y2 - center_y)
        if d1 <= d2:
            anchor_x, anchor_y, anchor_distance = x1, y1, d1
            far_x, far_y, far_distance = x2, y2, d2
        else:
            anchor_x, anchor_y, anchor_distance = x2, y2, d2
            far_x, far_y, far_distance = x1, y1, d1

        border_gate = max(4.0, radius * 0.45)
        if abs(anchor_distance - radius) > border_gate:
            return 0.0, None
        if far_distance < radius * 1.24:
            return 0.0, None

        length = hypot(x2 - x1, y2 - y1)
        border_score = max(0.0, 1.0 - abs(anchor_distance - radius) / border_gate)
        extension_score = min(1.0, max(0.0, far_distance - radius) / max(radius * 1.8, 1.0))
        length_score = min(1.0, length / max(radius * 3.2, 1.0))

        return border_score * 0.45 + extension_score * 0.35 + length_score * 0.2, (anchor_x, anchor_y)

    @staticmethod
    def _score_segment_for_box(
        candidate: CalloutCandidate,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> tuple[float, tuple[float, float] | None]:
        left = candidate.bbox_x
        top = candidate.bbox_y
        right = candidate.bbox_x + candidate.bbox_width
        bottom = candidate.bbox_y + candidate.bbox_height
        center_x = candidate.center_x
        center_y = candidate.center_y
        margin = max(4.0, min(candidate.bbox_width, candidate.bbox_height) * 0.22)

        endpoint_scores = []
        for px, py, ox, oy in ((x1, y1, x2, y2), (x2, y2, x1, y1)):
            near_vertical_edge = abs(px - left) <= margin or abs(px - right) <= margin
            near_horizontal_edge = abs(py - top) <= margin or abs(py - bottom) <= margin
            if not (near_vertical_edge or near_horizontal_edge):
                continue
            outside = ox < left - margin or ox > right + margin or oy < top - margin or oy > bottom + margin
            if not outside:
                continue
            edge_error = min(abs(px - left), abs(px - right), abs(py - top), abs(py - bottom))
            endpoint_scores.append((edge_error, px, py, ox, oy))

        if not endpoint_scores:
            return 0.0, None

        edge_error, anchor_x, anchor_y, far_x, far_y = min(endpoint_scores, key=lambda item: item[0])
        length = hypot(x2 - x1, y2 - y1)
        far_distance = hypot(far_x - center_x, far_y - center_y)
        near_distance = hypot(anchor_x - center_x, anchor_y - center_y)
        extension = max(0.0, far_distance - near_distance)

        border_score = max(0.0, 1.0 - edge_error / max(margin, 1.0))
        extension_score = min(1.0, extension / max(max(candidate.bbox_width, candidate.bbox_height) * 1.2, 1.0))
        length_score = min(1.0, length / max(max(candidate.bbox_width, candidate.bbox_height) * 1.8, 1.0))
        return border_score * 0.44 + extension_score * 0.34 + length_score * 0.22, (anchor_x, anchor_y)
