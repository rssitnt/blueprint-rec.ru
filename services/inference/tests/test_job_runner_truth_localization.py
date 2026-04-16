from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.candidate_recognizer import CandidateSuggestion, DocumentTextRegion
from app.services.candidate_vlm_recognizer import VisionLLMCandidateRecognizer
from app.services.job_runner import (
    _refine_truth_only_row_from_local_crop,
    _targeted_label_match_score,
)
from app.models.job_schemas import DrawingResultRowStatus


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
    assert row.status == DrawingResultRowStatus.FOUND
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
    assert row.status == DrawingResultRowStatus.UNCERTAIN
    assert row.bbox is not None
    assert row.note is not None and "суффикс" in row.note


def test_refine_truth_only_row_promotes_suffix_confusion_when_circle_is_confirmed(tmp_path, monkeypatch):
    image_path = tmp_path / "page.png"
    image = Image.new("RGB", (1600, 1800), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((628, 1582, 674, 1628), outline="black", width=4)
    image.save(image_path)

    def fake_detect(self, crop, include_tiles=True):  # noqa: ARG001
        return [
            DocumentTextRegion(
                bbox_x=139.0,
                bbox_y=143.0,
                bbox_width=42.0,
                bbox_height=35.0,
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
        {"label": "29B", "x": 651.0, "y": 1604.0, "confidence": 0.99},
        raster_path=image_path,
        page_width=1600,
        page_height=1800,
        row_number=7,
        require_local_match=False,
    )

    assert row is not None
    assert row.source_kind == "vlm_locator_refined"
    assert row.status == DrawingResultRowStatus.FOUND
    assert row.note is not None and "круговая выноска" in row.note


def test_refine_truth_only_row_can_use_extended_window_after_confirmation(tmp_path, monkeypatch):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (1600, 1800), "white").save(image_path)

    def fake_detect(self, crop, include_tiles=True):  # noqa: ARG001
        if crop.size[0] < 500:
            return []
        return [
            DocumentTextRegion(
                bbox_x=185.0,
                bbox_y=196.0,
                bbox_width=40.0,
                bbox_height=36.0,
                label="24",
                confidence=0.99,
                source="page-adapt",
            )
        ]

    monkeypatch.setattr(
        "app.services.candidate_recognizer.DrawingCandidateRecognizer.detect_document_text",
        fake_detect,
    )
    monkeypatch.setattr(
        "app.services.candidate_recognizer.DrawingCandidateRecognizer.recognize",
        lambda self, crop, kind: CandidateSuggestion(label=None, confidence=None, source=None),
    )
    monkeypatch.setattr(VisionLLMCandidateRecognizer, "is_enabled", lambda self: True)
    monkeypatch.setattr(
        VisionLLMCandidateRecognizer,
        "recognize",
        lambda self, crop, kind, allowed_labels=None, use_consensus=False, heavy_sheet=False: CandidateSuggestion(
            label="24", confidence=0.99, source="openrouter-vlm:test"
        ),
    )

    row = _refine_truth_only_row_from_local_crop(
        {"label": "24", "x": 641.6, "y": 1139.8, "confidence": 0.99},
        raster_path=image_path,
        page_width=1600,
        page_height=1800,
        row_number=7,
        require_local_match=False,
    )

    assert row is not None
    assert row.source_kind == "vlm_locator_refined"
    assert row.status == DrawingResultRowStatus.FOUND
    assert row.bbox is not None
    assert row.note is not None and "расширенном" in row.note


def test_refine_truth_only_row_promotes_local_confirmation_when_circle_is_confirmed(tmp_path, monkeypatch):
    image_path = tmp_path / "page.png"
    image = Image.new("RGB", (1600, 1800), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((1169, 1284, 1209, 1324), outline="black", width=4)
    image.save(image_path)

    monkeypatch.setattr(
        "app.services.candidate_recognizer.DrawingCandidateRecognizer.detect_document_text",
        lambda self, crop, include_tiles=True: [],
    )
    monkeypatch.setattr(
        "app.services.candidate_recognizer.DrawingCandidateRecognizer.recognize",
        lambda self, crop, kind: CandidateSuggestion(label="7", confidence=0.95, source="page-bw"),
    )
    monkeypatch.setattr(VisionLLMCandidateRecognizer, "is_enabled", lambda self: False)

    row = _refine_truth_only_row_from_local_crop(
        {"label": "7", "x": 1189.0, "y": 1304.0, "confidence": 0.99},
        raster_path=image_path,
        page_width=1600,
        page_height=1800,
        row_number=7,
        require_local_match=False,
    )

    assert row is not None
    assert row.source_kind == "vlm_locator_circle_confirmed"
    assert row.status == DrawingResultRowStatus.FOUND
    assert row.center is not None
    assert abs(row.center.x - 1189.0) <= 6.0
    assert abs(row.center.y - 1304.0) <= 6.0


def test_refine_truth_only_row_can_confirm_label_in_tight_circle_crop(tmp_path, monkeypatch):
    image_path = tmp_path / "page.png"
    image = Image.new("RGB", (1600, 1800), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((1169, 1284, 1209, 1324), outline="black", width=4)
    image.save(image_path)

    monkeypatch.setattr(
        "app.services.candidate_recognizer.DrawingCandidateRecognizer.detect_document_text",
        lambda self, crop, include_tiles=True: [],
    )

    def fake_recognize(self, crop, kind):  # noqa: ARG001
        if crop.size[0] <= 100 and crop.size[1] <= 100:
            return CandidateSuggestion(label="7", confidence=0.94, source="page-bw")
        return CandidateSuggestion(label=None, confidence=None, source=None)

    monkeypatch.setattr(
        "app.services.candidate_recognizer.DrawingCandidateRecognizer.recognize",
        fake_recognize,
    )
    monkeypatch.setattr(VisionLLMCandidateRecognizer, "is_enabled", lambda self: False)

    row = _refine_truth_only_row_from_local_crop(
        {"label": "7", "x": 1189.0, "y": 1304.0, "confidence": 0.99},
        raster_path=image_path,
        page_width=1600,
        page_height=1800,
        row_number=8,
        require_local_match=False,
    )

    assert row is not None
    assert row.source_kind == "vlm_locator_circle_confirmed"
    assert row.status == DrawingResultRowStatus.FOUND
    assert row.note is not None and "узком crop" in row.note


def test_refine_truth_only_row_can_promote_strong_circle_geometry_without_extra_local_text(tmp_path, monkeypatch):
    image_path = tmp_path / "page.png"
    image = Image.new("RGB", (1600, 1800), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((1169, 1284, 1209, 1324), outline="black", width=4)
    image.save(image_path)

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
        {"label": "7", "x": 1189.0, "y": 1304.0, "confidence": 0.99},
        raster_path=image_path,
        page_width=1600,
        page_height=1800,
        row_number=9,
        require_local_match=False,
    )

    assert row is not None
    assert row.source_kind == "vlm_locator_circle_confirmed"
    assert row.status == DrawingResultRowStatus.FOUND
    assert row.note is not None and "геометрия круговой выноски" in row.note
