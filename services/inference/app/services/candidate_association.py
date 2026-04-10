from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..models.schemas import CalloutCandidate, CandidateAssociation, CandidateKind


AssociationScoreFn = Callable[[CalloutCandidate, CalloutCandidate], float]
AssociationRelaxedFn = Callable[[CalloutCandidate, CalloutCandidate, float], bool]


@dataclass(frozen=True)
class AssociationBuildConfig:
    shape_kind: CandidateKind
    source: str
    min_score: float
    topology_weight: float
    max_per_text: int = 3


class CandidateAssociationBuilder:
    def build(
        self,
        texts: list[CalloutCandidate],
        shapes: list[CalloutCandidate],
        *,
        config: AssociationBuildConfig,
        score_fn: AssociationScoreFn,
        header_cutoff: float | None = None,
        relaxed_match_fn: AssociationRelaxedFn | None = None,
    ) -> list[CandidateAssociation]:
        associations: list[CandidateAssociation] = []

        for text_candidate in texts:
            if not text_candidate.suggested_label:
                continue
            if self._text_blocked_by_header(text_candidate, header_cutoff, config.shape_kind):
                continue

            scored_pairs: list[tuple[float, float, CalloutCandidate]] = []
            for shape_candidate in shapes:
                if self._shape_blocked_by_header(shape_candidate, header_cutoff):
                    continue

                geometry_score = max(0.0, min(1.0, score_fn(text_candidate, shape_candidate)))
                if geometry_score <= 0.0:
                    continue

                topology_score = max(0.0, min(shape_candidate.topology_score or 0.0, 1.0))
                total_score = min(1.0, geometry_score + topology_score * config.topology_weight)
                scored_pairs.append((total_score, geometry_score, shape_candidate))

            if not scored_pairs:
                continue

            scored_pairs.sort(
                key=lambda item: (
                    item[0],
                    item[1],
                    item[2].topology_score or 0.0,
                    item[2].score,
                ),
                reverse=True,
            )

            kept_for_text = 0
            for index, (total_score, geometry_score, shape_candidate) in enumerate(scored_pairs):
                accepted = total_score >= config.min_score
                if not accepted and index == 0 and relaxed_match_fn is not None:
                    accepted = relaxed_match_fn(text_candidate, shape_candidate, total_score)
                if not accepted:
                    continue

                associations.append(
                    CandidateAssociation(
                        shape_candidate_id=shape_candidate.candidate_id,
                        text_candidate_id=text_candidate.candidate_id,
                        shape_kind=config.shape_kind,
                        label=text_candidate.suggested_label,
                        score=round(total_score, 4),
                        geometry_score=round(geometry_score, 4),
                        topology_score=round(shape_candidate.topology_score, 4) if shape_candidate.topology_score is not None else None,
                        source=f"{config.source}:{'relaxed' if total_score < config.min_score else 'direct'}",
                        leader_anchor_x=shape_candidate.leader_anchor_x,
                        leader_anchor_y=shape_candidate.leader_anchor_y,
                        bbox_x=min(shape_candidate.bbox_x, text_candidate.bbox_x),
                        bbox_y=min(shape_candidate.bbox_y, text_candidate.bbox_y),
                        bbox_width=max(
                            shape_candidate.bbox_x + shape_candidate.bbox_width,
                            text_candidate.bbox_x + text_candidate.bbox_width,
                        )
                        - min(shape_candidate.bbox_x, text_candidate.bbox_x),
                        bbox_height=max(
                            shape_candidate.bbox_y + shape_candidate.bbox_height,
                            text_candidate.bbox_y + text_candidate.bbox_height,
                        )
                        - min(shape_candidate.bbox_y, text_candidate.bbox_y),
                    )
                )
                kept_for_text += 1
                if kept_for_text >= config.max_per_text:
                    break

        associations.sort(
            key=lambda item: (
                item.score,
                item.geometry_score,
                item.topology_score or 0.0,
            ),
            reverse=True,
        )
        return associations

    @staticmethod
    def _shape_blocked_by_header(shape_candidate: CalloutCandidate, header_cutoff: float | None) -> bool:
        return header_cutoff is not None and shape_candidate.center_y <= header_cutoff

    @staticmethod
    def _text_blocked_by_header(
        text_candidate: CalloutCandidate,
        header_cutoff: float | None,
        shape_kind: CandidateKind,
    ) -> bool:
        if header_cutoff is None:
            return False
        if shape_kind == CandidateKind.BOX:
            return max(text_candidate.center_y, 0.0) <= header_cutoff and "-" not in (text_candidate.suggested_label or "")
        return max(text_candidate.center_y, 0.0) <= header_cutoff
