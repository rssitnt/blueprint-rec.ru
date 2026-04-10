from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.candidate_recognizer import CandidateSuggestion
from app.services.candidate_vlm_recognizer import VisionLLMCandidateRecognizer


def make_image() -> Image.Image:
    return Image.new("RGB", (64, 64), color="white")


def test_extract_label_vocabulary_uses_only_primary_provider(monkeypatch):
    recognizer = VisionLLMCandidateRecognizer()
    calls: list[str] = []

    monkeypatch.setattr(recognizer, "_openai_enabled", lambda: True)
    monkeypatch.setattr(recognizer, "_openrouter_enabled", lambda: True)
    monkeypatch.setattr(recognizer, "_gemini_enabled", lambda: True)
    monkeypatch.setattr(
        recognizer,
        "_build_vocabulary_tiles",
        lambda image, max_tiles: [make_image(), make_image()],
    )

    def openai_vocab(_image, _prompt):
        calls.append("openai")
        return {"26"}

    def fail_vocab(_image, _prompt):
        raise AssertionError("non-primary vocabulary provider should not run")

    monkeypatch.setattr(recognizer, "_extract_vocabulary_with_openai", openai_vocab)
    monkeypatch.setattr(recognizer, "_extract_vocabulary_with_openrouter", fail_vocab)
    monkeypatch.setattr(recognizer, "_extract_vocabulary_with_gemini", fail_vocab)

    labels = recognizer.extract_label_vocabulary(make_image(), max_tiles=2)

    assert labels == {"26"}
    assert calls == ["openai", "openai"]


def test_recognize_prefers_openai_before_openrouter(monkeypatch):
    recognizer = VisionLLMCandidateRecognizer()
    calls: list[str] = []

    monkeypatch.setattr(recognizer, "_openai_enabled", lambda: True)
    monkeypatch.setattr(recognizer, "_openrouter_enabled", lambda: True)
    monkeypatch.setattr(recognizer, "_gemini_enabled", lambda: False)

    def openai_recognize(_image, _prompt, _kind):
        calls.append("openai")
        return CandidateSuggestion(label="26", confidence=0.99, source="openai-vlm:test")

    def openrouter_recognize(_image, _prompt, _kind):
        calls.append("openrouter")
        return CandidateSuggestion(label="27", confidence=0.55, source="openrouter-vlm:test")

    monkeypatch.setattr(recognizer, "_recognize_with_openai", openai_recognize)
    monkeypatch.setattr(recognizer, "_recognize_with_openrouter", openrouter_recognize)

    suggestion = recognizer.recognize(make_image(), "circle")

    assert suggestion.label == "26"
    assert suggestion.source == "openai-vlm:test"
    assert calls == ["openai"]
