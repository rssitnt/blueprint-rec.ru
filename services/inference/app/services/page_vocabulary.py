from __future__ import annotations

from dataclasses import dataclass, field
import re

from ..models.schemas import CalloutCandidate, CandidateKind, PageVocabularyEntry


def normalize_label(value: str | None) -> str:
    return (value or "").strip().lower()


def _label_sort_key(label: str) -> tuple[int, int, str, str]:
    normalized = normalize_label(label)
    match = re.fullmatch(r"(\d+)(.*)", normalized)
    if not match:
        return (1, 10**9, normalized, normalized)
    return (0, int(match.group(1)), match.group(2), normalized)


@dataclass
class _VocabularyAccumulator:
    label: str
    normalized_label: str
    occurrences: int = 0
    max_confidence: float | None = None
    sources: set[str] = field(default_factory=set)
    bbox_x: float | None = None
    bbox_y: float | None = None
    bbox_width: float | None = None
    bbox_height: float | None = None
    _best_bbox_score: float = 0.0

    def consume(self, candidate: CalloutCandidate) -> None:
        self.occurrences += 1
        confidence = candidate.suggested_confidence or 0.0
        if self.max_confidence is None or confidence > self.max_confidence:
            self.max_confidence = confidence
            self.label = candidate.suggested_label or self.label
        if candidate.suggested_source:
            self.sources.add(candidate.suggested_source)

        bbox_score = confidence + min(candidate.score / 300.0, 0.3)
        if bbox_score >= self._best_bbox_score:
            self._best_bbox_score = bbox_score
            self.bbox_x = round(candidate.bbox_x, 2)
            self.bbox_y = round(candidate.bbox_y, 2)
            self.bbox_width = round(candidate.bbox_width, 2)
            self.bbox_height = round(candidate.bbox_height, 2)

    def freeze(self) -> PageVocabularyEntry:
        return PageVocabularyEntry(
            label=self.label,
            normalized_label=self.normalized_label,
            occurrences=self.occurrences,
            max_confidence=None if self.max_confidence is None else round(self.max_confidence, 4),
            sources=sorted(self.sources),
            bbox_x=self.bbox_x,
            bbox_y=self.bbox_y,
            bbox_width=self.bbox_width,
            bbox_height=self.bbox_height,
        )


class PageVocabularyBuilder:
    def build(
        self,
        candidates: list[CalloutCandidate],
        explicit_vocabulary: set[str] | None = None,
    ) -> list[PageVocabularyEntry]:
        explicit_vocabulary = explicit_vocabulary or set()
        accumulators: dict[str, _VocabularyAccumulator] = {}

        for label in explicit_vocabulary:
            normalized = normalize_label(label)
            if not normalized:
                continue
            accumulators[normalized] = _VocabularyAccumulator(
                label=label.upper(),
                normalized_label=normalized,
                sources={"page-vlm-vocabulary"},
            )

        for candidate in candidates:
            normalized = normalize_label(candidate.suggested_label)
            if not normalized:
                continue
            if not self._candidate_is_vocabulary_worthy(candidate, normalized in accumulators):
                continue

            accumulator = accumulators.get(normalized)
            if accumulator is None:
                accumulator = _VocabularyAccumulator(
                    label=(candidate.suggested_label or normalized).upper(),
                    normalized_label=normalized,
                )
                accumulators[normalized] = accumulator
            accumulator.consume(candidate)

        entries = [entry.freeze() for entry in accumulators.values()]
        entries.sort(key=lambda item: _label_sort_key(item.label))
        return entries

    @staticmethod
    def _candidate_is_vocabulary_worthy(candidate: CalloutCandidate, preseeded: bool) -> bool:
        confidence = candidate.suggested_confidence or 0.0
        label = normalize_label(candidate.suggested_label)
        if not label:
            return False
        if preseeded:
            return True
        if candidate.kind == CandidateKind.TEXT:
            return confidence >= 0.58
        if "-" in label or any(char.isalpha() for char in label):
            return confidence >= 0.68
        return confidence >= 0.82
