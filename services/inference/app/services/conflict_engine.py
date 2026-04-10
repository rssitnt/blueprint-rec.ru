from __future__ import annotations

from math import hypot

from ..models.schemas import (
    AnnotationSession,
    CandidateAssociation,
    CandidateReviewStatus,
    Marker,
    PageVocabularyEntry,
    PipelineConflict,
    PipelineConflictSeverity,
    PipelineConflictType,
)


def normalize_label(value: str | None) -> str:
    return (value or "").strip().lower()


class PipelineConflictEngine:
    _ASSOCIATION_SCORE_GAP = 0.06
    _ASSOCIATION_MIN_SECONDARY_SCORE = 0.58

    def build(
        self,
        session: AnnotationSession,
        page_vocabulary: list[PageVocabularyEntry],
        *,
        include_missing_labels: bool,
    ) -> tuple[list[str], list[PipelineConflict]]:
        missing_labels = self._build_missing_labels(session, page_vocabulary) if include_missing_labels else []
        conflicts: list[PipelineConflict] = []
        conflicts.extend(self._build_candidate_conflicts(session))
        conflicts.extend(self._build_association_conflicts(session))
        conflicts.extend(self._build_duplicate_marker_conflicts(session.markers))
        if include_missing_labels:
            conflicts.extend(self._build_missing_label_conflicts(page_vocabulary, missing_labels))
        return missing_labels, conflicts

    def _build_candidate_conflicts(self, session: AnnotationSession) -> list[PipelineConflict]:
        grouped: dict[str, list] = {}
        for candidate in session.candidates:
            if candidate.review_status != CandidateReviewStatus.PENDING:
                continue
            if not candidate.conflict_group:
                continue
            grouped.setdefault(candidate.conflict_group, []).append(candidate)

        conflicts: list[PipelineConflict] = []
        for group_key, members in grouped.items():
            if len(members) < 2:
                continue
            left = min(item.bbox_x for item in members)
            top = min(item.bbox_y for item in members)
            right = max(item.bbox_x + item.bbox_width for item in members)
            bottom = max(item.bbox_y + item.bbox_height for item in members)
            labels = sorted({item.suggested_label for item in members if item.suggested_label})
            conflicts.append(
                PipelineConflict(
                    conflict_id=group_key,
                    type=PipelineConflictType.CANDIDATE_AMBIGUITY,
                    severity=PipelineConflictSeverity.WARNING,
                    label=labels[0] if len(labels) == 1 else None,
                    message="Несколько кандидатов спорят за одно и то же место.",
                    candidate_ids=[item.candidate_id for item in members],
                    related_labels=labels,
                    bbox_x=round(left, 2),
                    bbox_y=round(top, 2),
                    bbox_width=round(right - left, 2),
                    bbox_height=round(bottom - top, 2),
                )
            )
        return conflicts

    def _build_association_conflicts(self, session: AnnotationSession) -> list[PipelineConflict]:
        if not session.candidate_associations:
            return []

        conflicts: list[PipelineConflict] = []
        candidates_by_id = {candidate.candidate_id: candidate for candidate in session.candidates}

        associations_by_text: dict[str, list[CandidateAssociation]] = {}
        associations_by_shape: dict[str, list[CandidateAssociation]] = {}
        for association in session.candidate_associations:
            associations_by_text.setdefault(association.text_candidate_id, []).append(association)
            associations_by_shape.setdefault(association.shape_candidate_id, []).append(association)

        for text_candidate_id, members in associations_by_text.items():
            ranked = self._rank_conflicting_associations(members)
            if ranked is None:
                continue

            top, second = ranked
            text_candidate = candidates_by_id.get(text_candidate_id)
            labels = sorted({item.label for item in ranked if item.label})
            left, top_y, right, bottom = self._association_bounds(ranked, candidates_by_id)
            conflicts.append(
                PipelineConflict(
                    conflict_id=f"association-text-{text_candidate_id}",
                    type=PipelineConflictType.ASSOCIATION_AMBIGUITY,
                    severity=PipelineConflictSeverity.WARNING,
                    label=text_candidate.suggested_label if text_candidate and text_candidate.suggested_label else top.label,
                    message="Текстовая метка одинаково хорошо цепляется сразу к нескольким формам.",
                    candidate_ids=[
                        text_candidate_id,
                        *[item.shape_candidate_id for item in ranked],
                    ],
                    related_labels=labels,
                    bbox_x=left,
                    bbox_y=top_y,
                    bbox_width=right - left if right > left else None,
                    bbox_height=bottom - top_y if bottom > top_y else None,
                )
            )

        for shape_candidate_id, members in associations_by_shape.items():
            ranked = self._rank_conflicting_associations(members)
            if ranked is None:
                continue

            top, second = ranked
            labels = sorted({item.label for item in ranked if item.label})
            if len({item.text_candidate_id for item in ranked}) < 2:
                continue
            left, top_y, right, bottom = self._association_bounds(ranked, candidates_by_id)
            conflicts.append(
                PipelineConflict(
                    conflict_id=f"association-shape-{shape_candidate_id}",
                    type=PipelineConflictType.ASSOCIATION_AMBIGUITY,
                    severity=PipelineConflictSeverity.ERROR,
                    label=top.label if len(labels) == 1 else None,
                    message="Одна и та же форма спорит между несколькими текстовыми метками.",
                    candidate_ids=[
                        shape_candidate_id,
                        *[item.text_candidate_id for item in ranked],
                    ],
                    related_labels=labels,
                    bbox_x=left,
                    bbox_y=top_y,
                    bbox_width=right - left if right > left else None,
                    bbox_height=bottom - top_y if bottom > top_y else None,
                )
            )

        return conflicts

    def _build_duplicate_marker_conflicts(self, markers: list[Marker]) -> list[PipelineConflict]:
        by_label: dict[str, list[Marker]] = {}
        for marker in markers:
            normalized = normalize_label(marker.label)
            if not normalized:
                continue
            by_label.setdefault(normalized, []).append(marker)

        conflicts: list[PipelineConflict] = []
        for normalized_label, members in by_label.items():
            if len(members) < 2:
                continue
            visited: set[str] = set()
            for marker in members:
                if marker.marker_id in visited:
                    continue
                cluster = [marker]
                visited.add(marker.marker_id)
                for other in members:
                    if other.marker_id in visited:
                        continue
                    if self._markers_nearby(marker, other):
                        cluster.append(other)
                        visited.add(other.marker_id)
                if len(cluster) < 2:
                    continue
                min_x = min(item.x for item in cluster)
                max_x = max(item.x for item in cluster)
                min_y = min(item.y for item in cluster)
                max_y = max(item.y for item in cluster)
                conflicts.append(
                    PipelineConflict(
                        type=PipelineConflictType.DUPLICATE_LABEL_NEARBY,
                        severity=PipelineConflictSeverity.ERROR,
                        label=cluster[0].label,
                        message="Один и тот же ярлык оказался слишком близко в двух местах.",
                        marker_ids=[item.marker_id for item in cluster],
                        related_labels=[cluster[0].label or normalized_label],
                        bbox_x=round(min_x, 2),
                        bbox_y=round(min_y, 2),
                        bbox_width=round(max(max_x - min_x, 1.0), 2),
                        bbox_height=round(max(max_y - min_y, 1.0), 2),
                    )
                )
        return conflicts

    @staticmethod
    def _build_missing_labels(session: AnnotationSession, page_vocabulary: list[PageVocabularyEntry]) -> list[str]:
        marker_labels = {normalize_label(marker.label) for marker in session.markers if normalize_label(marker.label)}
        missing: list[str] = []
        for entry in page_vocabulary:
            if not PipelineConflictEngine._entry_requires_marker(entry):
                continue
            if entry.normalized_label not in marker_labels:
                missing.append(entry.label)
        return missing

    @staticmethod
    def _build_missing_label_conflicts(
        page_vocabulary: list[PageVocabularyEntry],
        missing_labels: list[str],
    ) -> list[PipelineConflict]:
        index = {entry.label: entry for entry in page_vocabulary}
        conflicts: list[PipelineConflict] = []
        for label in missing_labels:
            entry = index.get(label)
            conflicts.append(
                PipelineConflict(
                    type=PipelineConflictType.MISSING_VOCAB_LABEL,
                    severity=PipelineConflictSeverity.ERROR,
                    label=label,
                    message="Метка видна на странице, но финальная точка для неё не поставлена.",
                    related_labels=[label],
                    bbox_x=entry.bbox_x if entry else None,
                    bbox_y=entry.bbox_y if entry else None,
                    bbox_width=entry.bbox_width if entry else None,
                    bbox_height=entry.bbox_height if entry else None,
                )
            )
        return conflicts

    def _rank_conflicting_associations(
        self,
        associations: list[CandidateAssociation],
    ) -> tuple[CandidateAssociation, CandidateAssociation] | None:
        if len(associations) < 2:
            return None

        ranked = sorted(
            associations,
            key=lambda item: (item.score, item.geometry_score, item.topology_score or 0.0),
            reverse=True,
        )
        top = ranked[0]
        second = ranked[1]
        if second.score < self._ASSOCIATION_MIN_SECONDARY_SCORE:
            return None
        if top.score > second.score + self._ASSOCIATION_SCORE_GAP:
            return None
        return top, second

    @staticmethod
    def _association_bounds(
        associations: list[CandidateAssociation],
        candidates_by_id: dict[str, object],
    ) -> tuple[float | None, float | None, float | None, float | None]:
        left: float | None = None
        top: float | None = None
        right: float | None = None
        bottom: float | None = None

        for association in associations:
            if (
                association.bbox_x is not None
                and association.bbox_y is not None
                and association.bbox_width is not None
                and association.bbox_height is not None
            ):
                candidate_left = association.bbox_x
                candidate_top = association.bbox_y
                candidate_right = association.bbox_x + association.bbox_width
                candidate_bottom = association.bbox_y + association.bbox_height
            else:
                fallback_ids = [association.text_candidate_id, association.shape_candidate_id]
                for candidate_id in fallback_ids:
                    candidate = candidates_by_id.get(candidate_id)
                    if candidate is None:
                        continue
                    candidate_left = candidate.bbox_x
                    candidate_top = candidate.bbox_y
                    candidate_right = candidate.bbox_x + candidate.bbox_width
                    candidate_bottom = candidate.bbox_y + candidate.bbox_height
                    break
                else:
                    continue

            left = candidate_left if left is None else min(left, candidate_left)
            top = candidate_top if top is None else min(top, candidate_top)
            right = candidate_right if right is None else max(right, candidate_right)
            bottom = candidate_bottom if bottom is None else max(bottom, candidate_bottom)

        return left, top, right, bottom

    @staticmethod
    def _entry_requires_marker(entry: PageVocabularyEntry) -> bool:
        if entry.occurrences > 0:
            return True
        source_count = len(entry.sources)
        confidence = entry.max_confidence or 0.0
        return source_count >= 2 or confidence >= 0.82

    @staticmethod
    def _markers_nearby(left: Marker, right: Marker) -> bool:
        threshold = 48 if max(len(left.label or ""), len(right.label or "")) > 2 else 40
        return hypot(left.x - right.x, left.y - right.y) <= threshold
