from __future__ import annotations

import base64
import json
import re
import string
from io import BytesIO
from typing import Callable

from PIL import Image, ImageFilter, ImageOps
import requests

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    OpenAI = None

from ..core.config import settings
from .candidate_recognizer import CandidateSuggestion, DASH_CHARS, LABEL_PATTERN, LETTER_FIXES

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover
    RESAMPLE_LANCZOS = Image.LANCZOS


class VisionLLMCandidateRecognizer:
    def __init__(self) -> None:
        self._client = None
        self._openai_unavailable = False

    def is_enabled(self) -> bool:
        return bool(self._openrouter_enabled() or self._gemini_enabled() or self._openai_enabled())

    def extract_label_vocabulary(self, image: Image.Image, max_tiles: int = 6) -> set[str]:
        if not self.is_enabled():
            return set()

        tiles = self._build_vocabulary_tiles(image, max_tiles=max_tiles)
        if not tiles:
            return set()

        provider_calls: list[tuple[str, Callable[[Image.Image, str], set[str]]]] = []
        if self._openai_enabled():
            provider_calls.append(("openai", self._extract_vocabulary_with_openai))
        if self._openrouter_enabled():
            provider_calls.append(("openrouter", self._extract_vocabulary_with_openrouter))
        if self._gemini_enabled():
            provider_calls.append(("gemini", self._extract_vocabulary_with_gemini))

        if not provider_calls:
            return set()

        _, primary_call = provider_calls[0]
        aggregated: dict[str, int] = {}
        for tile in tiles:
            prompt = self._build_vocabulary_prompt()
            # Vocabulary extraction is only a coarse hint for low-res recovery, so
            # avoid multiplying latency by faning out every tile across every VLM.
            for label in primary_call(tile, prompt):
                aggregated[label] = aggregated.get(label, 0) + 1

        return {label for label, count in aggregated.items() if count >= 1}

    def recognize(
        self,
        crop: Image.Image,
        kind: str,
        local_label: str | None = None,
        local_confidence: float | None = None,
        allowed_labels: list[str] | None = None,
        *,
        use_consensus: bool = True,
    ) -> CandidateSuggestion:
        if not self.is_enabled():
            return CandidateSuggestion(label=None, confidence=None, source=None)

        payload_image = self._prepare_crop(crop)
        prompt = self._build_prompt(
            kind=kind,
            local_label=local_label,
            local_confidence=local_confidence,
            allowed_labels=allowed_labels,
        )
        provider_calls: list[tuple[str, Callable[[Image.Image, str, str], CandidateSuggestion]]] = []
        if self._openai_enabled():
            provider_calls.append(("openai", self._recognize_with_openai))
        if self._openrouter_enabled():
            provider_calls.append(("openrouter", self._recognize_with_openrouter))
        if self._gemini_enabled():
            provider_calls.append(("gemini", self._recognize_with_gemini))

        if not provider_calls:
            return CandidateSuggestion(label=None, confidence=None, source=None)

        primary_name, primary_call = provider_calls[0]
        primary = primary_call(payload_image, prompt, kind)
        suggestions: list[tuple[str, CandidateSuggestion]] = [(primary_name, primary)]

        if (
            not use_consensus
            or len(provider_calls) == 1
            or self._should_short_circuit_candidate_suggestion(
            primary,
            local_label=local_label,
            local_confidence=local_confidence,
        )):
            return primary

        for provider_name, provider_call in provider_calls[1:]:
            suggestion = provider_call(payload_image, prompt, kind)
            suggestions.append((provider_name, suggestion))

        return self._aggregate_candidate_suggestions(
            suggestions,
            local_label=local_label,
            local_confidence=local_confidence,
        )

    def _recognize_with_openai(self, payload_image: Image.Image, prompt: str, kind: str) -> CandidateSuggestion:
        client = self._get_client()
        if client is None:
            return CandidateSuggestion(label=None, confidence=None, source=None)

        try:
            response = client.responses.create(
                model=settings.openai_vision_model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": self._image_to_data_uri(payload_image)},
                        ],
                    }
                ],
                max_output_tokens=120,
            )
        except Exception:
            self._openai_unavailable = True
            return CandidateSuggestion(label=None, confidence=None, source=None)

        output_text = (getattr(response, "output_text", "") or "").strip()
        label, confidence, no_callout = self._parse_response(output_text, kind)
        if not label:
            return CandidateSuggestion(
                label=None,
                confidence=1.0 if no_callout else None,
                source=f"openai-vlm:{settings.openai_vision_model}:no-callout" if no_callout else None,
            )

        return CandidateSuggestion(
            label=label,
            confidence=confidence,
            source=f"openai-vlm:{settings.openai_vision_model}",
        )

    def _extract_vocabulary_with_openai(self, payload_image: Image.Image, prompt: str) -> set[str]:
        client = self._get_client()
        if client is None:
            return set()

        try:
            response = client.responses.create(
                model=settings.openai_vision_model,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Return only valid JSON with a top-level 'labels' array.",
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": self._image_to_data_uri(payload_image)},
                        ],
                    },
                ],
                max_output_tokens=240,
            )
        except Exception:
            self._openai_unavailable = True
            return set()

        output_text = (getattr(response, "output_text", "") or "").strip()
        return self._parse_vocabulary_labels(output_text)

    def _get_client(self):
        if self._client is None and self._openai_enabled():
            self._client = OpenAI(
                api_key=settings.openai_api_key,
                timeout=settings.openai_vision_timeout_seconds,
            )
        return self._client

    @staticmethod
    def _gemini_enabled() -> bool:
        return bool(settings.enable_gemini_vision and settings.gemini_api_key)

    def _openai_enabled(self) -> bool:
        return bool(
            not self._openai_unavailable
            and settings.enable_openai_vision
            and settings.openai_api_key
            and OpenAI is not None
        )

    def _recognize_with_gemini(self, payload_image: Image.Image, prompt: str, kind: str) -> CandidateSuggestion:
        if not self._gemini_enabled():
            return CandidateSuggestion(label=None, confidence=None, source=None)

        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": self._image_to_base64(payload_image),
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
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_vision_model}:generateContent?key={settings.gemini_api_key}"
        )
        try:
            response = requests.post(url, json=body, timeout=settings.openai_vision_timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            output_text = (
                payload.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
        except Exception:
            return CandidateSuggestion(label=None, confidence=None, source=None)

        label, confidence, no_callout = self._parse_response(output_text, kind)
        if not label:
            return CandidateSuggestion(
                label=None,
                confidence=1.0 if no_callout else None,
                source=f"gemini-vlm:{settings.gemini_vision_model}:no-callout" if no_callout else None,
            )

        return CandidateSuggestion(
            label=label,
            confidence=confidence,
            source=f"gemini-vlm:{settings.gemini_vision_model}",
        )

    def _extract_vocabulary_with_gemini(self, payload_image: Image.Image, prompt: str) -> set[str]:
        if not self._gemini_enabled():
            return set()

        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": self._image_to_base64(payload_image),
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
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_vision_model}:generateContent?key={settings.gemini_api_key}"
        )
        try:
            response = requests.post(url, json=body, timeout=settings.openai_vision_timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            output_text = (
                payload.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
        except Exception:
            return set()

        return self._parse_vocabulary_labels(output_text)

    @staticmethod
    def _openrouter_enabled() -> bool:
        return bool(settings.enable_openrouter_vision and settings.openrouter_api_key)

    def _recognize_with_openrouter(self, payload_image: Image.Image, prompt: str, kind: str) -> CandidateSuggestion:
        if not self._openrouter_enabled():
            return CandidateSuggestion(label=None, confidence=None, source=None)

        output_text = self._openrouter_chat_json(
            payload_image=payload_image,
            system_prompt="Return only valid JSON. If uncertain, prefer is_callout=false.",
            user_prompt=prompt,
        )
        if output_text is None:
            return CandidateSuggestion(label=None, confidence=None, source=None)

        label, confidence, no_callout = self._parse_response(str(output_text or ""), kind)
        if not label:
            return CandidateSuggestion(
                label=None,
                confidence=1.0 if no_callout else None,
                source=f"openrouter-vlm:{settings.openrouter_vision_model}:no-callout" if no_callout else None,
            )

        return CandidateSuggestion(
            label=label,
            confidence=confidence,
            source=f"openrouter-vlm:{settings.openrouter_vision_model}",
        )

    def _extract_vocabulary_with_openrouter(self, payload_image: Image.Image, prompt: str) -> set[str]:
        if not self._openrouter_enabled():
            return set()

        output_text = self._openrouter_chat_json(
            payload_image=payload_image,
            system_prompt="Return only valid JSON with a top-level 'labels' array.",
            user_prompt=prompt,
        )
        if output_text is None:
            return set()
        return self._parse_vocabulary_labels(str(output_text or ""))

    def resolve_indexed_tile(self, payload_image: Image.Image) -> list[tuple[str, str, float, str]]:
        prompt = (
            "You are reviewing a zoomed technical drawing tile. "
            "Red circles with letter badges mark candidate callout bubbles. "
            "Only choose from these lettered candidates. "
            "For each lettered candidate that is truly a numbered callout bubble, return its index letter and the underlying callout label printed inside the drawing, not the badge letter. "
            "Omit candidates that are not real callouts or whose label is unreadable. "
            "Never invent labels for unmarked spots. "
            "Do not guess a smooth number sequence. Repeated labels are allowed only if multiple different marked bubbles visibly show the same label. "
            'Return JSON only: {"items":[{"index":"A","label":"string","confidence":0-1}]}.'
        )
        provider_results = [
            ("openrouter", self._resolve_indexed_tile_with_openrouter(payload_image, prompt)),
            ("openai", self._resolve_indexed_tile_with_openai(payload_image, prompt)),
            ("gemini", self._resolve_indexed_tile_with_gemini(payload_image, prompt)),
        ]
        return self._aggregate_indexed_tile_votes(provider_results)

    def _resolve_indexed_tile_with_openrouter(self, payload_image: Image.Image, prompt: str) -> list[tuple[str, str, float]]:
        if not self._openrouter_enabled():
            return []
        output_text = self._openrouter_chat_json(
            payload_image=payload_image,
            system_prompt="Return only valid JSON. Badge letters are NOT the answer. If uncertain, omit.",
            user_prompt=prompt,
        )
        return self._parse_indexed_tile_items(output_text)

    def _resolve_indexed_tile_with_openai(self, payload_image: Image.Image, prompt: str) -> list[tuple[str, str, float]]:
        if not self._openai_enabled():
            return []
        client = self._get_client()
        if client is None:
            return []
        try:
            response = client.responses.create(
                model=settings.openai_vision_model,
                input=[
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": "Return only valid JSON. Badge letters are NOT the answer. If uncertain, omit."}],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": self._image_to_data_uri(payload_image)},
                        ],
                    },
                ],
                max_output_tokens=220,
            )
            output_text = (getattr(response, "output_text", "") or "").strip()
        except Exception:
            self._openai_unavailable = True
            return []
        return self._parse_indexed_tile_items(output_text)

    def _resolve_indexed_tile_with_gemini(self, payload_image: Image.Image, prompt: str) -> list[tuple[str, str, float]]:
        if not self._gemini_enabled():
            return []
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": self._image_to_base64(payload_image),
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
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_vision_model}:generateContent?key={settings.gemini_api_key}"
        )
        try:
            response = requests.post(url, json=body, timeout=settings.openai_vision_timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            output_text = (
                payload.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
        except Exception:
            return []
        return self._parse_indexed_tile_items(output_text)

    def _parse_indexed_tile_items(self, output_text: str | None) -> list[tuple[str, str, float]]:
        if not output_text:
            return []

        match = re.search(r"\{.*\}", str(output_text), re.DOTALL)
        raw_json = match.group(0) if match else str(output_text)
        try:
            payload = json.loads(raw_json)
        except Exception:
            return []

        items = payload.get("items")
        if not isinstance(items, list):
            return []

        resolved: list[tuple[str, str, float]] = []
        valid_indexes = set(string.ascii_uppercase[:12])
        for item in items:
            if not isinstance(item, dict):
                continue
            index = str(item.get("index") or "").strip().upper()
            if index not in valid_indexes:
                continue
            label = self._normalize_label(item.get("label"))
            if not label or not self._is_plausible_label(label, "circle"):
                continue
            try:
                confidence = float(item.get("confidence", 0.0))
            except Exception:
                confidence = 0.0
            confidence = max(0.0, min(1.0, round(confidence, 4)))
            resolved.append((index, label, confidence))
        return resolved

    def _build_vocabulary_tiles(self, image: Image.Image, max_tiles: int = 6) -> list[Image.Image]:
        width, height = image.size
        if width <= 0 or height <= 0:
            return []

        cols = 3
        rows = 2
        tile_w = max(220, int(width / cols))
        tile_h = max(220, int(height / rows))
        tiles: list[Image.Image] = []

        for r in range(rows):
            for c in range(cols):
                left = int(c * tile_w)
                top = int(r * tile_h)
                right = int(min(width, left + tile_w))
                bottom = int(min(height, top + tile_h))
                if right - left < 40 or bottom - top < 40:
                    continue
                crop = image.crop((left, top, right, bottom)).convert("RGB")
                tiles.append(self._prepare_crop(crop))

        if len(tiles) > max_tiles:
            tiles = tiles[:max_tiles]
        return tiles

    @staticmethod
    def _build_vocabulary_prompt() -> str:
        return (
            "You are looking at a technical drawing tile. "
            "List every callout label that is clearly visible within this tile. "
            "Return JSON only in this exact shape: {\"labels\":[\"1\",\"14-1\",\"29A\",...]} "
            "Only include labels that are actually visible; do NOT guess missing numbers. "
            "Exclude page numbers or non-callout text."
        )

    @classmethod
    def _parse_vocabulary_labels(cls, output_text: str | None) -> set[str]:
        if not output_text:
            return set()

        match = re.search(r"\{.*\}", str(output_text), re.DOTALL)
        raw_json = match.group(0) if match else str(output_text)
        try:
            payload = json.loads(raw_json)
        except Exception:
            return set()

        labels = payload.get("labels")
        if not isinstance(labels, list):
            return set()

        normalized: set[str] = set()
        for item in labels:
            label = cls._normalize_label(item)
            if not label:
                continue
            if not cls._is_plausible_label(label, "circle"):
                continue
            normalized.add(label)
        return normalized

    def _aggregate_candidate_suggestions(
        self,
        suggestions: list[tuple[str, CandidateSuggestion]],
        local_label: str | None = None,
        local_confidence: float | None = None,
    ) -> CandidateSuggestion:
        active = [(provider_name, suggestion) for provider_name, suggestion in suggestions if suggestion.source]
        if not active:
            return CandidateSuggestion(label=None, confidence=None, source=None)

        normalized_local = self._normalize_label(local_label)
        label_votes: dict[str, list[tuple[str, CandidateSuggestion]]] = {}
        no_callout: list[tuple[str, CandidateSuggestion]] = []
        for provider_name, suggestion in active:
            if suggestion.label:
                label_votes.setdefault(suggestion.label, []).append((provider_name, suggestion))
            elif suggestion.source and suggestion.source.endswith(":no-callout"):
                no_callout.append((provider_name, suggestion))

        if not label_votes:
            if len(no_callout) >= 2 or (len(active) == 1 and len(no_callout) == 1):
                providers = sorted({provider_name for provider_name, _ in no_callout})
                avg_confidence = sum((suggestion.confidence or 1.0) for _, suggestion in no_callout) / max(len(no_callout), 1)
                source = (
                    f"vlm-consensus:{'+'.join(providers)}:no-callout"
                    if len(providers) >= 2
                    else no_callout[0][1].source
                )
                return CandidateSuggestion(label=None, confidence=round(avg_confidence, 4), source=source)
            return CandidateSuggestion(label=None, confidence=None, source=None)

        ranked = sorted(
            label_votes.items(),
            key=lambda item: (
                len(item[1]),
                1 if normalized_local and item[0] == normalized_local else 0,
                sum((evidence.confidence or 0.0) for _, evidence in item[1]) / max(len(item[1]), 1),
            ),
            reverse=True,
        )
        best_label, best_evidence = ranked[0]
        vote_count = len(best_evidence)
        avg_confidence = sum((evidence.confidence or 0.0) for _, evidence in best_evidence) / max(vote_count, 1)
        best_is_local = bool(normalized_local and best_label == normalized_local)

        if len(ranked) > 1 and len(active) >= 2:
            runner_up_evidence = ranked[1][1]
            runner_up_count = len(runner_up_evidence)
            runner_up_confidence = sum((evidence.confidence or 0.0) for _, evidence in runner_up_evidence) / max(len(runner_up_evidence), 1)
            if runner_up_count == vote_count and abs(avg_confidence - runner_up_confidence) < 0.1 and not best_is_local:
                return CandidateSuggestion(label=None, confidence=None, source=None)

        local_match_gate = max(0.84, min(0.96, (local_confidence or 0.0) - 0.06))
        strong_single_provider = avg_confidence >= 0.96 or (best_is_local and avg_confidence >= local_match_gate)
        if len(active) >= 2 and vote_count < 2 and not strong_single_provider:
            if len(no_callout) >= 2:
                providers = sorted({provider_name for provider_name, _ in no_callout})
                avg_no_callout = sum((suggestion.confidence or 1.0) for _, suggestion in no_callout) / max(len(no_callout), 1)
                return CandidateSuggestion(
                    label=None,
                    confidence=round(avg_no_callout, 4),
                    source=f"vlm-consensus:{'+'.join(providers)}:no-callout",
                )
            return CandidateSuggestion(label=None, confidence=None, source=None)

        providers = sorted({provider_name for provider_name, _ in best_evidence})
        if len(providers) >= 2:
            source = f"vlm-consensus:{'+'.join(providers)}"
        else:
            source = best_evidence[0][1].source
        return CandidateSuggestion(label=best_label, confidence=round(avg_confidence, 4), source=source)

    @staticmethod
    def _should_short_circuit_candidate_suggestion(
        suggestion: CandidateSuggestion,
        local_label: str | None = None,
        local_confidence: float | None = None,
    ) -> bool:
        if not suggestion.source:
            return False
        normalized_local = VisionLLMCandidateRecognizer._normalize_label(local_label)
        confidence = suggestion.confidence or 0.0
        if suggestion.label:
            if normalized_local and suggestion.label == normalized_local and confidence >= max(0.9, (local_confidence or 0.0) - 0.03):
                return True
            return not normalized_local and confidence >= 0.985
        return suggestion.source.endswith(":no-callout") and not normalized_local and confidence >= 0.995

    def _aggregate_indexed_tile_votes(
        self,
        provider_results: list[tuple[str, list[tuple[str, str, float]]]],
    ) -> list[tuple[str, str, float, str]]:
        votes: dict[str, list[tuple[str, float, str]]] = {}
        configured_provider_count = len(provider_results)
        active_provider_count = sum(1 for _, items in provider_results if items)
        for provider_name, items in provider_results:
            for index, label, confidence in items:
                votes.setdefault(index, []).append((label, confidence, provider_name))

        resolved: list[tuple[str, str, float, str]] = []
        for index, entries in votes.items():
            stats: dict[str, list[tuple[float, str]]] = {}
            for label, confidence, provider_name in entries:
                stats.setdefault(label, []).append((confidence, provider_name))

            ranked = sorted(
                stats.items(),
                key=lambda item: (
                    len(item[1]),
                    sum(conf for conf, _ in item[1]) / max(len(item[1]), 1),
                ),
                reverse=True,
            )
            best_label, evidence = ranked[0]
            vote_count = len(evidence)
            avg_confidence = sum(conf for conf, _ in evidence) / max(vote_count, 1)
            label_is_single_digit = best_label.isdigit() and len(best_label) == 1
            label_is_multi_digit = best_label.isdigit() and len(best_label) >= 2

            if len(ranked) > 1 and configured_provider_count >= 2:
                runner_up_count = len(ranked[1][1])
                runner_up_conf = sum(conf for conf, _ in ranked[1][1]) / max(len(ranked[1][1]), 1)
                if runner_up_count == vote_count and abs(avg_confidence - runner_up_conf) < 0.1:
                    continue

            if configured_provider_count >= 2 and vote_count < 2:
                if label_is_multi_digit:
                    continue
                if label_is_single_digit and avg_confidence < 0.985:
                    continue
                if not label_is_single_digit and avg_confidence < 0.98:
                    continue

            providers = sorted({provider_name for _, provider_name in evidence})
            source = (
                f"tile-vlm-consensus:{'+'.join(providers)}"
                if len(providers) >= 2
                else f"tile-vlm:{providers[0]}"
            )
            resolved.append((index, best_label, round(avg_confidence, 4), source))

        return sorted(resolved, key=lambda item: item[0])

    def _openrouter_chat_json(
        self,
        payload_image: Image.Image,
        system_prompt: str,
        user_prompt: str,
    ) -> str | None:
        body = {
            "model": settings.openrouter_vision_model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": self._image_to_data_uri(payload_image)}},
                    ],
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "blueprint-rec-2",
        }
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=body,
                timeout=settings.openai_vision_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            return (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
        except Exception:
            return None

    @staticmethod
    def _prepare_crop(crop: Image.Image) -> Image.Image:
        rgb = crop.convert("RGB")
        gray = ImageOps.grayscale(rgb)
        auto = ImageOps.autocontrast(gray, cutoff=1)
        sharpened = auto.filter(ImageFilter.UnsharpMask(radius=1.4, percent=180, threshold=2))
        max_side = max(sharpened.size)
        if max_side < 420:
            scale = min(8, max(2, int(round(420 / max(max_side, 1)))))
            sharpened = sharpened.resize(
                (sharpened.width * scale, sharpened.height * scale),
                RESAMPLE_LANCZOS,
            )
        return sharpened.convert("RGB")

    @staticmethod
    def _build_prompt(
        kind: str,
        local_label: str | None = None,
        local_confidence: float | None = None,
        allowed_labels: list[str] | None = None,
    ) -> str:
        hint = ""
        if local_label:
            confidence_text = ""
            if local_confidence is not None:
                confidence_text = f" (local confidence {max(0.0, min(1.0, float(local_confidence))):.2f})"
            hint = (
                f" A local detector currently suggests the centered target may read '{local_label}'{confidence_text}. "
                "Treat that only as a hint: keep it if it matches the visible centered callout, correct it if the visible label is different, and set is_callout=false if there is no real callout at the centered target."
            )
        allowed_hint = ""
        if allowed_labels:
            choices = ", ".join(label for label in allowed_labels[:24] if label)
            if choices:
                allowed_hint = (
                    f" If the centered target is a real callout, its label must be one of these exact values: {choices}. "
                    "Do not invent a different label. If none of these exact values visibly match the centered target, set is_callout=false and label=null."
                )
        return (
            "You are reading a tiny crop from a technical drawing. "
            "The crop contains one highlighted target area near the center. "
            "The image may show both a close zoom and a wider context view of the same target. "
            "If two views are present, use the wider view only to understand context and use the close zoom to read the actual label. "
            "Read only the main callout label inside the highlighted target, which may be a circle, a box, or a tight text callout. "
            "If the highlighted target is just part geometry, a hole, a bolt, a fitting, a line crossing, or any non-callout drawing element, it is NOT a callout. "
            "Ignore page numbers, neighboring callouts, construction lines, holes, and part geometry. "
            f"Candidate kind hint: {kind}. "
            f"{hint}"
            f"{allowed_hint}"
            "Return JSON only in this exact shape: "
            '{"is_callout": boolean, "label": null | string, "confidence": number}. '
            "Use label formats like 17, 29A, 14-1, 14-4(2). "
            "Set is_callout=false and label=null if the highlighted target itself does not visibly contain a callout label."
        )

    @staticmethod
    def _image_to_data_uri(image: Image.Image) -> str:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"

    @staticmethod
    def _image_to_base64(image: Image.Image) -> str:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    @classmethod
    def _parse_response(cls, output_text: str, kind: str) -> tuple[str | None, float | None, bool]:
        if not output_text:
            return None, None, False
        match = re.search(r"\{.*\}", output_text, re.DOTALL)
        raw_json = match.group(0) if match else output_text
        try:
            data = json.loads(raw_json)
        except Exception:
            return None, None, False

        is_callout = data.get("is_callout")
        if is_callout is False:
            return None, None, True

        label = cls._normalize_label(data.get("label"))
        if not label:
            return None, None, True
        if not cls._is_plausible_label(label, kind):
            return None, None, False

        try:
            confidence = float(data.get("confidence", 0.0))
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(1.0, round(confidence, 4)))
        return label, confidence, True

    @staticmethod
    def _normalize_label(value: object) -> str | None:
        text = str(value or "").strip().upper().translate(LETTER_FIXES)
        text = re.sub(f"[{re.escape(DASH_CHARS)}]+", "-", text)
        text = text.replace("—", "-").replace(" ", "")
        text = re.sub(r"[^0-9A-ZА-ЯЁ()\-]", "", text)
        if not text or text in {"NULL", "NONE", "N/A", "NA"}:
            return None
        return text

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
        if label.isdigit() and kind == "circle" and len(label) > 3:
            return False
        return True
