from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image, ImageDraw

from app.api import sessions as sessions_api
from app.core.config import settings
from app.main import create_app
from app.models.schemas import AnnotationSession, Actor, CalloutCandidate, CandidateAssociation, CandidateKind, Marker, MarkerPointType, MarkerStatus
from app.services.candidate_association import AssociationBuildConfig, CandidateAssociationBuilder
from app.services.candidate_detector import DrawingCandidateDetector, RawCandidate
from app.services.candidate_recognizer import CandidateSuggestion, DocumentTextRegion, DrawingCandidateRecognizer
from app.services.candidate_vlm_recognizer import VisionLLMCandidateRecognizer
from app.services.leader_topology import LeaderTopologyAnalyzer
from app.services.session_store import InMemorySessionStore


def make_png(width: int = 800, height: int = 600) -> BytesIO:
    image = Image.new("RGB", (width, height), color=(245, 244, 238))
    payload = BytesIO()
    image.save(payload, format="PNG")
    payload.seek(0)
    return payload


def make_candidate_png(width: int = 800, height: int = 600) -> BytesIO:
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((120, 120, 200, 200), outline=(0, 0, 0), width=5)
    draw.text((145, 137), "12", fill=(0, 0, 0))
    draw.rectangle((320, 110, 390, 180), outline=(0, 0, 0), width=5)
    draw.text((342, 128), "7", fill=(0, 0, 0))
    payload = BytesIO()
    image.save(payload, format="PNG")
    payload.seek(0)
    return payload


def make_leader_circle_image(width: int = 320, height: int = 240) -> Image.Image:
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((72, 72, 132, 132), outline=(0, 0, 0), width=4)
    draw.line((132, 102, 214, 70), fill=(0, 0, 0), width=3)
    return image


def make_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "var"))
    sessions_api.service = InMemorySessionStore()
    app = create_app()
    return TestClient(app)


def test_create_upload_and_marker_flow(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Breaker assembly"}).json()["session"]
    session_id = created["sessionId"]
    assert created["state"] == "draft"

    uploaded = client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("assembly.png", make_png(), "image/png")},
    ).json()["session"]
    assert uploaded["state"] == "ready"
    assert uploaded["document"]["width"] == 800
    assert uploaded["viewport"]["centerX"] == 400
    assert uploaded["viewport"]["centerY"] == 300

    placed = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "place_marker", "actor": "human", "x": 120, "y": 180, "label": "14-4(1)", "pointType": "top_left"},
    ).json()["session"]
    marker = placed["markers"][0]
    assert marker["status"] == "human_draft"
    assert marker["label"] == "14-4(1)"
    assert marker["pointType"] == "top_left"
    assert placed["summary"]["totalMarkers"] == 1
    assert placed["summary"]["humanCorrected"] == 0

    moved = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "move_marker", "actor": "human", "markerId": marker["markerId"], "deltaX": 10, "deltaY": -20},
    ).json()["session"]
    moved_marker = moved["markers"][0]
    assert moved_marker["x"] == 130
    assert moved_marker["y"] == 160

    updated = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "update_marker", "actor": "human", "markerId": marker["markerId"], "pointType": "center"},
    ).json()["session"]
    assert updated["markers"][0]["pointType"] == "center"

    confirmed = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "confirm_marker", "actor": "human", "markerId": marker["markerId"], "status": "human_corrected"},
    ).json()["session"]
    assert confirmed["markers"][0]["status"] == "human_corrected"
    assert confirmed["summary"]["humanCorrected"] == 1
    assert len(confirmed["actionLog"]) >= 4


def test_viewport_commands_and_marker_delete(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Viewport test"}).json()["session"]
    session_id = created["sessionId"]
    uploaded = client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("drawing.png", make_png(1200, 900), "image/png")},
    ).json()["session"]
    action_log_length = len(uploaded["actionLog"])

    zoomed = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "zoom_to_region", "actor": "ai", "x": 300, "y": 150, "width": 200, "height": 100},
    ).json()["session"]
    assert zoomed["viewport"]["centerX"] == 400
    assert zoomed["viewport"]["centerY"] == 200
    assert zoomed["viewport"]["zoom"] >= 6
    assert len(zoomed["actionLog"]) == action_log_length

    placed = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "place_marker", "actor": "ai", "x": 390, "y": 210, "label": "7"},
    ).json()["session"]
    marker_id = placed["markers"][0]["markerId"]
    assert placed["summary"]["aiDetected"] == 1

    deleted = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "delete_marker", "actor": "human", "markerId": marker_id},
    ).json()["session"]
    assert deleted["markers"] == []
    assert deleted["summary"]["totalMarkers"] == 0

    sessions = client.get("/api/sessions").json()["sessions"]
    assert sessions[0]["sessionId"] == session_id
    assert sessions[0]["markerCount"] == 0


def test_human_edits_demote_confirmed_marker_back_to_draft(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Draft guard"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("drawing.png", make_png(640, 480), "image/png")},
    )

    placed = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "place_marker", "actor": "human", "x": 120, "y": 180, "label": "40"},
    ).json()["session"]
    marker_id = placed["markers"][0]["markerId"]

    confirmed = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "confirm_marker", "actor": "human", "markerId": marker_id, "status": "human_corrected"},
    ).json()["session"]
    assert confirmed["markers"][0]["status"] == "human_corrected"

    moved = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "move_marker", "actor": "human", "markerId": marker_id, "deltaX": 5, "deltaY": -5},
    ).json()["session"]
    assert moved["markers"][0]["status"] == "human_draft"

    reconfirmed = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "confirm_marker", "actor": "human", "markerId": marker_id, "status": "human_corrected"},
    ).json()["session"]
    assert reconfirmed["markers"][0]["status"] == "human_corrected"

    relabeled = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "update_marker", "actor": "human", "markerId": marker_id, "label": "41"},
    ).json()["session"]
    assert relabeled["markers"][0]["status"] == "human_draft"


def test_clear_markers_command(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Clear test"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("drawing.png", make_png(640, 480), "image/png")},
    )

    client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "place_marker", "actor": "human", "x": 100, "y": 120, "label": "1"},
    )
    client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "place_marker", "actor": "human", "x": 180, "y": 220, "label": "2"},
    )

    cleared = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "clear_markers", "actor": "human"},
    ).json()["session"]

    assert cleared["markers"] == []
    assert cleared["summary"]["totalMarkers"] == 0
    assert cleared["summary"]["humanCorrected"] == 0
    assert cleared["actionLog"][-1]["type"] == "markers_cleared"


def test_footer_page_counter_is_filtered_as_non_callout():
    candidate = CalloutCandidate(
        bbox_x=85.5,
        bbox_y=1580.33,
        bbox_width=14.0,
        bbox_height=17.84,
        center_x=92.5,
        center_y=1589.25,
        kind=CandidateKind.TEXT,
        score=263.7,
        suggested_label="4",
        suggested_confidence=0.99,
        suggested_source="tile-sharp-0-1124|cluster-2",
    )

    assert InMemorySessionStore._is_probable_footer_text_candidate(
        candidate,
        neighbor_count=3,
        image_width=1191,
        image_height=1684,
    )


def test_bottom_callout_number_is_not_treated_as_footer():
    candidate = CalloutCandidate(
        bbox_x=660.32,
        bbox_y=1359.5,
        bbox_width=89.26,
        bbox_height=34.4,
        center_x=704.95,
        center_y=1376.7,
        kind=CandidateKind.TEXT,
        score=260.0,
        suggested_label="14",
        suggested_confidence=0.99,
        suggested_source="tile-sharp-1-1024|cluster-1",
    )

    assert not InMemorySessionStore._is_probable_footer_text_candidate(
        candidate,
        neighbor_count=1,
        image_width=1191,
        image_height=1684,
    )


def test_final_text_vlm_does_not_invent_complex_label_from_blank_candidate():
    store = InMemorySessionStore()
    candidate = CalloutCandidate(
        bbox_x=100,
        bbox_y=100,
        bbox_width=24,
        bbox_height=24,
        center_x=112,
        center_y=112,
        kind=CandidateKind.TEXT,
        score=120,
        suggested_label=None,
        suggested_confidence=None,
        suggested_source="page-sharp|cluster-1",
    )

    assert not store._should_replace_candidate_suggestion(
        candidate,
        new_label="14-1",
        new_confidence=0.99,
        new_source="openrouter-vlm:google/gemini-3.1-pro-preview",
    )


def test_targeted_locator_pure_vlm_fallback_restricted_to_simple_numeric_labels():
    store = InMemorySessionStore()
    preview = Image.new("RGB", (1600, 2262), color=(255, 255, 255))

    class StubVlm:
        def is_enabled(self):
            return True

        def locate_labels(self, image, labels, heavy_sheet=False):
            return [{"label": labels[0], "x": 0.5, "y": 0.5, "confidence": 0.95}]

        def recognize(self, *args, **kwargs):
            return CandidateSuggestion(label=None, confidence=0.0, source=None)

    class StubOcr:
        def detect_document_text(self, *args, **kwargs):
            return []

        def recognize(self, *args, **kwargs):
            return CandidateSuggestion(label=None, confidence=0.0, source=None)

    store._candidate_vlm_recognizer = StubVlm()
    store._candidate_recognizer = StubOcr()

    compound = store._build_targeted_missing_label_locator_candidates(
        preview,
        [],
        [],
        allowed_labels={"14-1"},
    )
    numeric = store._build_targeted_missing_label_locator_candidates(
        preview,
        [],
        [],
        allowed_labels={"9"},
    )

    assert compound == []
    assert len(numeric) == 1
    assert numeric[0].suggested_label == "9"


def test_targeted_only_recovery_skips_broad_multi_label_passes_for_normal_sheet():
    store = InMemorySessionStore()
    preview = Image.new("RGB", (1200, 1600), color=(255, 255, 255))
    base_candidate = CalloutCandidate(
        bbox_x=180,
        bbox_y=390,
        bbox_width=40,
        bbox_height=20,
        center_x=200,
        center_y=400,
        kind=CandidateKind.TEXT,
        score=200,
        crop_url="/storage/demo/base-text.png",
        suggested_label=None,
        suggested_confidence=None,
        suggested_source=None,
    )
    recovered_candidate = CalloutCandidate(
        bbox_x=628,
        bbox_y=808,
        bbox_width=24,
        bbox_height=24,
        center_x=640,
        center_y=820,
        kind=CandidateKind.CIRCLE,
        score=260,
        crop_url="/storage/demo/cand-8.png",
        suggested_label="8",
        suggested_confidence=0.97,
        suggested_source="targeted-ocr",
    )

    store._build_low_res_missing_label_document_text_candidates = lambda *args, **kwargs: []
    store._build_low_res_missing_label_locator_candidates = lambda *args, **kwargs: []
    store._build_targeted_missing_label_locator_candidates = lambda *args, **kwargs: []
    store._recover_missing_labels_per_target = lambda *args, **kwargs: [recovered_candidate]
    store._build_low_res_missing_label_ocr_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("broad ocr should not run"))
    store._build_relaxed_text_candidates = lambda *args, **kwargs: []
    store._build_low_res_missing_label_text_candidates = lambda *args, **kwargs: []
    store._build_low_res_missing_label_text_vlm_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("text vlm should not run"))
    store._build_low_res_missing_label_vlm_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("broad vlm should not run"))
    store._build_low_res_letter_tile_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("letter tile should not run"))
    store._build_low_res_context_tile_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("context tile should not run"))
    store._build_low_res_sequence_tile_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sequence tile should not run"))

    result = store._recover_missing_low_res_vocabulary_labels(
        preview,
        [base_candidate],
        [],
        {"8"},
        prefer_targeted_only=True,
    )

    assert [candidate.suggested_label for candidate in result if candidate.suggested_label] == ["8"]


def test_select_best_single_target_candidate_returns_only_top_match():
    store = InMemorySessionStore()
    low = CalloutCandidate(
        bbox_x=88,
        bbox_y=88,
        bbox_width=24,
        bbox_height=24,
        center_x=100,
        center_y=100,
        kind=CandidateKind.CIRCLE,
        score=180,
        crop_url="/storage/demo/cand-low.png",
        suggested_label="9",
        suggested_confidence=0.82,
        suggested_source="circle3x",
    )
    high = CalloutCandidate(
        bbox_x=128,
        bbox_y=88,
        bbox_width=24,
        bbox_height=24,
        center_x=140,
        center_y=100,
        kind=CandidateKind.CIRCLE,
        score=220,
        crop_url="/storage/demo/cand-high.png",
        suggested_label="9",
        suggested_confidence=0.96,
        suggested_source="targeted-ocr",
    )

    selected = store._select_best_single_target_candidates([low, high], "9")

    assert len(selected) == 1
    assert selected[0].candidate_id == high.candidate_id


def test_build_candidates_uses_full_preview_for_vocabulary_and_recovery(tmp_path, monkeypatch):
    store = InMemorySessionStore()
    document_path = tmp_path / "page.png"
    image = Image.new("RGB", (1200, 1600), color=(255, 255, 255))
    image.putpixel((0, 0), (17, 33, 65))
    image.save(document_path)

    session = AnnotationSession(title="demo")
    raw_circle = RawCandidate(
        bbox_x=640,
        bbox_y=820,
        bbox_width=30,
        bbox_height=30,
        center_x=655,
        center_y=835,
        score=0.9,
        kind="circle",
    )

    monkeypatch.setattr(store._candidate_detector, "detect", lambda *_args, **_kwargs: [raw_circle])
    monkeypatch.setattr(store._candidate_recognizer, "detect_document_text", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(store, "_infer_effective_header_cutoff", lambda *_args, **_kwargs: 540.0)
    monkeypatch.setattr(
        store,
        "_mask_top_region",
        lambda preview_image, cutoff: Image.new("RGB", preview_image.size, color=(0, 0, 0)),
    )
    monkeypatch.setattr(store, "_compose_candidates", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(store, "_refine_final_candidates_with_ocr", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(store, "_refine_final_candidates_with_vlm", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(store, "_apply_label_vocabulary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(store, "_mark_candidates_against_existing_markers", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(store, "_assign_candidate_conflicts", lambda *_args, **_kwargs: None)

    calls: dict[str, tuple[int, int, int] | None] = {"vocab_pixel": None, "recovery_pixel": None}

    def fake_extract_label_vocabulary(preview_image, *args, **kwargs):
        calls["vocab_pixel"] = preview_image.getpixel((0, 0))
        return {"1", "2", "3"}

    def fake_recover(preview_image, *args, **kwargs):
        calls["recovery_pixel"] = preview_image.getpixel((0, 0))
        return []

    monkeypatch.setattr(store._candidate_vlm_recognizer, "extract_label_vocabulary", fake_extract_label_vocabulary)
    monkeypatch.setattr(store, "_recover_missing_low_res_vocabulary_labels", fake_recover)

    _, _, label_vocabulary = store._build_candidates(session, document_path)

    assert label_vocabulary == {"1", "2", "3"}
    assert calls["vocab_pixel"] == (17, 33, 65)
    assert calls["recovery_pixel"] == (17, 33, 65)


def test_recover_missing_labels_per_target_tries_text_path_before_tiles():
    store = InMemorySessionStore()
    preview = Image.new("RGB", (1200, 1600), color=(255, 255, 255))
    recovered_candidate = CalloutCandidate(
        bbox_x=628,
        bbox_y=808,
        bbox_width=24,
        bbox_height=24,
        center_x=640,
        center_y=820,
        kind=CandidateKind.TEXT,
        score=260,
        crop_url="/storage/demo/cand-8.png",
        suggested_label="8",
        suggested_confidence=0.97,
        suggested_source="targeted-text-ocr",
    )

    store._build_relaxed_text_candidates = lambda *args, **kwargs: []
    store._build_low_res_missing_label_text_candidates = lambda *args, **kwargs: [recovered_candidate]
    store._build_low_res_missing_label_text_vlm_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("text vlm should not run"))
    store._build_low_res_missing_label_ocr_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("circle ocr should not run"))
    store._build_low_res_missing_label_vlm_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("circle vlm should not run"))
    store._build_low_res_letter_tile_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("letter tile should not run"))
    store._build_low_res_context_tile_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("context tile should not run"))

    result = store._recover_missing_labels_per_target(preview, [], [], {"8"})

    assert [candidate.suggested_label for candidate in result if candidate.suggested_label] == ["8"]


def test_missing_label_text_vlm_candidates_do_not_leak_existing_labels(monkeypatch):
    store = InMemorySessionStore()
    preview = Image.new("RGB", (1200, 1600), color=(255, 255, 255))
    text_candidate = CalloutCandidate(
        bbox_x=198,
        bbox_y=148,
        bbox_width=24,
        bbox_height=24,
        center_x=210,
        center_y=160,
        kind=CandidateKind.TEXT,
        score=208,
        crop_url="/storage/demo/text-13.png",
        suggested_label="13",
        suggested_confidence=0.92,
        suggested_source="ocr",
    )

    monkeypatch.setattr(store, "_candidate_is_strongly_covered", lambda *args, **kwargs: False)
    monkeypatch.setattr(store, "_infer_header_cutoff", lambda *args, **kwargs: None)
    monkeypatch.setattr(store, "_build_candidate_ocr_crop", lambda *args, **kwargs: preview.crop((0, 0, 64, 64)))
    monkeypatch.setattr(store, "_build_candidate_vlm_crop", lambda *args, **kwargs: preview.crop((0, 0, 64, 64)))
    monkeypatch.setattr(store._candidate_recognizer, "recognize", lambda *args, **kwargs: CandidateSuggestion(label=None, confidence=0.0, source=None))
    monkeypatch.setattr(store._candidate_vlm_recognizer, "is_enabled", lambda: True)
    monkeypatch.setattr(store._candidate_vlm_recognizer, "recognize", lambda *args, **kwargs: CandidateSuggestion(label=None, confidence=0.0, source=None))

    result = store._build_low_res_missing_label_text_vlm_candidates(
        preview,
        [text_candidate],
        [],
        allowed_labels={"8"},
    )

    assert result == []


def test_locate_labels_uses_best_result_across_image_variants(monkeypatch):
    recognizer = VisionLLMCandidateRecognizer()
    preview = Image.new("RGB", (1200, 1600), color=(255, 255, 255))
    variant_a = Image.new("RGB", (1200, 1600), color=(10, 10, 10))
    variant_b = Image.new("RGB", (1200, 1600), color=(20, 20, 20))

    monkeypatch.setattr(recognizer, "_openrouter_enabled", lambda: True)
    monkeypatch.setattr(recognizer, "_prepare_vocabulary_images", lambda image, heavy_sheet=False: [variant_a, variant_b])

    def fake_chat(payload_image, **kwargs):
        if payload_image is variant_a:
            return ('{"items":[{"label":"3","x":0.1,"y":0.2,"confidence":0.51}]}', "model-a")
        return ('{"items":[{"label":"3","x":0.4,"y":0.6,"confidence":0.93}]}', "model-b")

    monkeypatch.setattr(recognizer, "_openrouter_chat_json", fake_chat)

    result = recognizer.locate_labels(preview, ["3"], heavy_sheet=True)

    assert result == [{"label": "3", "x": 0.4, "y": 0.6, "confidence": 0.93}]


def test_cap_header_cutoff_limits_over_aggressive_top_mask():
    assert InMemorySessionStore._cap_header_cutoff(542.88, 1600) == 304.0
    assert InMemorySessionStore._cap_header_cutoff(120.0, 1600) == 120.0
    assert InMemorySessionStore._cap_header_cutoff(None, 1600) is None


def test_cap_header_cutoff_keeps_first_real_callout_row_on_tall_pages():
    # On the benchmark PDF render, labels 3 and 4 sit around y=443..476.
    # A 22% cap masked them out; the lower cap must preserve that row.
    assert InMemorySessionStore._cap_header_cutoff(542.88, 2262) == pytest.approx(429.78)


def test_build_candidates_keeps_first_real_callout_row_after_header_cap(tmp_path, monkeypatch):
    store = InMemorySessionStore()
    document_path = tmp_path / "page.png"
    Image.new("RGB", (1600, 2262), color=(255, 255, 255)).save(document_path)
    session = AnnotationSession(title="demo")

    monkeypatch.setattr(store._candidate_detector, "detect", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        store._candidate_recognizer,
        "detect_document_text",
        lambda *_args, **_kwargs: [
            DocumentTextRegion(
                bbox_x=118.2,
                bbox_y=427.0,
                bbox_width=24.0,
                bbox_height=31.4,
                label="3",
                confidence=0.99,
                source="page-sharp|cluster-2",
            ),
            DocumentTextRegion(
                bbox_x=117.0,
                bbox_y=459.5,
                bbox_width=27.3,
                bbox_height=33.1,
                label="4",
                confidence=0.99,
                source="page-sharp|cluster-2",
            ),
        ],
    )
    monkeypatch.setattr(store, "_infer_effective_header_cutoff", lambda *_args, **_kwargs: 542.88)
    monkeypatch.setattr(store._candidate_vlm_recognizer, "extract_label_vocabulary", lambda *_args, **_kwargs: set())
    monkeypatch.setattr(store, "_recover_missing_low_res_vocabulary_labels", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(store, "_refine_final_candidates_with_ocr", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(store, "_refine_final_candidates_with_vlm", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(store, "_apply_label_vocabulary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(store, "_mark_candidates_against_existing_markers", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(store, "_assign_candidate_conflicts", lambda *_args, **_kwargs: None)

    captured: dict[str, list[str]] = {}

    def fake_compose(_session_id, _preview_image, base_candidates):
        captured["labels"] = sorted(
            {
                candidate.suggested_label
                for candidate in base_candidates
                if candidate.suggested_label
            }
        )
        return [], []

    monkeypatch.setattr(store, "_compose_candidates", fake_compose)

    store._build_candidates(session, document_path)

    assert captured["labels"] == ["3", "4"]


def test_vocabulary_focus_crop_removes_header_and_footer_bands():
    store = InMemorySessionStore()
    preview = Image.new("RGB", (1200, 1600), color=(255, 255, 255))
    header_text = CalloutCandidate(
        bbox_x=120,
        bbox_y=120,
        bbox_width=140,
        bbox_height=40,
        center_x=190,
        center_y=140,
        kind=CandidateKind.TEXT,
        score=200,
        crop_url="/storage/demo/header.png",
        suggested_label="impulse",
        suggested_confidence=0.99,
        suggested_source="doc-text",
    )
    drawing_shape = CalloutCandidate(
        bbox_x=180,
        bbox_y=360,
        bbox_width=760,
        bbox_height=920,
        center_x=560,
        center_y=820,
        kind=CandidateKind.BOX,
        score=220,
        crop_url="/storage/demo/body.png",
        suggested_label=None,
        suggested_confidence=None,
        suggested_source=None,
    )
    footer_text = CalloutCandidate(
        bbox_x=40,
        bbox_y=1538,
        bbox_width=16,
        bbox_height=18,
        center_x=48,
        center_y=1547,
        kind=CandidateKind.TEXT,
        score=180,
        crop_url="/storage/demo/footer.png",
        suggested_label="4",
        suggested_confidence=0.99,
        suggested_source="doc-text",
    )

    crop = store._build_vocabulary_focus_crop(preview, [header_text, drawing_shape, footer_text], header_cutoff=220)

    assert crop is not None
    assert crop.size[0] < preview.size[0]
    assert crop.size[1] < preview.size[1]


def test_delete_session_removes_it_and_its_files(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Delete me"}).json()["session"]
    session_id = created["sessionId"]

    uploaded = client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("drawing.png", make_png(320, 240), "image/png")},
    ).json()["session"]

    session_dir = Path(settings.storage_dir) / session_id
    stored_files = list(session_dir.iterdir())

    assert uploaded["document"]["storageUrl"].startswith(f"/storage/{session_id}/")
    assert session_dir.exists()
    assert len(stored_files) == 1

    deleted = client.delete(f"/api/sessions/{session_id}")
    assert deleted.status_code == 204

    listing = client.get("/api/sessions").json()["sessions"]
    assert all(item["sessionId"] != session_id for item in listing)
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert not session_dir.exists()


def test_delete_session_removes_store_entry_and_storage(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Delete test"}).json()["session"]
    session_id = created["sessionId"]

    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("drawing.png", make_png(320, 240), "image/png")},
    )

    session_dir = tmp_path / "var" / session_id
    assert session_dir.is_dir()
    assert any(session_dir.iterdir())

    deleted = client.delete(f"/api/sessions/{session_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""

    assert not session_dir.exists()

    missing = client.get(f"/api/sessions/{session_id}")
    assert missing.status_code == 404

    sessions = client.get("/api/sessions").json()["sessions"]
    assert all(item["sessionId"] != session_id for item in sessions)


def test_export_session_archive_contains_markup_and_table(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Export demo"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("drawing.png", make_png(640, 480), "image/png")},
    )
    client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "place_marker", "actor": "human", "x": 120, "y": 160, "label": "14", "pointType": "center"},
    )
    client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "place_marker", "actor": "human", "x": 240, "y": 260, "label": "15", "pointType": "top_left"},
    )

    exported = client.get(f"/api/sessions/{session_id}/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/zip")
    assert "attachment;" in exported.headers["content-disposition"]

    with zipfile.ZipFile(BytesIO(exported.content)) as archive:
        names = set(archive.namelist())
        assert len(names) == 2
        assert any(name.endswith("/markers.xlsx") for name in names)
        assert any(name.endswith("/annotated.png") for name in names)

        workbook_name = next(name for name in names if name.endswith("/markers.xlsx"))
        workbook = load_workbook(BytesIO(archive.read(workbook_name)))
        sheet = workbook.active
        assert sheet["A1"].value == "Цифра"
        assert sheet["B1"].value == "X"
        assert sheet["C1"].value == "Y"
        assert sheet["A2"].value == "14"
        assert sheet["B2"].value == 120
        assert sheet["C2"].value == 160
        assert sheet["A3"].value == "15"
        assert sheet["B3"].value == 240
        assert sheet["C3"].value == 260

        annotated_name = next(name for name in names if name.endswith("/annotated.png"))
        annotated = Image.open(BytesIO(archive.read(annotated_name)))
        assert annotated.size == (640, 480)


def test_export_is_blocked_when_pipeline_has_unresolved_error_conflicts(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Blocked export"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("drawing.png", make_png(640, 480), "image/png")},
    )

    store = sessions_api.service
    assert isinstance(store, InMemorySessionStore)
    session = store._get_session(session_id)
    session.candidates = [
        CalloutCandidate.model_validate(
            {
                "candidateId": "cand-29a",
                "kind": "circle",
                "centerX": 120,
                "centerY": 140,
                "bboxX": 100,
                "bboxY": 120,
                "bboxWidth": 40,
                "bboxHeight": 40,
                "score": 180,
                "cropUrl": None,
                "suggestedLabel": "29A",
                "suggestedConfidence": 0.91,
                "suggestedSource": "tile-vlm:consensus",
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        )
    ]

    exported = client.get(f"/api/sessions/{session_id}/export")

    assert exported.status_code == 400
    assert "Export blocked until pipeline conflicts are resolved" in exported.json()["detail"]
    assert "missing_vocab_label:29A" in exported.json()["detail"]


def test_detect_candidates_and_review_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(
        DrawingCandidateDetector,
        "detect",
        lambda self, image_path: [RawCandidate("circle", 160, 160, 128, 128, 64, 64, 188)],
    )
    monkeypatch.setattr(
        DrawingCandidateRecognizer,
        "detect_document_text",
        lambda self, image: [DocumentTextRegion(146, 146, 28, 28, "12", 0.96, "test-region")],
    )
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Candidate review"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("candidate.png", make_candidate_png(), "image/png")},
    )

    detected = client.post(f"/api/sessions/{session_id}/detect-candidates")
    assert detected.status_code == 200
    detected_session = detected.json()["session"]
    assert len(detected_session["candidates"]) >= 1
    assert "candidateAssociations" in detected_session
    first_candidate = detected_session["candidates"][0]
    assert first_candidate["reviewStatus"] == "pending"
    assert first_candidate["cropUrl"]
    assert any(item["suggestedLabel"] for item in detected_session["candidates"])

    placed = client.post(
        f"/api/sessions/{session_id}/commands",
        json={
            "type": "place_marker",
            "actor": "human",
            "candidateId": first_candidate["candidateId"],
            "pointType": "center",
            "label": "12",
        },
    )
    assert placed.status_code == 200
    placed_session = placed.json()["session"]
    accepted_candidate = next(item for item in placed_session["candidates"] if item["candidateId"] == first_candidate["candidateId"])
    assert accepted_candidate["reviewStatus"] == "accepted"

    second_pending = next((item for item in placed_session["candidates"] if item["reviewStatus"] == "pending"), None)
    if second_pending:
        rejected = client.post(f"/api/sessions/{session_id}/candidates/{second_pending['candidateId']}/reject")
        assert rejected.status_code == 200
        rejected_session = rejected.json()["session"]
        rejected_candidate = next(item for item in rejected_session["candidates"] if item["candidateId"] == second_pending["candidateId"])
        assert rejected_candidate["reviewStatus"] == "rejected"


def test_place_marker_from_candidate_prefills_suggested_label(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Candidate suggestion"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("candidate.png", make_png(640, 480), "image/png")},
    )

    store = sessions_api.service
    assert isinstance(store, InMemorySessionStore)

    fake_candidate = {
        "candidateId": "candidate-1",
        "kind": "circle",
        "centerX": 120,
        "centerY": 180,
        "bboxX": 100,
        "bboxY": 160,
        "bboxWidth": 40,
        "bboxHeight": 40,
        "score": 0.91,
        "cropUrl": "/storage/demo/candidate-1.png",
        "suggestedLabel": "28",
        "suggestedConfidence": 0.88,
        "suggestedSource": "sharp3x",
        "reviewStatus": "pending",
        "conflictGroup": None,
        "conflictCount": 0,
    }
    session = store._get_session(session_id)
    session.candidates = [CalloutCandidate.model_validate(fake_candidate)]

    placed = client.post(
        f"/api/sessions/{session_id}/commands",
        json={
            "type": "place_marker",
            "actor": "human",
            "candidateId": "candidate-1",
            "pointType": "center",
        },
    )
    assert placed.status_code == 200
    marker = placed.json()["session"]["markers"][0]
    assert marker["label"] == "28"
    assert marker["confidence"] == 0.88


def test_auto_annotate_creates_ai_markers_from_detected_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(
        DrawingCandidateDetector,
        "detect",
        lambda self, image_path: [RawCandidate("circle", 160, 160, 128, 128, 64, 64, 188)],
    )
    monkeypatch.setattr(
        DrawingCandidateRecognizer,
        "detect_document_text",
        lambda self, image: [DocumentTextRegion(146, 146, 28, 28, "12", 0.96, "test-region")],
    )
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Auto annotate"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("candidate.png", make_candidate_png(), "image/png")},
    )

    auto_run = client.post(f"/api/sessions/{session_id}/auto-annotate")
    assert auto_run.status_code == 200
    session = auto_run.json()["session"]

    assert len(session["candidates"]) >= 1
    assert len(session["markers"]) >= 1
    assert any(marker["createdBy"] == "ai" for marker in session["markers"])
    assert any(marker["status"] in {"ai_detected", "ai_review"} for marker in session["markers"])
    assert "candidateAssociations" in session
    assert "pageVocabulary" in session
    assert "pipelineConflicts" in session
    assert "missingLabels" in session


def test_auto_annotate_demotes_association_ambiguity_to_ai_review(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Auto annotate ambiguity"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("candidate.png", make_png(640, 480), "image/png")},
    )

    def fake_build_candidates(self, current_session, document_path):
        candidate = CalloutCandidate.model_validate(
            {
                "candidateId": "final-12",
                "kind": "circle",
                "centerX": 160,
                "centerY": 160,
                "bboxX": 120,
                "bboxY": 130,
                "bboxWidth": 86,
                "bboxHeight": 60,
                "score": 330,
                "cropUrl": None,
                "suggestedLabel": "12",
                "suggestedConfidence": 0.94,
                "suggestedSource": "test-region+circle+assoc",
                "topologyScore": 0.44,
                "topologySource": "leader-topology",
                "leaderAnchorX": 186,
                "leaderAnchorY": 152,
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        )
        associations = [
            CandidateAssociation.model_validate(
                {
                    "shapeCandidateId": "circle-a",
                    "textCandidateId": "text-12",
                    "shapeKind": "circle",
                    "label": "12",
                    "score": 0.81,
                    "geometryScore": 0.77,
                    "topologyScore": 0.4,
                    "source": "shape-text:circle:direct",
                    "bboxX": 120,
                    "bboxY": 130,
                    "bboxWidth": 60,
                    "bboxHeight": 60,
                }
            ),
            CandidateAssociation.model_validate(
                {
                    "shapeCandidateId": "circle-b",
                    "textCandidateId": "text-12",
                    "shapeKind": "circle",
                    "label": "12",
                    "score": 0.78,
                    "geometryScore": 0.75,
                    "topologyScore": 0.38,
                    "source": "shape-text:circle:direct",
                    "bboxX": 146,
                    "bboxY": 134,
                    "bboxWidth": 60,
                    "bboxHeight": 60,
                }
            ),
        ]
        return [candidate], associations, set()

    monkeypatch.setattr(InMemorySessionStore, "_build_candidates", fake_build_candidates)

    auto_run = client.post(f"/api/sessions/{session_id}/auto-annotate")
    assert auto_run.status_code == 200
    session = auto_run.json()["session"]

    assert len(session["markers"]) == 1
    assert session["markers"][0]["status"] == "ai_review"
    final_candidate = next(item for item in session["candidates"] if item["candidateId"] == "final-12")
    assert final_candidate["reviewStatus"] == "pending"
    assert any(conflict["type"] == "association_ambiguity" for conflict in session["pipelineConflicts"])


def test_auto_annotate_demotes_candidate_ambiguity_to_ai_review(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Auto annotate candidate ambiguity"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("candidate.png", make_png(640, 480), "image/png")},
    )

    def fake_build_candidates(self, current_session, document_path):
        top_candidate = CalloutCandidate.model_validate(
            {
                "candidateId": "cand-top",
                "kind": "circle",
                "centerX": 160,
                "centerY": 160,
                "bboxX": 124,
                "bboxY": 128,
                "bboxWidth": 64,
                "bboxHeight": 64,
                "score": 330,
                "cropUrl": None,
                "suggestedLabel": "12",
                "suggestedConfidence": 0.94,
                "suggestedSource": "ocr+circle",
                "topologyScore": 0.42,
                "topologySource": "leader-topology",
                "leaderAnchorX": 184,
                "leaderAnchorY": 150,
                "reviewStatus": "pending",
                "conflictGroup": "candidate-conflict-1",
                "conflictCount": 2,
            }
        )
        weaker_candidate = CalloutCandidate.model_validate(
            {
                "candidateId": "cand-weaker",
                "kind": "circle",
                "centerX": 166,
                "centerY": 162,
                "bboxX": 130,
                "bboxY": 130,
                "bboxWidth": 62,
                "bboxHeight": 62,
                "score": 250,
                "cropUrl": None,
                "suggestedLabel": "12",
                "suggestedConfidence": 0.72,
                "suggestedSource": "ocr+circle",
                "topologyScore": 0.34,
                "topologySource": "leader-topology",
                "leaderAnchorX": 188,
                "leaderAnchorY": 152,
                "reviewStatus": "pending",
                "conflictGroup": "candidate-conflict-1",
                "conflictCount": 2,
            }
        )
        return [top_candidate, weaker_candidate], [], set()

    monkeypatch.setattr(InMemorySessionStore, "_build_candidates", fake_build_candidates)

    auto_run = client.post(f"/api/sessions/{session_id}/auto-annotate")
    assert auto_run.status_code == 200
    session = auto_run.json()["session"]

    assert len(session["markers"]) == 1
    assert session["markers"][0]["status"] == "ai_review"
    top_candidate = next(item for item in session["candidates"] if item["candidateId"] == "cand-top")
    weaker_candidate = next(item for item in session["candidates"] if item["candidateId"] == "cand-weaker")
    assert top_candidate["reviewStatus"] == "pending"
    assert weaker_candidate["reviewStatus"] == "pending"
    assert any(conflict["type"] == "candidate_ambiguity" for conflict in session["pipelineConflicts"])
    assert session["summary"]["aiDetected"] == 0
    assert session["summary"]["aiReview"] == 1


def test_pipeline_state_exposes_missing_vocabulary_labels(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Pipeline state"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("candidate.png", make_png(640, 480), "image/png")},
    )

    store = sessions_api.service
    assert isinstance(store, InMemorySessionStore)
    session = store._get_session(session_id)
    session.candidates = [
        CalloutCandidate.model_validate(
            {
                "candidateId": "cand-29a",
                "kind": "circle",
                "centerX": 120,
                "centerY": 140,
                "bboxX": 100,
                "bboxY": 120,
                "bboxWidth": 40,
                "bboxHeight": 40,
                "score": 180,
                "cropUrl": None,
                "suggestedLabel": "29A",
                "suggestedConfidence": 0.91,
                "suggestedSource": "tile-vlm:consensus",
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        )
    ]
    store._refresh_pipeline_state(session, explicit_vocabulary={"29A", "29B"}, include_missing_labels=True)

    vocabulary_labels = [entry.label for entry in session.page_vocabulary]
    assert "29A" in vocabulary_labels
    assert "29B" in vocabulary_labels
    assert session.missing_labels == ["29A"]
    assert any(conflict.type == "missing_vocab_label" and conflict.label == "29A" for conflict in session.pipeline_conflicts)


def test_pipeline_state_flags_nearby_duplicate_markers(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Duplicate markers"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("candidate.png", make_png(640, 480), "image/png")},
    )

    store = sessions_api.service
    assert isinstance(store, InMemorySessionStore)
    session = store._get_session(session_id)
    session.markers = [
        Marker(
            label="34",
            x=180,
            y=240,
            point_type=MarkerPointType.CENTER,
            status=MarkerStatus.AI_REVIEW,
            confidence=0.83,
            created_by=Actor.AI,
            updated_by=Actor.AI,
        ),
        Marker(
            label="34",
            x=202,
            y=252,
            point_type=MarkerPointType.CENTER,
            status=MarkerStatus.AI_REVIEW,
            confidence=0.79,
            created_by=Actor.AI,
            updated_by=Actor.AI,
        ),
    ]

    store._refresh_pipeline_state(session, include_missing_labels=False)

    assert any(conflict.type == "duplicate_label_nearby" and conflict.label == "34" for conflict in session.pipeline_conflicts)


def test_pipeline_state_flags_association_ambiguity(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    created = client.post("/api/sessions", json={"title": "Association ambiguity"}).json()["session"]
    session_id = created["sessionId"]
    client.post(
        f"/api/sessions/{session_id}/document",
        files={"file": ("candidate.png", make_png(640, 480), "image/png")},
    )

    store = sessions_api.service
    assert isinstance(store, InMemorySessionStore)
    session = store._get_session(session_id)
    session.candidates = [
        CalloutCandidate.model_validate(
            {
                "candidateId": "text-12",
                "kind": "text",
                "centerX": 160,
                "centerY": 160,
                "bboxX": 148,
                "bboxY": 148,
                "bboxWidth": 24,
                "bboxHeight": 24,
                "score": 210,
                "cropUrl": None,
                "suggestedLabel": "12",
                "suggestedConfidence": 0.93,
                "suggestedSource": "ocr",
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        ),
        CalloutCandidate.model_validate(
            {
                "candidateId": "circle-a",
                "kind": "circle",
                "centerX": 150,
                "centerY": 160,
                "bboxX": 120,
                "bboxY": 130,
                "bboxWidth": 60,
                "bboxHeight": 60,
                "score": 180,
                "cropUrl": None,
                "suggestedLabel": None,
                "suggestedConfidence": None,
                "suggestedSource": None,
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        ),
        CalloutCandidate.model_validate(
            {
                "candidateId": "circle-b",
                "kind": "circle",
                "centerX": 176,
                "centerY": 164,
                "bboxX": 146,
                "bboxY": 134,
                "bboxWidth": 60,
                "bboxHeight": 60,
                "score": 176,
                "cropUrl": None,
                "suggestedLabel": None,
                "suggestedConfidence": None,
                "suggestedSource": None,
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        ),
        CalloutCandidate.model_validate(
            {
                "candidateId": "text-13",
                "kind": "text",
                "centerX": 210,
                "centerY": 160,
                "bboxX": 198,
                "bboxY": 148,
                "bboxWidth": 24,
                "bboxHeight": 24,
                "score": 208,
                "cropUrl": None,
                "suggestedLabel": "13",
                "suggestedConfidence": 0.92,
                "suggestedSource": "ocr",
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        ),
    ]
    session.candidate_associations = [
        CandidateAssociation.model_validate(
            {
                "shapeCandidateId": "circle-a",
                "textCandidateId": "text-12",
                "shapeKind": "circle",
                "label": "12",
                "score": 0.81,
                "geometryScore": 0.77,
                "topologyScore": 0.4,
                "source": "shape-text:circle:direct",
                "bboxX": 120,
                "bboxY": 130,
                "bboxWidth": 60,
                "bboxHeight": 60,
            }
        ),
        CandidateAssociation.model_validate(
            {
                "shapeCandidateId": "circle-b",
                "textCandidateId": "text-12",
                "shapeKind": "circle",
                "label": "12",
                "score": 0.78,
                "geometryScore": 0.75,
                "topologyScore": 0.38,
                "source": "shape-text:circle:direct",
                "bboxX": 146,
                "bboxY": 134,
                "bboxWidth": 60,
                "bboxHeight": 60,
            }
        ),
        CandidateAssociation.model_validate(
            {
                "shapeCandidateId": "circle-a",
                "textCandidateId": "text-13",
                "shapeKind": "circle",
                "label": "13",
                "score": 0.79,
                "geometryScore": 0.76,
                "topologyScore": 0.37,
                "source": "shape-text:circle:direct",
                "bboxX": 120,
                "bboxY": 130,
                "bboxWidth": 102,
                "bboxHeight": 60,
            }
        ),
    ]

    store._refresh_pipeline_state(session, include_missing_labels=False)

    assert any(
        conflict.type == "association_ambiguity"
        and conflict.label == "12"
        and conflict.severity == "warning"
        for conflict in session.pipeline_conflicts
    )
    assert any(
        conflict.type == "association_ambiguity"
        and conflict.severity == "error"
        and sorted(conflict.related_labels) == ["12", "13"]
        for conflict in session.pipeline_conflicts
    )


def test_leader_topology_finds_outgoing_line_for_circle_candidate():
    analyzer = LeaderTopologyAnalyzer()
    image = make_leader_circle_image()
    candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "circle-1",
            "kind": "circle",
            "centerX": 102,
            "centerY": 102,
            "bboxX": 72,
            "bboxY": 72,
            "bboxWidth": 60,
            "bboxHeight": 60,
            "score": 180,
            "cropUrl": None,
            "suggestedLabel": "12",
            "suggestedConfidence": 0.9,
            "suggestedSource": "ocr",
            "topologyScore": None,
            "topologySource": None,
            "leaderAnchorX": None,
            "leaderAnchorY": None,
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    observations = analyzer.analyze(image, [candidate])

    assert "circle-1" in observations
    observation = observations["circle-1"]
    assert observation.topology_score >= 0.36
    assert observation.leader_anchor_x is not None
    assert observation.leader_anchor_y is not None


def test_candidate_association_builder_links_text_to_circle():
    builder = CandidateAssociationBuilder()
    text_candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "text-1",
            "kind": "text",
            "centerX": 100,
            "centerY": 100,
            "bboxX": 88,
            "bboxY": 88,
            "bboxWidth": 24,
            "bboxHeight": 24,
            "score": 200,
            "cropUrl": None,
            "suggestedLabel": "12",
            "suggestedConfidence": 0.94,
            "suggestedSource": "ocr",
            "topologyScore": None,
            "topologySource": None,
            "leaderAnchorX": None,
            "leaderAnchorY": None,
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )
    circle_candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "circle-1",
            "kind": "circle",
            "centerX": 100,
            "centerY": 100,
            "bboxX": 70,
            "bboxY": 70,
            "bboxWidth": 60,
            "bboxHeight": 60,
            "score": 180,
            "cropUrl": None,
            "suggestedLabel": None,
            "suggestedConfidence": None,
            "suggestedSource": None,
            "topologyScore": 0.52,
            "topologySource": "leader-topology:hough",
            "leaderAnchorX": 132,
            "leaderAnchorY": 102,
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    associations = builder.build(
        [text_candidate],
        [circle_candidate],
        config=AssociationBuildConfig(
            shape_kind=CandidateKind.CIRCLE,
            source="shape-text:circle",
            min_score=0.72,
            topology_weight=0.08,
        ),
        score_fn=InMemorySessionStore._circle_pair_score,
    )

    assert len(associations) == 1
    association = associations[0]
    assert association.shape_candidate_id == "circle-1"
    assert association.text_candidate_id == "text-1"
    assert association.label == "12"
    assert association.score >= 0.72


def test_text_region_hypotheses_collapse_to_single_best_label():
    recognizer = DrawingCandidateRecognizer()
    regions = [
        DocumentTextRegion(100, 100, 24, 24, "13", 0.88, "tile-sharp-0-0"),
        DocumentTextRegion(101, 101, 23, 24, "1", 0.9, "page-sharp"),
        DocumentTextRegion(100.5, 99.5, 24, 25, "13", 0.84, "tile-bw-0-0"),
        DocumentTextRegion(180, 140, 25, 25, "9", 0.79, "page-sharp"),
    ]

    resolved = recognizer._resolve_region_hypotheses(regions)
    labels = sorted(region.label for region in resolved)

    assert labels == ["13", "9"]
    primary = next(region for region in resolved if region.label == "13")
    assert primary.confidence >= 0.9


def test_bw_source_wins_equal_ocr_tie_for_same_region():
    recognizer = DrawingCandidateRecognizer()
    regions = [
        DocumentTextRegion(284.67, 582.33, 20.0, 19.67, "6", 1.0, "tile-sharp-0-380"),
        DocumentTextRegion(282.33, 580.0, 18.67, 24.0, "9", 1.0, "tile-bw-0-380"),
    ]

    resolved = recognizer._resolve_region_hypotheses(regions)

    assert len(resolved) == 1
    assert resolved[0].label == "9"


def test_candidate_recognizer_vote_resolution_prefers_supported_label():
    recognizer = DrawingCandidateRecognizer()
    label, confidence, source = recognizer._resolve_label_votes(
        [
            ("29B", 1.02, 0.93, "circle3x"),
            ("29B", 0.97, 0.89, "bw3x"),
            ("298", 1.01, 0.94, "sharp2x"),
        ]
    )

    assert label == "29B"
    assert confidence == 0.93
    assert source == "circle3x"


def test_dashed_text_candidate_survives_dense_row_filter():
    candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "cand-14-1",
            "kind": "text",
            "centerX": 320,
            "centerY": 640,
            "bboxX": 302,
            "bboxY": 628,
            "bboxWidth": 42,
            "bboxHeight": 20,
            "score": 210,
            "cropUrl": "/storage/demo/cand-14-1.png",
            "suggestedLabel": "14-1",
            "suggestedConfidence": 0.41,
            "suggestedSource": "tile-sharp-10-10",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    assert InMemorySessionStore._should_keep_text_candidate(candidate, neighbor_count=7, header_cutoff=420) is True


def test_low_res_circle_sheet_rejects_weak_text_only_numeric_candidate():
    candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "cand-lowres-text-3",
            "kind": "text",
            "centerX": 468.8,
            "centerY": 834.7,
            "bboxX": 460.6,
            "bboxY": 826.4,
            "bboxWidth": 16.3,
            "bboxHeight": 16.7,
            "score": 244,
            "cropUrl": "/storage/demo/cand-lowres-text-3.png",
            "suggestedLabel": "3",
            "suggestedConfidence": 0.8342,
            "suggestedSource": "tile-sharp-340-380",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    assert InMemorySessionStore._should_keep_text_candidate(
        candidate,
        neighbor_count=0,
        header_cutoff=None,
        low_res_circle_mode=True,
    ) is False


def test_same_target_conflicting_candidates_collapse_to_best_one():
    store = InMemorySessionStore()
    candidates = [
        CalloutCandidate.model_validate(
            {
                "candidateId": "cand-11",
                "kind": "text",
                "centerX": 292.9,
                "centerY": 775.2,
                "bboxX": 281.3,
                "bboxY": 761.7,
                "bboxWidth": 23.2,
                "bboxHeight": 27.2,
                "score": 270,
                "cropUrl": "/storage/demo/cand-11.png",
                "suggestedLabel": "11",
                "suggestedConfidence": 0.99,
                "suggestedSource": "tile-bw-0-380|cluster-2",
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        ),
        CalloutCandidate.model_validate(
            {
                "candidateId": "cand-71",
                "kind": "text",
                "centerX": 292.0,
                "centerY": 774.8,
                "bboxX": 281.5,
                "bboxY": 762.5,
                "bboxWidth": 21.0,
                "bboxHeight": 24.5,
                "score": 248,
                "cropUrl": "/storage/demo/cand-71.png",
                "suggestedLabel": "71",
                "suggestedConfidence": 0.9513,
                "suggestedSource": "sharp2x",
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        ),
    ]

    deduped = store._dedupe_composed_candidates(candidates)

    assert len(deduped) == 1
    assert deduped[0].suggested_label == "11"


def test_low_confidence_single_digit_box_candidate_is_rejected():
    candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "cand-box-8",
            "kind": "box",
            "centerX": 634.5,
            "centerY": 1327.5,
            "bboxX": 621.0,
            "bboxY": 1307.0,
            "bboxWidth": 27.0,
            "bboxHeight": 41.0,
            "score": 160,
            "cropUrl": "/storage/demo/cand-box-8.png",
            "suggestedLabel": "8",
            "suggestedConfidence": 0.5722,
            "suggestedSource": "sharp2x",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    assert InMemorySessionStore._should_keep_box_candidate(candidate, header_cutoff=420) is False


def test_bottom_edge_page_number_candidate_is_rejected_as_footer():
    candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "cand-footer-4",
            "kind": "text",
            "centerX": 92.5,
            "centerY": 1589.2,
            "bboxX": 85.5,
            "bboxY": 1580.3,
            "bboxWidth": 14.0,
            "bboxHeight": 17.8,
            "score": 210,
            "cropUrl": "/storage/demo/cand-footer-4.png",
            "suggestedLabel": "4",
            "suggestedConfidence": 0.99,
            "suggestedSource": "tile-sharp-0-1124|cluster-2",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    assert InMemorySessionStore._is_probable_footer_text_candidate(candidate, neighbor_count=0, image_width=1191, image_height=1684) is True


def test_circle_pair_score_accepts_low_res_text_box_that_nearly_fills_callout():
    text_candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "text-13",
            "kind": "text",
            "centerX": 605.2,
            "centerY": 211.0,
            "bboxX": 593.7,
            "bboxY": 198.3,
            "bboxWidth": 23.0,
            "bboxHeight": 25.3,
            "score": 220,
            "cropUrl": "/storage/demo/text-13.png",
            "suggestedLabel": "13",
            "suggestedConfidence": 0.99,
            "suggestedSource": "tile-sharp-340-0",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )
    circle_candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "circle-13",
            "kind": "circle",
            "centerX": 604.5,
            "centerY": 210.5,
            "bboxX": 590.5,
            "bboxY": 196.5,
            "bboxWidth": 28.0,
            "bboxHeight": 28.0,
            "score": 211.4,
            "cropUrl": "/storage/demo/circle-13.png",
            "suggestedLabel": None,
            "suggestedConfidence": None,
            "suggestedSource": None,
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    assert InMemorySessionStore._circle_pair_score(text_candidate, circle_candidate) >= 0.72


def test_header_cutoff_ignores_top_callouts_that_are_near_circle_shapes():
    candidates = [
        CalloutCandidate.model_validate(
            {
                "candidateId": f"text-{index}",
                "kind": "text",
                "centerX": center_x,
                "centerY": center_y,
                "bboxX": center_x - 10,
                "bboxY": center_y - 10,
                "bboxWidth": 20,
                "bboxHeight": 20,
                "score": 220,
                "cropUrl": f"/storage/demo/text-{index}.png",
                "suggestedLabel": label,
                "suggestedConfidence": 0.99,
                "suggestedSource": "tile-sharp-0-0",
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        )
        for index, (label, center_x, center_y) in enumerate(
            [
                ("1", 380, 50),
                ("5", 704, 95),
                ("6", 703, 135),
                ("2", 606, 175),
                ("8", 323, 188),
                ("13", 605, 211),
                ("9", 322, 223),
                ("10", 321, 263),
                ("37", 257, 309),
            ],
            start=1,
        )
    ]
    circles = [
        CalloutCandidate.model_validate(
            {
                "candidateId": f"circle-{index}",
                "kind": "circle",
                "centerX": center_x,
                "centerY": center_y,
                "bboxX": center_x - 16,
                "bboxY": center_y - 16,
                "bboxWidth": 32,
                "bboxHeight": 32,
                "score": 200,
                "cropUrl": f"/storage/demo/circle-{index}.png",
                "suggestedLabel": None,
                "suggestedConfidence": None,
                "suggestedSource": None,
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        )
        for index, (_, center_x, center_y) in enumerate(
            [
                ("1", 380, 50),
                ("5", 704, 95),
                ("6", 703, 135),
                ("2", 606, 175),
                ("8", 323, 188),
                ("13", 605, 211),
                ("9", 322, 223),
                ("10", 321, 263),
                ("37", 257, 309),
            ],
            start=1,
        )
    ]

    cutoff = InMemorySessionStore._infer_header_cutoff(candidates + circles, image_width=900, image_height=1060)

    assert cutoff is None


def test_header_cutoff_still_detects_text_only_header_rows():
    candidates = [
        CalloutCandidate.model_validate(
            {
                "candidateId": f"header-{index}",
                "kind": "text",
                "centerX": center_x,
                "centerY": center_y,
                "bboxX": center_x - 12,
                "bboxY": center_y - 10,
                "bboxWidth": 24,
                "bboxHeight": 20,
                "score": 220,
                "cropUrl": f"/storage/demo/header-{index}.png",
                "suggestedLabel": label,
                "suggestedConfidence": 0.99,
                "suggestedSource": "tile-sharp-0-0",
                "reviewStatus": "pending",
                "conflictGroup": None,
                "conflictCount": 0,
            }
        )
        for index, (label, center_x, center_y) in enumerate(
            [
                ("1", 120, 45),
                ("11", 360, 48),
                ("100", 700, 50),
                ("1", 130, 85),
                ("11", 380, 88),
                ("100", 720, 90),
            ],
            start=1,
        )
    ]

    cutoff = InMemorySessionStore._infer_header_cutoff(candidates, image_width=1191, image_height=1684)

    assert cutoff is not None


def test_plausible_label_rejects_leading_zero_and_long_numeric_suffix_noise():
    assert not DrawingCandidateRecognizer._is_plausible_label("00D", "text")
    assert not DrawingCandidateRecognizer._is_plausible_label("0E", "text")
    assert not DrawingCandidateRecognizer._is_plausible_label("100C", "text")
    assert DrawingCandidateRecognizer._is_plausible_label("29A", "text")
    assert DrawingCandidateRecognizer._is_plausible_label("14-4(1)", "text")


def test_pair_candidate_prefers_extended_label_from_local_crop():
    store = InMemorySessionStore()
    candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "candidate-demo",
            "kind": "circle",
            "centerX": 120,
            "centerY": 140,
            "bboxX": 108,
            "bboxY": 128,
            "bboxWidth": 24,
            "bboxHeight": 24,
            "score": 240,
            "cropUrl": "/storage/demo/candidate-demo.png",
            "suggestedLabel": "29",
            "suggestedConfidence": 0.89,
            "suggestedSource": "tile-adapt-0-0+circle",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    assert store._should_replace_candidate_suggestion(
        candidate,
        new_label="29A",
        new_confidence=0.8,
        new_source="easy-circle-inner6x",
    )


def test_pair_candidate_prefers_shorter_circle_label_when_suffix_looks_like_noise():
    store = InMemorySessionStore()
    candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "candidate-noisy-88",
            "kind": "circle",
            "centerX": 292,
            "centerY": 588.5,
            "bboxX": 284,
            "bboxY": 580.5,
            "bboxWidth": 16,
            "bboxHeight": 16,
            "score": 220,
            "cropUrl": "/storage/demo/candidate-noisy-88.png",
            "suggestedLabel": "88",
            "suggestedConfidence": 0.92,
            "suggestedSource": "tile-sharp-0-500+circle",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    assert store._should_replace_candidate_suggestion(
        candidate,
        new_label="8",
        new_confidence=0.9,
        new_source="easy-circle-inner6x",
    )


def test_pair_candidate_allows_tiny_circle_easy_override_for_repeated_digit_noise():
    store = InMemorySessionStore()
    candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "candidate-noisy-88-tiny",
            "kind": "circle",
            "centerX": 292,
            "centerY": 588.5,
            "bboxX": 284,
            "bboxY": 580.5,
            "bboxWidth": 16,
            "bboxHeight": 16,
            "score": 220,
            "cropUrl": "/storage/demo/candidate-noisy-88-tiny.png",
            "suggestedLabel": "88",
            "suggestedConfidence": 0.92,
            "suggestedSource": "tile-sharp-0-500+circle",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    assert store._should_replace_candidate_suggestion(
        candidate,
        new_label="8",
        new_confidence=0.54,
        new_source="easy-circle-inner-adapt8x",
    )


def test_prune_low_res_oversized_circle_candidates_keeps_small_and_drops_huge_numeric():
    huge_numeric = CalloutCandidate.model_validate(
        {
            "candidateId": "huge-8",
            "kind": "circle",
            "centerX": 120,
            "centerY": 120,
            "bboxX": 77,
            "bboxY": 77,
            "bboxWidth": 86,
            "bboxHeight": 86,
            "score": 210,
            "cropUrl": "/storage/demo/huge-8.png",
            "suggestedLabel": "8",
            "suggestedConfidence": 0.93,
            "suggestedSource": "easy-circle-inner6x",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )
    normal_numeric = CalloutCandidate.model_validate(
        {
            "candidateId": "normal-4",
            "kind": "circle",
            "centerX": 200,
            "centerY": 200,
            "bboxX": 188,
            "bboxY": 188,
            "bboxWidth": 24,
            "bboxHeight": 24,
            "score": 220,
            "cropUrl": "/storage/demo/normal-4.png",
            "suggestedLabel": "4",
            "suggestedConfidence": 0.95,
            "suggestedSource": "easy-circle-inner6x",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    pruned = InMemorySessionStore._prune_low_res_oversized_circle_candidates([huge_numeric, normal_numeric])

    assert [candidate.candidate_id for candidate in pruned] == ["normal-4"]


def test_prune_low_res_oversized_circle_candidates_drops_large_weak_two_digit_circle():
    weak_large_double = CalloutCandidate.model_validate(
        {
            "candidateId": "weak-23",
            "kind": "circle",
            "centerX": 412,
            "centerY": 540,
            "bboxX": 390.5,
            "bboxY": 518.5,
            "bboxWidth": 43.0,
            "bboxHeight": 43.0,
            "score": 180,
            "cropUrl": "/storage/demo/weak-23.png",
            "suggestedLabel": "23",
            "suggestedConfidence": 0.78,
            "suggestedSource": "tile-sharp-340-380+circle",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    pruned = InMemorySessionStore._prune_low_res_oversized_circle_candidates([weak_large_double])

    assert pruned == []


def test_should_retry_low_res_final_candidate_ocr_skips_stable_single_digit_and_keeps_noisy_two_digit():
    stable_single = CalloutCandidate.model_validate(
        {
            "candidateId": "stable-6",
            "kind": "circle",
            "centerX": 120,
            "centerY": 120,
            "bboxX": 112,
            "bboxY": 112,
            "bboxWidth": 16,
            "bboxHeight": 16,
            "score": 220,
            "cropUrl": "/storage/demo/stable-6.png",
            "suggestedLabel": "6",
            "suggestedConfidence": 0.97,
            "suggestedSource": "tile-sharp+circle",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )
    noisy_double = CalloutCandidate.model_validate(
        {
            "candidateId": "noisy-88",
            "kind": "circle",
            "centerX": 180,
            "centerY": 180,
            "bboxX": 172,
            "bboxY": 172,
            "bboxWidth": 16,
            "bboxHeight": 16,
            "score": 220,
            "cropUrl": "/storage/demo/noisy-88.png",
            "suggestedLabel": "88",
            "suggestedConfidence": 0.92,
            "suggestedSource": "tile-sharp+circle",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    assert not InMemorySessionStore._should_retry_low_res_final_candidate_ocr(stable_single)
    assert InMemorySessionStore._should_retry_low_res_final_candidate_ocr(noisy_double)


def test_easyocr_override_prefers_two_digit_circle_over_single_digit_rapidocr():
    recognizer = DrawingCandidateRecognizer()

    override = recognizer._select_easyocr_override(
        kind="circle",
        best_label="4",
        best_confidence=0.98,
        easy_votes=[("14", 1.24, 0.94, "easy-circle-inner6x")],
    )

    assert override == ("14", 0.94, "easy-circle-inner6x")


def test_adaptive_bw_caps_large_page_upscale_before_thresholding():
    recognizer = DrawingCandidateRecognizer()
    source = Image.new("L", (900, 1060), color=255)

    result = recognizer._adaptive_bw(source, scale=5)

    assert result.size[0] <= 2400
    assert result.size[1] <= 2400
    assert result.size[0] * result.size[1] <= 4_200_000


def test_vlm_candidate_consensus_prefers_majority_label_over_single_conflict():
    recognizer = VisionLLMCandidateRecognizer()

    result = recognizer._aggregate_candidate_suggestions(
        [
            ("openrouter", CandidateSuggestion(label="6", confidence=0.9, source="openrouter-vlm:model")),
            ("openai", CandidateSuggestion(label="8", confidence=0.86, source="openai-vlm:model")),
            ("gemini", CandidateSuggestion(label="6", confidence=0.88, source="gemini-vlm:model")),
        ],
        local_label="6",
        local_confidence=0.82,
    )

    assert result.label == "6"
    assert result.confidence == 0.89
    assert result.source == "vlm-consensus:gemini+openrouter"


def test_vlm_candidate_consensus_rejects_single_weak_label_against_no_callout_votes():
    recognizer = VisionLLMCandidateRecognizer()

    result = recognizer._aggregate_candidate_suggestions(
        [
            ("openrouter", CandidateSuggestion(label="60", confidence=0.88, source="openrouter-vlm:model")),
            ("openai", CandidateSuggestion(label=None, confidence=1.0, source="openai-vlm:model:no-callout")),
            ("gemini", CandidateSuggestion(label=None, confidence=1.0, source="gemini-vlm:model:no-callout")),
        ],
        local_label=None,
        local_confidence=None,
    )

    assert result.label is None
    assert result.source == "vlm-consensus:gemini+openai:no-callout"


def test_indexed_tile_vote_aggregation_prefers_supported_label_and_returns_tile_consensus_source():
    recognizer = VisionLLMCandidateRecognizer()

    result = recognizer._aggregate_indexed_tile_votes(
        [
            ("openrouter", [("A", "6", 0.9), ("B", "8", 0.94)]),
            ("openai", [("A", "6", 0.88), ("B", "3", 0.87)]),
            ("gemini", [("A", "9", 0.89), ("B", "8", 0.96)]),
        ]
    )

    assert ("A", "6", 0.89, "tile-vlm-consensus:openai+openrouter") in result
    assert ("B", "8", 0.95, "tile-vlm-consensus:gemini+openrouter") in result


def test_indexed_tile_vote_aggregation_rejects_single_provider_multi_digit_vote():
    recognizer = VisionLLMCandidateRecognizer()

    result = recognizer._aggregate_indexed_tile_votes(
        [
            ("openrouter", [("A", "63", 0.99)]),
            ("openai", []),
            ("gemini", []),
        ]
    )

    assert result == []


def test_context_tile_label_requires_local_support_for_conflicting_multi_digit_guess():
    candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "cand-63",
            "kind": "circle",
            "centerX": 120,
            "centerY": 90,
            "bboxX": 110,
            "bboxY": 80,
            "bboxWidth": 24,
            "bboxHeight": 24,
            "score": 160,
            "cropUrl": "/storage/demo/cand-63.png",
            "suggestedLabel": "63",
            "suggestedConfidence": 0.97,
            "suggestedSource": "tile-vlm:openrouter",
            "reviewStatus": "pending",
        }
    )

    weak_local = CandidateSuggestion(label=None, confidence=None, source=None)
    conflicting_local = CandidateSuggestion(label="8", confidence=0.8, source="circle3x")

    assert InMemorySessionStore._context_tile_label_survives_local_check(candidate, weak_local) is True
    assert InMemorySessionStore._context_tile_label_survives_local_check(candidate, conflicting_local) is False



def test_low_res_letter_tile_candidates_can_replace_weak_existing_circle_label():
    store = InMemorySessionStore()

    class FakeVLM:
        def is_enabled(self) -> bool:
            return True

        def resolve_indexed_tile(self, payload_image):
            return [("A", "6", 0.98)]

    store._candidate_vlm_recognizer = FakeVLM()
    store._fallback_circle_looks_like_callout = lambda preview, candidate: True

    preview = Image.new("RGB", (300, 300), color=(255, 255, 255))
    base_a = CalloutCandidate.model_validate(
        {
            "candidateId": "circle-a",
            "kind": "circle",
            "centerX": 100,
            "centerY": 100,
            "bboxX": 88,
            "bboxY": 88,
            "bboxWidth": 24,
            "bboxHeight": 24,
            "score": 210,
            "cropUrl": "/storage/demo/circle-a.png",
            "suggestedLabel": None,
            "suggestedConfidence": None,
            "suggestedSource": None,
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )
    base_b = CalloutCandidate.model_validate(
        {
            "candidateId": "circle-b",
            "kind": "circle",
            "centerX": 128,
            "centerY": 104,
            "bboxX": 116,
            "bboxY": 92,
            "bboxWidth": 24,
            "bboxHeight": 24,
            "score": 205,
            "cropUrl": "/storage/demo/circle-b.png",
            "suggestedLabel": None,
            "suggestedConfidence": None,
            "suggestedSource": None,
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )
    weak_existing = CalloutCandidate.model_validate(
        {
            "candidateId": "weak-existing",
            "kind": "circle",
            "centerX": 100,
            "centerY": 100,
            "bboxX": 88,
            "bboxY": 88,
            "bboxWidth": 24,
            "bboxHeight": 24,
            "score": 225,
            "cropUrl": "/storage/demo/weak-existing.png",
            "suggestedLabel": "60",
            "suggestedConfidence": 0.72,
            "suggestedSource": "sharp2x",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    resolved, reviewed = store._build_low_res_letter_tile_candidates(preview, [base_a, base_b], [weak_existing])

    assert any(candidate.suggested_label == "6" for candidate in resolved)
    assert any((candidate.suggested_source or "").startswith("tile-vlm:") for candidate in resolved)
    assert len(reviewed) >= 2


def test_detector_spatial_limit_preserves_bottom_band_candidates():
    detector = DrawingCandidateDetector()
    candidates: list[RawCandidate] = []
    for index in range(500):
        if index < 480:
            center_y = 40 + (index % 120)
        else:
            center_y = 980 + (index - 480)
        candidates.append(
            RawCandidate(
                kind="circle",
                center_x=float(20 + index),
                center_y=float(center_y),
                bbox_x=float(10 + index),
                bbox_y=float(center_y - 10),
                bbox_width=24.0,
                bbox_height=24.0,
                score=float(500 - index),
            )
        )

    limited = detector._limit_candidates_spatially(candidates, image_height=1060, max_total=120, band_count=6)

    assert len(limited) == 120
    assert any(candidate.center_y >= 980 for candidate in limited)


def test_low_res_final_vlm_rechecks_small_single_digit_circle_candidates():
    candidate = CalloutCandidate.model_validate(
        {
            "candidateId": "circle-32",
            "kind": "circle",
            "centerX": 452,
            "centerY": 994.5,
            "bboxX": 437.5,
            "bboxY": 980.0,
            "bboxWidth": 29.0,
            "bboxHeight": 29.0,
            "score": 220.0,
            "cropUrl": "/storage/demo/circle-32.png",
            "suggestedLabel": "3",
            "suggestedConfidence": 0.9867,
            "suggestedSource": "base",
            "reviewStatus": "pending",
            "conflictGroup": None,
            "conflictCount": 0,
        }
    )

    assert InMemorySessionStore._eligible_for_final_candidate_vlm(candidate, low_res_circle_mode=True) is True
