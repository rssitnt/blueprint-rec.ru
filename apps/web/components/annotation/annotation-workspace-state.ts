import type { AnnotationSession, CalloutCandidate, Marker } from "../../lib/types";

export const pipelineConflictTypeLabels = {
  association_ambiguity: "shape↔text спор",
  candidate_ambiguity: "спор кандидатов"
} as const;

export const historyQueueContextLabels = {
  review: "review",
  deferred: "отложенные",
  all: "все"
} as const;

export type HistoryQueueMode = "current" | "deferred";
export type MarkerQueueMode = "all" | "current" | "deferred";
export type HistoryQueueContext = "review" | "deferred";
export type HistoryJumpContext = HistoryQueueContext | "all";
export type SessionPipelineConflict = NonNullable<AnnotationSession["pipelineConflicts"]>[number];

export type HistoryAlternateQueueAction = {
  mode: HistoryQueueMode;
  label: string;
};

export type AmbiguityReviewCandidateViewModel = {
  candidate: CalloutCandidate;
  linkedConflict: { message: string } | null;
  distanceToMarker: number;
  sameSuggestedLabel: boolean;
  associationCount: number;
};

export type SelectedAmbiguityReviewViewModel = {
  reviewCandidates: AmbiguityReviewCandidateViewModel[];
  reviewMessages: string[];
  reviewTypeLabels: string[];
  hasSelectedReview: boolean;
  canConfirm: boolean;
  canSkip: boolean;
};

export type AmbiguityQueueViewModel = {
  activeQueueLabel: string;
  firstMarkerId: string | null;
  displayedMarkers: Marker[];
  selectedMarkerIndex: number;
  currentPosition: number;
  progress: number;
  selectedQueueTitle: string;
  selectedQueueHint: string;
  hasUnresolvedMarkers: boolean;
};

export function normalizeConflictLabel(label: string | null | undefined) {
  return (label ?? "").trim().toLowerCase();
}

export function getPrimaryHistoryQueueContext(currentPassCount: number, deferredCount: number): HistoryQueueContext | null {
  if (currentPassCount > 0) {
    return "review";
  }

  if (deferredCount > 0) {
    return "deferred";
  }

  return null;
}

export function getQueueCountByContext(queueContext: HistoryQueueContext | null, currentPassCount: number, deferredCount: number): number {
  if (queueContext === "review") {
    return currentPassCount;
  }

  if (queueContext === "deferred") {
    return deferredCount;
  }

  return 0;
}

export function buildHistoryAlternateQueueAction(args: {
  showDeferredAmbiguityMarkersOnly: boolean;
  currentPassCount: number;
  deferredCount: number;
  toReviewLabel: string;
  toDeferredLabel: string;
}): HistoryAlternateQueueAction | null {
  const { showDeferredAmbiguityMarkersOnly, currentPassCount, deferredCount, toReviewLabel, toDeferredLabel } = args;

  if (showDeferredAmbiguityMarkersOnly && currentPassCount > 0) {
    return { mode: "current", label: `${toReviewLabel} ${currentPassCount}` };
  }

  if (currentPassCount > 0 && deferredCount > 0) {
    return { mode: "deferred", label: `${toDeferredLabel} ${deferredCount}` };
  }

  return null;
}

export function getNextMarkerIdInQueue(queue: Marker[], currentMarkerId: string): string | null {
  const currentIndex = queue.findIndex((marker) => marker.markerId === currentMarkerId);
  const nextIndex = currentIndex >= 0 && queue.length > 1 ? (currentIndex + 1) % queue.length : -1;

  return nextIndex >= 0 ? queue[nextIndex]?.markerId ?? null : null;
}

export function buildSelectedAmbiguityReviewViewModel(args: {
  selectedMarker: Marker | null;
  selectedMarkerAmbiguityConflicts: SessionPipelineConflict[];
  pendingCandidates: CalloutCandidate[];
  candidatesById: ReadonlyMap<string, CalloutCandidate>;
  candidateAssociationCountById: ReadonlyMap<string, number>;
  draftLabel: string;
  showDeferredAmbiguityMarkersOnly: boolean;
}): SelectedAmbiguityReviewViewModel {
  const {
    selectedMarker,
    selectedMarkerAmbiguityConflicts,
    pendingCandidates,
    candidatesById,
    candidateAssociationCountById,
    draftLabel,
    showDeferredAmbiguityMarkersOnly
  } = args;

  const selectedMarkerNormalizedLabel = normalizeConflictLabel(selectedMarker?.label);
  const markerReviewCandidateIds = new Set<string>();

  for (const conflict of selectedMarkerAmbiguityConflicts) {
    for (const candidateId of conflict.candidateIds) {
      markerReviewCandidateIds.add(candidateId);
    }
  }

  if (selectedMarker) {
    for (const candidate of pendingCandidates) {
      if (markerReviewCandidateIds.has(candidate.candidateId)) {
        continue;
      }

      const sameSuggestedLabel =
        selectedMarkerNormalizedLabel.length > 0 &&
        normalizeConflictLabel(candidate.suggestedLabel) === selectedMarkerNormalizedLabel;
      const distanceToMarker = Math.hypot(candidate.centerX - selectedMarker.x, candidate.centerY - selectedMarker.y);
      const nearbyCandidate = distanceToMarker <= Math.max(96, Math.max(candidate.bboxWidth, candidate.bboxHeight) * 1.5);

      if (sameSuggestedLabel || nearbyCandidate) {
        markerReviewCandidateIds.add(candidate.candidateId);
      }
    }
  }

  const reviewCandidates =
    selectedMarker == null
      ? []
      : Array.from(markerReviewCandidateIds)
          .map((candidateId) => candidatesById.get(candidateId) ?? null)
          .filter((candidate): candidate is CalloutCandidate => candidate != null)
          .map((candidate) => {
            const linkedConflict = selectedMarkerAmbiguityConflicts.find((conflict) => conflict.candidateIds.includes(candidate.candidateId)) ?? null;
            const distanceToMarker = Math.hypot(candidate.centerX - selectedMarker.x, candidate.centerY - selectedMarker.y);
            const sameSuggestedLabel =
              selectedMarkerNormalizedLabel.length > 0 &&
              normalizeConflictLabel(candidate.suggestedLabel) === selectedMarkerNormalizedLabel;

            return {
              candidate,
              linkedConflict,
              distanceToMarker,
              sameSuggestedLabel,
              associationCount: candidateAssociationCountById.get(candidate.candidateId) ?? 0
            };
          })
          .sort((left, right) => {
            if (Number(Boolean(right.linkedConflict)) !== Number(Boolean(left.linkedConflict))) {
              return Number(Boolean(right.linkedConflict)) - Number(Boolean(left.linkedConflict));
            }
            if (Number(right.sameSuggestedLabel) !== Number(left.sameSuggestedLabel)) {
              return Number(right.sameSuggestedLabel) - Number(left.sameSuggestedLabel);
            }
            if (right.associationCount !== left.associationCount) {
              return right.associationCount - left.associationCount;
            }
            if (Math.abs(right.candidate.score - left.candidate.score) > 0.001) {
              return right.candidate.score - left.candidate.score;
            }
            return left.distanceToMarker - right.distanceToMarker;
          })
          .slice(0, 4);

  const reviewMessages = Array.from(
    new Set(selectedMarkerAmbiguityConflicts.map((conflict) => conflict.message.trim()).filter(Boolean))
  ).slice(0, 2);
  const reviewTypeLabels = Array.from(
    new Set(
      selectedMarkerAmbiguityConflicts.map(
        (conflict) => pipelineConflictTypeLabels[conflict.type as keyof typeof pipelineConflictTypeLabels] ?? conflict.type
      )
    )
  );
  const hasSelectedReview = selectedMarker?.status === "ai_review" && selectedMarkerAmbiguityConflicts.length > 0;
  const canConfirm = Boolean((draftLabel || selectedMarker?.label || "").trim());
  const canSkip = hasSelectedReview && !showDeferredAmbiguityMarkersOnly;

  return {
    reviewCandidates,
    reviewMessages,
    reviewTypeLabels,
    hasSelectedReview,
    canConfirm,
    canSkip
  };
}

export function buildAmbiguityQueueViewModel(args: {
  sessionMarkers: Marker[];
  selectedMarkerId: string | null;
  showAmbiguityMarkersOnly: boolean;
  showDeferredAmbiguityMarkersOnly: boolean;
  activeAmbiguityQueueMarkers: Marker[];
  currentPassCount: number;
  deferredCount: number;
}): AmbiguityQueueViewModel {
  const {
    sessionMarkers,
    selectedMarkerId,
    showAmbiguityMarkersOnly,
    showDeferredAmbiguityMarkersOnly,
    activeAmbiguityQueueMarkers,
    currentPassCount,
    deferredCount
  } = args;

  const firstMarkerId = activeAmbiguityQueueMarkers[0]?.markerId ?? null;
  const displayedMarkers = showAmbiguityMarkersOnly ? activeAmbiguityQueueMarkers : sessionMarkers;
  const selectedMarkerIndex =
    selectedMarkerId == null ? -1 : activeAmbiguityQueueMarkers.findIndex((marker) => marker.markerId === selectedMarkerId);
  const currentPosition = selectedMarkerIndex >= 0 ? selectedMarkerIndex + 1 : 0;
  const progress =
    activeAmbiguityQueueMarkers.length > 0 && currentPosition > 0 ? currentPosition / activeAmbiguityQueueMarkers.length : 0;
  const selectedQueueTitle = showDeferredAmbiguityMarkersOnly ? "Отложенный проход" : "Review-проход";
  const selectedQueueHint =
    activeAmbiguityQueueMarkers.length > 0
      ? showDeferredAmbiguityMarkersOnly
        ? `Сейчас точка ${Math.max(currentPosition, 1)} из ${activeAmbiguityQueueMarkers.length} в отдельной очереди отложенных ambiguity-кейсов.`
        : showAmbiguityMarkersOnly
          ? `Сейчас точка ${Math.max(currentPosition, 1)} из ${activeAmbiguityQueueMarkers.length} в активной review-очереди.`
          : `Открыт общий список, но эта точка идёт как ${Math.max(currentPosition, 1)} из ${activeAmbiguityQueueMarkers.length} в рабочей review-очереди.`
      : showDeferredAmbiguityMarkersOnly
        ? "Открыт отдельный проход по ранее отложенным ambiguity-кейсам."
        : "Точка относится к текущей рабочей review-очереди.";
  const activeQueueLabel =
    showDeferredAmbiguityMarkersOnly ? historyQueueContextLabels.deferred : showAmbiguityMarkersOnly ? historyQueueContextLabels.review : "рабочая очередь";
  const hasUnresolvedMarkers = currentPassCount > 0 || deferredCount > 0;

  return {
    activeQueueLabel,
    firstMarkerId,
    displayedMarkers,
    selectedMarkerIndex,
    currentPosition,
    progress,
    selectedQueueTitle,
    selectedQueueHint,
    hasUnresolvedMarkers
  };
}
