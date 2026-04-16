from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.candidate_recognizer import CandidateSuggestion, DocumentTextRegion
from app.services.candidate_vlm_recognizer import VisionLLMCandidateRecognizer
from app.services.job_runner import _refine_truth_only_row_from_local_crop, _targeted_label_match_score


def test_targeted_label_match_score_accepts_suffix_confusion():
    assert _targeted_label_match_score("29B", "29B") == 1.0
    assert _targeted_label_match_score("298", "29B") > 0.9
    assert _targeted_label_match_score("29A", "29B") == 0.0


def test_refine_truth_only_row_uses_local_bbox(tmp_path, monkeypatch):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (1600, 1800), "white").save(image_path)

    def fake_detect(self, crop, include_tiles=True):  # noqa: ARG001
        return [
            DocumentTextRegion(
                bbox_x=95.0,
                bbox_y=100.0,
                bbox_width=30.0,
                bbox_height=40.0,
                label="42",
                confidence=0.99,
                source="page-bw",
            )
        ]

    monkeypatch.setattr(
        "app.services.candidate_recognizer.DrawingCandidateRecognizer.detect_document_text",
        fake_detect,
    )

    row = _refine_truth_only_row_from_local_crop(
        {"label": "42", "x": 1200.0, "y": 1610.8, "confidence": 0.99},
        raster_path=image_path,
        page_width=1600,
        page_height=1800,
        row_number=7,
        require_local_match=False,
    )

    assert row is not None
    assert row.source_kind == "vlm_locator_refined"
    assert row.bbox is not None
    assert row.center is not None
    assert row.center.x != 1200.0
    assert row.center.y != 1610.8


def test_refine_truth_only_row_requires_local_match_for_duplicate(tmp_path, monkeypatch):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (1600, 1800), "white").save(image_path)

    monkeypatch.setattr(
        "app.services.candidate_recognizer.DrawingCandidateRecognizer.detect_document_text",
        lambda self, crop, include_tiles=True: [],
    )
    monkeypatch.setattr(
        "app.services.candidate_recognizer.DrawingCandidateRecognizer.recognize",
        lambda self, crop, kind: CandidateSuggestion(label=None, confidence=None, source=None),
    )
    monkeypatch.setattr(VisionLLMCandidateRecognizer, "is_enabled", lambda self: False)

    row = _refine_truth_only_row_from_local_crop(
        {"label": "7", "x": 1200.0, "y": 1610.8, "confidence": 0.99},
        raster_path=image_path,
        page_width=1600,
        page_height=1800,
        row_number=7,
        require_local_match=True,
    )

    assert row is None


def test_refine_truth_only_row_uses_suffix_confusion_region(tmp_path, monkeypatch):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (1600, 1800), "white").save(image_path)

    def fake_detect(self, crop, include_tiles=True):  # noqa: ARG001
        return [
            DocumentTextRegion(
                bbox_x=180.0,
                bbox_y=200.0,
                bbox_width=45.0,
                bbox_height=34.0,
                label="298",
                confidence=0.99,
                source="page-bw",
            )
        ]

    monkeypatch.setattr(
        "app.services.candidate_recognizer.DrawingCandidateRecognizer.detect_document_text",
        fake_detect,
    )

    row = _refine_truth_only_row_from_local_crop(
        {"label": "29B", "x": 660.8, "y": 1610.8, "confidence": 0.99},
        raster_path=image_path,
        page_width=1600,
        page_height=1800,
        row_number=7,
        require_local_match=False,
    )

    assert row is not None
    assert row.source_kind == "vlm_locator_refined"
    assert row.bbox is not None
    assert row.note is not None and "суффикс" in row.note
