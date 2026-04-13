"use client";

import Image, { type ImageLoaderProps } from "next/image";
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { applySessionCommand, autoAnnotateSession, downloadSessionExport, refreshSession, rejectSessionCandidate, resolveAssetUrl, uploadDocument } from "@/lib/api";
import type { ActionLogEntry, AnnotationSession, CalloutCandidate, CandidateAssociation, Marker, MarkerPointType, MarkerStatus, Viewport } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { classNames } from "@/components/ui/utils";
import {
  buildAmbiguityQueueViewModel,
  buildHistoryAlternateQueueAction,
  buildSelectedAmbiguityReviewViewModel,
  getNextMarkerIdInQueue,
  getPrimaryHistoryQueueContext,
  getQueueCountByContext,
  historyQueueContextLabels,
  normalizeConflictLabel,
  type AmbiguityReviewCandidateViewModel,
  type HistoryAlternateQueueAction,
  type HistoryJumpContext,
  type HistoryQueueContext,
  type HistoryQueueMode,
  type MarkerQueueMode,
  type SessionPipelineConflict
} from "./annotation-workspace-state";

const pointTypeLabels: Record<MarkerPointType, string> = {
  center: "Середина",
  top_left: "Верхний левый"
};

const candidateKindLabels = {
  circle: "Круг",
  box: "Рамка",
  text: "Текст"
} as const;

const markerStatusLabels: Record<MarkerStatus, string> = {
  human_draft: "Черновик",
  ai_detected: "AI нашёл",
  ai_review: "AI review",
  human_confirmed: "Подтверждено",
  human_corrected: "Исправлено",
  rejected: "Ложный"
};

const NEAR_TIE_AMBIGUITY_TOKEN = "OCR near-tie ambiguity";

function hasNearTieAmbiguity(message: string | null | undefined) {
  return (message ?? "").includes(NEAR_TIE_AMBIGUITY_TOKEN);
}

const actionActorLabels = {
  human: "человек",
  ai: "ai",
  system: "система"
} as const;

const ambiguityRouteLabels = {
  confirmed: "подтверждено",
  deleted: "ложный",
  skipped: "отложено",
  restored: "возвращено"
} as const;

const ambiguityNextStepHints = {
  skipped: "Следующий шаг: вернуть точку в review и принять финальное решение.",
  restored: "Следующий шаг: подтвердить точку или отметить её как ложную."
} as const;

const historyActionLabels = {
  nextCase: "следующий кейс",
  toReview: "в review",
  toDeferred: "в отложенные",
  allActions: "все действия",
  ambiguityOnly: "только спорные решения",
  compact: "компактно",
  jumpToPoint: "к точке"
} as const;

const historyStatusLabels = {
  journal: "журнал",
  ambiguity: "ambiguity",
  all: "все",
  compact: "compact",
  full: "full",
  primary: "primary",
  review: "review",
  deferred: "отложенные",
  noQueue: "без очереди"
} as const;

type AmbiguityHistorySummary = Record<keyof typeof ambiguityRouteLabels, number>;

type MarkerItemTone = "normal" | "conflict";

type HistoryEntryCardViewModel = {
  route: NonNullable<RenderedHistoryEntry["ambiguityDecision"]>[];
  canJumpToMarker: boolean;
  historyJumpContext: HistoryJumpContext;
  nextStepHint: string | null;
  ambiguityMetaLabel: string | null;
};

type AmbiguityReviewState = {
  ambiguityReviewMarkers: Marker[];
  showAmbiguityMarkersOnly: boolean;
  showDeferredAmbiguityMarkersOnly: boolean;
  deferredAmbiguityMarkers: Marker[];
  currentPassAmbiguityMarkers: Marker[];
  currentPassAmbiguityMarkerIds: ReadonlySet<string>;
  deferredAmbiguityMarkerIds: ReadonlySet<string>;
  activeAmbiguityQueueMarkers: Marker[];
  activeAmbiguityQueueLabel: string;
  hasDeferredAmbiguityMarkers: boolean;
  selectedMarkerAmbiguityConflicts: SessionPipelineConflict[];
  selectedAmbiguityReviewCandidates: AmbiguityReviewCandidateViewModel[];
  selectedAmbiguityReviewMessages: string[];
  selectedAmbiguityReviewTypeLabels: string[];
  hasSelectedAmbiguityReview: boolean;
  canConfirmSelectedAmbiguityReview: boolean;
  canSkipSelectedAmbiguityReview: boolean;
  firstAmbiguityMarkerId: string | null;
  displayedMarkers: Marker[];
  selectedAmbiguityMarkerIndex: number;
  ambiguityReviewCurrentPosition: number;
  ambiguityReviewProgress: number;
  selectedAmbiguityQueueTitle: string;
  selectedAmbiguityQueueHint: string;
  hasUnresolvedAmbiguityMarkers: boolean;
};

type BuildHistoryEntryCardViewModelArgs = {
  entry: RenderedHistoryEntry;
  ambiguityRouteByMarkerId: Readonly<Record<string, NonNullable<RenderedHistoryEntry["ambiguityDecision"]>[]>>;
  currentPassAmbiguityMarkerIds: ReadonlySet<string>;
  deferredAmbiguityMarkerIds: ReadonlySet<string>;
  sessionMarkerIds: ReadonlySet<string>;
};

type BuildAmbiguityReviewStateArgs = {
  sessionMarkers: Marker[];
  markerAmbiguityConflictsById: ReadonlyMap<string, SessionPipelineConflict[]>;
  reviewedAmbiguityMarkerIds: string[];
  skippedAmbiguityMarkerIds: string[];
  ambiguityMarkerFilterMode: MarkerQueueMode;
  selectedMarker: Marker | null;
  pendingCandidates: CalloutCandidate[];
  candidatesById: ReadonlyMap<string, CalloutCandidate>;
  candidateAssociationCountById: ReadonlyMap<string, number>;
  selectedMarkerId: string | null;
  draftLabel: string;
};

const historyQueueContextToMode: Record<HistoryQueueContext, HistoryQueueMode> = {
  review: "current",
  deferred: "deferred"
};

function buildAmbiguityReviewState({
  sessionMarkers,
  markerAmbiguityConflictsById,
  reviewedAmbiguityMarkerIds,
  skippedAmbiguityMarkerIds,
  ambiguityMarkerFilterMode,
  selectedMarker,
  pendingCandidates,
  candidatesById,
  candidateAssociationCountById,
  selectedMarkerId,
  draftLabel
}: BuildAmbiguityReviewStateArgs): AmbiguityReviewState {
  const ambiguityReviewMarkers = sessionMarkers.filter(
    (marker) => marker.status === "ai_review" && (markerAmbiguityConflictsById.get(marker.markerId)?.length ?? 0) > 0
  );
  const showAmbiguityMarkersOnly = ambiguityMarkerFilterMode !== "all";
  const showDeferredAmbiguityMarkersOnly = ambiguityMarkerFilterMode === "deferred";
  const reviewedAmbiguityMarkerIdSet = new Set(reviewedAmbiguityMarkerIds);
  const skippedAmbiguityMarkerIdSet = new Set(skippedAmbiguityMarkerIds);
  const deferredAmbiguityMarkers = ambiguityReviewMarkers.filter((marker) => skippedAmbiguityMarkerIdSet.has(marker.markerId));
  const currentPassAmbiguityMarkers = ambiguityReviewMarkers.filter(
    (marker) => !reviewedAmbiguityMarkerIdSet.has(marker.markerId) && !skippedAmbiguityMarkerIdSet.has(marker.markerId)
  );
  const currentPassAmbiguityMarkerIds = new Set(currentPassAmbiguityMarkers.map((marker) => marker.markerId));
  const deferredAmbiguityMarkerIds = new Set(deferredAmbiguityMarkers.map((marker) => marker.markerId));
  const activeAmbiguityQueueMarkers = showDeferredAmbiguityMarkersOnly ? deferredAmbiguityMarkers : currentPassAmbiguityMarkers;
  const hasDeferredAmbiguityMarkers = deferredAmbiguityMarkers.length > 0;
  const selectedMarkerAmbiguityConflicts =
    selectedMarker == null ? [] : markerAmbiguityConflictsById.get(selectedMarker.markerId) ?? [];
  const {
    reviewCandidates: selectedAmbiguityReviewCandidates,
    reviewMessages: selectedAmbiguityReviewMessages,
    reviewTypeLabels: selectedAmbiguityReviewTypeLabels,
    hasSelectedReview: hasSelectedAmbiguityReview,
    canConfirm: canConfirmSelectedAmbiguityReview,
    canSkip: canSkipSelectedAmbiguityReview
  } = buildSelectedAmbiguityReviewViewModel({
    selectedMarker,
    selectedMarkerAmbiguityConflicts,
    pendingCandidates,
    candidatesById,
    candidateAssociationCountById,
    draftLabel,
    showDeferredAmbiguityMarkersOnly
  });
  const {
    activeQueueLabel: activeAmbiguityQueueLabel,
    firstMarkerId: firstAmbiguityMarkerId,
    displayedMarkers,
    selectedMarkerIndex: selectedAmbiguityMarkerIndex,
    currentPosition: ambiguityReviewCurrentPosition,
    progress: ambiguityReviewProgress,
    selectedQueueTitle: selectedAmbiguityQueueTitle,
    selectedQueueHint: selectedAmbiguityQueueHint,
    hasUnresolvedMarkers: hasUnresolvedAmbiguityMarkers
  } = buildAmbiguityQueueViewModel({
    sessionMarkers,
    selectedMarkerId,
    showAmbiguityMarkersOnly,
    showDeferredAmbiguityMarkersOnly,
    activeAmbiguityQueueMarkers,
    currentPassCount: currentPassAmbiguityMarkers.length,
    deferredCount: deferredAmbiguityMarkers.length
  });

  return {
    ambiguityReviewMarkers,
    showAmbiguityMarkersOnly,
    showDeferredAmbiguityMarkersOnly,
    deferredAmbiguityMarkers,
    currentPassAmbiguityMarkers,
    currentPassAmbiguityMarkerIds,
    deferredAmbiguityMarkerIds,
    activeAmbiguityQueueMarkers,
    activeAmbiguityQueueLabel,
    hasDeferredAmbiguityMarkers,
    selectedMarkerAmbiguityConflicts,
    selectedAmbiguityReviewCandidates,
    selectedAmbiguityReviewMessages,
    selectedAmbiguityReviewTypeLabels,
    hasSelectedAmbiguityReview,
    canConfirmSelectedAmbiguityReview,
    canSkipSelectedAmbiguityReview,
    firstAmbiguityMarkerId,
    displayedMarkers,
    selectedAmbiguityMarkerIndex,
    ambiguityReviewCurrentPosition,
    ambiguityReviewProgress,
    selectedAmbiguityQueueTitle,
    selectedAmbiguityQueueHint,
    hasUnresolvedAmbiguityMarkers
  };
}

function HistoryQueueChip({
  queueContext,
  count
}: {
  queueContext: HistoryQueueContext;
  count?: number;
}) {
  return (
    <span
      className={classNames(
        "inline-flex min-h-6 items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]",
        queueContext === "review"
          ? "border-[#7a5a23] bg-[#2e2418] text-[#f5d0a8]"
          : "border-[#2b4b67] bg-[#162433] text-[#bfe1ff]"
      )}
    >
      {historyQueueContextLabels[queueContext]}
      {count != null ? ` ${count}` : ""}
    </span>
  );
}

function HistorySummaryChips({
  summary,
  compact
}: {
  summary: AmbiguityHistorySummary;
  compact: boolean;
}) {
  const chips = [
    {
      key: "confirmed",
      count: summary.confirmed,
      label: "подтверждено",
      className: "border-[#3e5f2b] bg-[#1c2718] text-[#d7f5c9]"
    },
    {
      key: "deleted",
      count: summary.deleted,
      label: "ложных",
      className: "border-[#7b2d2d] bg-[#331d1e] text-[#ffcccc]"
    },
    {
      key: "skipped",
      count: summary.skipped,
      label: "отложено",
      className: "border-[#5a5f69] bg-[#1d2026] text-[#d7dbe2]"
    },
    {
      key: "restored",
      count: summary.restored,
      label: "возвращено",
      className: "border-[#2b4b67] bg-[#162433] text-[#bfe1ff]"
    }
  ].filter((chip) => chip.count > 0);

  if (chips.length === 0) {
    return null;
  }

  return (
    <div className={classNames("flex flex-wrap items-center", compact ? "mt-1.5 gap-1.5" : "mt-2 gap-2")}>
      {chips.map((chip) => (
        <span
          key={chip.key}
          className={classNames(
            "inline-flex min-h-6 items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]",
            chip.className
          )}
        >
          {chip.label} {chip.count}
        </span>
      ))}
    </div>
  );
}

function HistoryNextStepPanel({
  compact,
  hint,
  currentPassCount,
  deferredCount,
  primaryQueueContext,
  onContinue,
  onOpenQueue
}: {
  compact: boolean;
  hint: string | null;
  currentPassCount: number;
  deferredCount: number;
  primaryQueueContext: HistoryQueueContext | null;
  onContinue: () => void;
  onOpenQueue: (mode: HistoryQueueMode) => void;
}) {
  if (!hint) {
    return null;
  }

  const hasQueueActions = currentPassCount > 0 || deferredCount > 0;

  return (
    <div
      className={classNames(
        "rounded-[0.85rem] border border-white/8 bg-black/10 transition-[padding,margin] duration-150",
        compact ? "mt-1.5 px-2.5 py-1.5" : "mt-2 px-3 py-2"
      )}
    >
      {!compact && <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#d7dbe2]">что сейчас осталось</p>}
      {!compact && <p className="mt-1 text-[12px] text-[#aeb4be]">{hint}</p>}
      {hasQueueActions && (
        <div className={classNames("flex flex-wrap gap-2", compact ? "mt-0" : "mt-2")}>
          <div className="inline-flex items-center gap-1.5 rounded-[0.7rem] border border-[#3e5f2b] bg-[#1c2718] px-1.5 py-1">
            {primaryQueueContext && <HistoryQueueChip queueContext={primaryQueueContext} />}
            <button
              type="button"
              className="inline-flex min-h-7 items-center rounded-[0.7rem] px-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#d7f5c9] transition"
              onClick={onContinue}
            >
              {historyActionLabels.nextCase}
            </button>
          </div>
          {currentPassCount > 0 && primaryQueueContext !== "review" && (
            <button
              type="button"
              className="inline-flex min-h-7 items-center rounded-[0.7rem] border border-[#7a5a23] bg-[#2e2418] px-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8] transition"
              onClick={() => onOpenQueue("current")}
            >
              {historyActionLabels.toReview} {currentPassCount}
            </button>
          )}
          {deferredCount > 0 && primaryQueueContext !== "deferred" && (
            <button
              type="button"
              className="inline-flex min-h-7 items-center rounded-[0.7rem] border border-[#2b4b67] bg-[#162433] px-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#bfe1ff] transition"
              onClick={() => onOpenQueue("deferred")}
            >
              {historyActionLabels.toDeferred} {deferredCount}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function HistoryStickyToolbar({
  compact,
  hasUnresolved,
  primaryQueueContext,
  nextUnresolvedCount,
  historyAlternateQueueAction,
  showOnlyAmbiguityHistory,
  ambiguityHistoryCount,
  isCompactPinned,
  historyModeScopeLabel,
  historyModeQueueLabel,
  onContinue,
  onOpenQueue,
  onToggleScope,
  onToggleCompactPinned
}: {
  compact: boolean;
  hasUnresolved: boolean;
  primaryQueueContext: HistoryQueueContext | null;
  nextUnresolvedCount: number;
  historyAlternateQueueAction: HistoryAlternateQueueAction | null;
  showOnlyAmbiguityHistory: boolean;
  ambiguityHistoryCount: number;
  isCompactPinned: boolean;
  historyModeScopeLabel: string;
  historyModeQueueLabel: string;
  onContinue: () => void;
  onOpenQueue: (mode: HistoryQueueMode) => void;
  onToggleScope: () => void;
  onToggleCompactPinned: () => void;
}) {
  const canToggleAmbiguityScope = showOnlyAmbiguityHistory || ambiguityHistoryCount > 0;

  return (
    <div className={classNames("flex items-center justify-between gap-3", compact ? "mt-2" : "mt-3")}>
      <div className="flex items-center gap-2">
        {hasUnresolved && (
          <div className="inline-flex items-center gap-1.5 rounded-full border border-[#3e5f2b] bg-[#1c2718] px-1.5 py-1">
            {primaryQueueContext && <HistoryQueueChip queueContext={primaryQueueContext} count={nextUnresolvedCount} />}
            <button
              type="button"
              className="inline-flex min-h-8 items-center rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#d7f5c9] transition"
              onClick={onContinue}
            >
              {historyActionLabels.nextCase}
            </button>
          </div>
        )}
        {historyAlternateQueueAction && (
          <button
            type="button"
            className="inline-flex min-h-8 items-center rounded-full border border-[#2b4b67] bg-[#162433] px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#bfe1ff] transition"
            onClick={() => onOpenQueue(historyAlternateQueueAction.mode)}
          >
            {historyAlternateQueueAction.label}
          </button>
        )}
        {canToggleAmbiguityScope && (
          <button
            type="button"
            className={classNames(
              "inline-flex min-h-8 items-center rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] transition",
              showOnlyAmbiguityHistory
                ? "border-[#7a5a23] bg-[#2e2418] text-[#f5d0a8]"
                : "border-white/10 bg-white/5 text-[#c8ccd3]"
            )}
            onClick={onToggleScope}
          >
            {showOnlyAmbiguityHistory
              ? historyActionLabels.allActions
              : `${historyActionLabels.ambiguityOnly} ${ambiguityHistoryCount}`}
          </button>
        )}
        <button
          type="button"
          aria-pressed={isCompactPinned}
          className={classNames(
            "inline-flex min-h-8 items-center rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] transition",
            isCompactPinned
              ? "border-[#2b4b67] bg-[#162433] text-[#bfe1ff]"
              : "border-white/10 bg-white/5 text-[#c8ccd3]"
          )}
          onClick={onToggleCompactPinned}
        >
          {historyActionLabels.compact}
        </button>
      </div>
      <div className="flex flex-col items-end gap-1.5">
        <div className="flex flex-wrap items-center justify-end gap-1 text-[10px] uppercase tracking-[0.12em] text-[#7f8691]">
          <span>{historyStatusLabels.journal}</span>
          <span className="text-[#626874]">→</span>
          {canToggleAmbiguityScope ? (
            <button type="button" className="transition" onClick={onToggleScope}>
              {historyModeScopeLabel}
            </button>
          ) : (
            <span>{historyStatusLabels.all}</span>
          )}
          <span className="text-[#626874]">→</span>
          {primaryQueueContext ? (
            <button
              type="button"
              className="transition"
              onClick={() => onOpenQueue(historyQueueContextToMode[primaryQueueContext])}
            >
              {historyModeQueueLabel}
            </button>
          ) : (
            <span>{historyModeQueueLabel}</span>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5 text-[10px] uppercase tracking-[0.12em]">
          {canToggleAmbiguityScope && (
            <button
              type="button"
              className={classNames(
                "inline-flex min-h-6 items-center rounded-full border px-2 py-0.5 transition",
                showOnlyAmbiguityHistory
                  ? "border-[#7a5a23] bg-[#2e2418] text-[#f5d0a8]"
                  : "border-white/10 bg-white/5 text-[#aeb4be]"
              )}
              onClick={onToggleScope}
            >
              {showOnlyAmbiguityHistory ? historyStatusLabels.ambiguity : historyStatusLabels.all}
            </button>
          )}
          <span
            className={classNames(
              "inline-flex min-h-6 items-center rounded-full border px-2 py-0.5",
              compact
                ? "border-[#2b4b67] bg-[#162433] text-[#bfe1ff]"
                : "border-white/10 bg-white/5 text-[#aeb4be]"
            )}
          >
            {compact ? historyStatusLabels.compact : historyStatusLabels.full}
          </span>
          {primaryQueueContext && hasUnresolved && (
            <button
              type="button"
              className={classNames(
                "inline-flex min-h-6 items-center rounded-full border px-2 py-0.5 transition",
                primaryQueueContext === "review"
                  ? "border-[#7a5a23] bg-[#2e2418] text-[#f5d0a8]"
                  : "border-[#2b4b67] bg-[#162433] text-[#bfe1ff]"
              )}
              onClick={() => onOpenQueue(historyQueueContextToMode[primaryQueueContext])}
            >
              {historyStatusLabels.primary} {historyQueueContextLabels[primaryQueueContext]}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function HistoryCardHeader({
  entry,
  canJumpToMarker,
  historyJumpContext,
  ambiguityMetaLabel,
  onJumpToMarker
}: {
  entry: RenderedHistoryEntry;
  canJumpToMarker: boolean;
  historyJumpContext: HistoryJumpContext;
  ambiguityMetaLabel: string | null;
  onJumpToMarker: (markerId: string | null) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-white">{entry.presentation.title}</p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
        {canJumpToMarker && entry.markerId && (
          <button
            type="button"
            className="inline-flex min-h-6 items-center gap-1 rounded-full border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-white transition"
            onClick={() => onJumpToMarker(entry.markerId)}
          >
            <span
              className={classNames(
                "inline-flex min-h-5 items-center rounded-full border px-1.5 py-0 text-[9px] font-semibold uppercase tracking-[0.12em]",
                historyJumpContext === "review"
                  ? "border-[#7a5a23] bg-[#2e2418] text-[#f5d0a8]"
                  : historyJumpContext === "deferred"
                    ? "border-[#2b4b67] bg-[#162433] text-[#bfe1ff]"
                    : "border-white/10 bg-[#1c2026] text-[#d0d4db]"
              )}
            >
              {historyQueueContextLabels[historyJumpContext]}
            </span>
            <span className="pr-0.5">{historyActionLabels.jumpToPoint}</span>
          </button>
        )}
        <span className="text-[10px] uppercase tracking-[0.16em] text-[#9499a3]">
          {ambiguityMetaLabel ? `${ambiguityMetaLabel} · ${actionActorLabels[entry.actor]}` : actionActorLabels[entry.actor]}
        </span>
      </div>
    </div>
  );
}

function HistoryRouteRow({
  entryId,
  markerId,
  route,
  currentDecision,
  canJumpToMarker,
  onJumpToMarker
}: {
  entryId: string;
  markerId: string | null;
  route: NonNullable<RenderedHistoryEntry["ambiguityDecision"]>[];
  currentDecision: RenderedHistoryEntry["ambiguityDecision"];
  canJumpToMarker: boolean;
  onJumpToMarker: (markerId: string | null) => void;
}) {
  if (route.length <= 1) {
    return null;
  }

  return (
    <div
      className="mt-1 flex flex-wrap items-center gap-1 text-[9px] uppercase tracking-[0.1em] text-[#94a0ad]"
      title="Маршрут ambiguity-решения"
      aria-label="Маршрут ambiguity-решения"
    >
      {route.map((decision, routeIndex) => {
        const isCurrentRouteStep = decision === currentDecision;
        const key = `${entryId}-route-${routeIndex}-${decision}`;

        return (
          <Fragment key={key}>
            {canJumpToMarker && markerId ? (
              <button
                type="button"
                className={classNames(
                  "inline-flex min-h-5 items-center rounded-full border px-1.5 py-0.5 transition",
                  isCurrentRouteStep
                    ? "border-[#f5d0a8] bg-[#2e2418] font-semibold text-[#f5d0a8]"
                    : "border-white/8 bg-black/10 text-[#d6dae1]"
                )}
                onClick={() => onJumpToMarker(markerId)}
                title={isCurrentRouteStep ? "Текущий шаг маршрута. Открыть эту точку на холсте" : "Открыть эту точку на холсте"}
                aria-current={isCurrentRouteStep ? "step" : undefined}
              >
                {ambiguityRouteLabels[decision]}
              </button>
            ) : (
              <span
                className={classNames(
                  "inline-flex min-h-5 items-center rounded-full border px-1.5 py-0.5",
                  isCurrentRouteStep
                    ? "border-[#f5d0a8] bg-[#2e2418] font-semibold text-[#f5d0a8]"
                    : "border-white/8 bg-black/10 text-[#d6dae1]"
                )}
                aria-current={isCurrentRouteStep ? "step" : undefined}
              >
                {ambiguityRouteLabels[decision]}
              </span>
            )}
            {routeIndex < route.length - 1 && <span className="text-[9px] text-[#6f7681]">→</span>}
          </Fragment>
        );
      })}
    </div>
  );
}

function HistoryEntryCard({
  entry,
  route,
  canJumpToMarker,
  historyJumpContext,
  nextStepHint,
  ambiguityMetaLabel,
  onJumpToMarker
}: {
  entry: RenderedHistoryEntry;
  route: NonNullable<RenderedHistoryEntry["ambiguityDecision"]>[];
  canJumpToMarker: boolean;
  historyJumpContext: HistoryJumpContext;
  nextStepHint: string | null;
  ambiguityMetaLabel: string | null;
  onJumpToMarker: (markerId: string | null) => void;
}) {
  return (
    <div
      className={classNames(
        "rounded-[1rem] border px-2.5 py-2",
        entry.ambiguityDecision === "confirmed"
          ? "border-[#3e5f2b] bg-[#172016]"
          : entry.ambiguityDecision === "deleted"
            ? "border-[#7b2d2d] bg-[#241617]"
            : entry.ambiguityDecision === "skipped"
              ? "border-[#5a5f69] bg-[#1d2026]"
              : entry.ambiguityDecision === "restored"
                ? "border-[#2b4b67] bg-[#162433]"
                : "border-white/8 bg-[#111317]"
      )}
    >
      <HistoryCardHeader
        entry={entry}
        canJumpToMarker={canJumpToMarker}
        historyJumpContext={historyJumpContext}
        ambiguityMetaLabel={ambiguityMetaLabel}
        onJumpToMarker={onJumpToMarker}
      />
      <HistoryRouteRow
        entryId={entry.id}
        markerId={entry.markerId}
        route={route}
        currentDecision={entry.ambiguityDecision}
        canJumpToMarker={canJumpToMarker}
        onJumpToMarker={onJumpToMarker}
      />
      {nextStepHint && (
        <div className="mt-1 flex items-start gap-1.5 rounded-[0.75rem] border border-white/8 bg-black/10 px-2 py-1.5">
          <span className="inline-flex min-h-5 shrink-0 items-center rounded-full border border-white/10 bg-white/5 px-1.5 py-0 text-[9px] font-semibold uppercase tracking-[0.12em] text-[#d7dbe2]">
            дальше
          </span>
          <p className="text-[11px] leading-4 text-[#aeb4be]">{nextStepHint}</p>
        </div>
      )}
      {entry.presentation.details.length > 0 && (
        <div className="mt-1.5 space-y-0.5">
          {entry.presentation.details.map((detail, detailIndex) => (
            <p key={`${entry.id}-${detailIndex}`} className="text-[11px] leading-4 text-[#9ba1ab]">
              {detail}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function buildHistoryEntryCardViewModel({
  entry,
  ambiguityRouteByMarkerId,
  currentPassAmbiguityMarkerIds,
  deferredAmbiguityMarkerIds,
  sessionMarkerIds
}: BuildHistoryEntryCardViewModelArgs): HistoryEntryCardViewModel {
  const route =
    entry.markerId && entry.ambiguityDecision !== null ? ambiguityRouteByMarkerId[entry.markerId] ?? [] : [];
  const canJumpToMarker = entry.markerId != null && sessionMarkerIds.has(entry.markerId);
  const historyJumpContext = currentPassAmbiguityMarkerIds.has(entry.markerId ?? "")
    ? "review"
    : deferredAmbiguityMarkerIds.has(entry.markerId ?? "")
      ? "deferred"
      : "all";
  const nextStepHint =
    entry.ambiguityDecision === "skipped" || entry.ambiguityDecision === "restored"
      ? ambiguityNextStepHints[entry.ambiguityDecision]
      : null;
  const ambiguityMetaLabel = entry.ambiguityDecision !== null ? ambiguityRouteLabels[entry.ambiguityDecision] : null;
  return {
    route,
    canJumpToMarker,
    historyJumpContext,
    nextStepHint,
    ambiguityMetaLabel
  };
}

function MarkerListItem({
  marker,
  selected,
  tone,
  hasAmbiguityReview,
  hasNearTieReview,
  ambiguityTooltip,
  onSelect
}: {
  marker: Marker;
  selected: boolean;
  tone: MarkerItemTone;
  hasAmbiguityReview: boolean;
  hasNearTieReview: boolean;
  ambiguityTooltip: string;
  onSelect: () => void;
}) {
  const isTopLeftPoint = marker.pointType === "top_left";
  const isConflict = tone === "conflict";

  return (
    <button
      type="button"
      onClick={onSelect}
      className={classNames(
        "block w-full rounded-[0.9rem] border px-3 py-2 text-left transition",
        selected
          ? "border-[#474c55] bg-[#22262d] shadow-[0_10px_24px_rgba(10,12,16,0.28)]"
          : "border-transparent bg-transparent",
        (isConflict || hasAmbiguityReview) && "border-[#6d4a1a] bg-[#231d15]/70",
        hasNearTieReview && "border-[#7b3aed] bg-[#221933]/75"
      )}
    >
      <div className="flex items-start gap-3">
        <span className={classNames("mt-1 h-2.5 w-2.5 rounded-full", isTopLeftPoint ? "bg-[#16a34a]" : "bg-[#d92d20]")} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p
              className={classNames(
                "truncate text-[13px] font-semibold text-white",
                isConflict && "underline decoration-[#f59e0b] decoration-2 underline-offset-4"
              )}
            >
              {marker.label ?? "Без ярлыка"}
            </p>
            {marker.status === "human_draft" && (
              <span className="inline-flex items-center rounded-full border border-[#6d4a1a] bg-[#2a2118] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8]">
                черновик
              </span>
            )}
            {hasAmbiguityReview && (
              <span
                title={ambiguityTooltip || "Есть спор по AI-разметке"}
                className="inline-flex items-center rounded-full border border-[#7a5a23] bg-[#2e2418] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8]"
              >
                AI review
              </span>
            )}
            {hasNearTieReview && (
              <span
                title="Один bbox содержит почти равный OCR-спор между двумя цифрами"
                className="inline-flex items-center rounded-full border border-[#7c3aed] bg-[#271a3f] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#dcc8ff]"
              >
                OCR split
              </span>
            )}
            {isConflict && (
              <span className="inline-flex items-center rounded-full border border-[#6d4a1a] bg-[#2a2118] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8]">
                конфликт
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-[#9ba1ab]">
            {pointTypeLabels[marker.pointType ?? "center"]} • {Math.round(marker.x)}, {Math.round(marker.y)}
          </p>
        </div>
      </div>
    </button>
  );
}

function AmbiguityReviewCandidateCard({
  item,
  onSelectCandidate,
  onMoveMarker
}: {
  item: AmbiguityReviewCandidateViewModel;
  onSelectCandidate: (candidateId: string) => void;
  onMoveMarker: (x: number, y: number) => void;
}) {
  const { candidate, linkedConflict, distanceToMarker, sameSuggestedLabel, associationCount } = item;

  return (
    <div className="rounded-[0.9rem] border border-white/8 bg-black/10 px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[12px] font-semibold text-white">
            {candidate.suggestedLabel ? `№ ${candidate.suggestedLabel}` : candidateKindLabels[candidate.kind]}
          </p>
          <p className="mt-1 text-xs text-[#d7dbe2]">
            {candidateKindLabels[candidate.kind]} • score {candidate.score.toFixed(2)} • {Math.round(distanceToMarker)} px
          </p>
          <p className="mt-1 text-[11px] text-[#aeb4be]">
            {candidate.suggestedSource ?? "локальный кандидат"}
            {candidate.suggestedConfidence != null ? ` • ${formatCandidateConfidence(candidate.suggestedConfidence)}` : ""}
            {associationCount > 0 ? ` • связей ${associationCount}` : ""}
          </p>
          {(sameSuggestedLabel || linkedConflict?.message) && (
            <p className="mt-1 text-[11px] text-[#d9bf9b]">
              {linkedConflict?.message ?? "Ярлык совпадает с текущей точкой."}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col gap-1.5">
          <button
            type="button"
            className="inline-flex min-h-7 items-center justify-center rounded-[0.7rem] border border-white/10 bg-white/[0.05] px-2.5 text-[11px] font-medium text-white transition"
            onClick={() => onSelectCandidate(candidate.candidateId)}
          >
            К кандидату
          </button>
          <button
            type="button"
            className="inline-flex min-h-7 items-center justify-center rounded-[0.7rem] border border-[#3e5f2b] bg-[#1c2718] px-2.5 text-[11px] font-medium text-[#d7f5c9] transition"
            onClick={() => onMoveMarker(candidate.centerX, candidate.centerY)}
          >
            Поставить сюда
          </button>
        </div>
      </div>
    </div>
  );
}

function AmbiguityReviewPanel({
  reviewCandidates,
  reviewConflictCount,
  reviewTypeLabels,
  reviewMessages,
  hasNearTieAmbiguity,
  reviewQueueTitle,
  reviewQueueHint,
  showDeferredPass,
  canSkip,
  canConfirm,
  busy,
  hasConflictFocus,
  onFocusConflict,
  onSelectCandidate,
  onMoveMarker,
  onSkip,
  onDelete,
  onConfirm
}: {
  reviewCandidates: AmbiguityReviewCandidateViewModel[];
  reviewConflictCount: number;
  reviewTypeLabels: string[];
  reviewMessages: string[];
  hasNearTieAmbiguity: boolean;
  reviewQueueTitle: string;
  reviewQueueHint: string;
  showDeferredPass: boolean;
  canSkip: boolean;
  canConfirm: boolean;
  busy: boolean;
  hasConflictFocus: boolean;
  onFocusConflict: () => void;
  onSelectCandidate: (candidateId: string) => void;
  onMoveMarker: (x: number, y: number) => void;
  onSkip: () => void;
  onDelete: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="space-y-3 rounded-[1rem] border border-[#5f461c] bg-[#231d15] p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#f5d0a8]">AI review</p>
          <p className="mt-1 text-sm text-[#f2e5d0]">
            Точка помечена как спорная. Рядом показаны причины и ближайшие альтернативы для быстрой проверки.
          </p>
        </div>
        <span className="inline-flex min-h-8 items-center rounded-full border border-[#7a5a23] bg-[#2e2418] px-3 text-[11px] font-semibold text-[#f5d0a8]">
          {reviewCandidates.length || reviewConflictCount} варианта
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-[0.85rem] border border-white/8 bg-black/10 px-3 py-2">
        <span
          className={classNames(
            "inline-flex min-h-7 items-center rounded-full px-2.5 text-[10px] font-semibold uppercase tracking-[0.12em]",
            showDeferredPass ? "bg-[#1d2026] text-[#d7dbe2]" : "bg-[#2e2418] text-[#f5d0a8]"
          )}
        >
          {reviewQueueTitle}
        </span>
        <p className="text-[12px] text-[#d9bf9b]">{reviewQueueHint}</p>
      </div>

      {hasNearTieAmbiguity && (
        <div className="rounded-[0.9rem] border border-[#7c3aed] bg-[#221933] px-3 py-2.5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#dcc8ff]">OCR split</p>
          <p className="mt-1 text-[12px] leading-5 text-[#efe5ff]">
            Один bbox содержит почти равный OCR-спор между двумя цифрами. Это не две реальные детали на листе, а два варианта чтения одного и того же места.
          </p>
        </div>
      )}

      {reviewTypeLabels.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {reviewTypeLabels.map((label) => (
            <span
              key={label}
              className="inline-flex min-h-7 items-center rounded-full border border-[#7a5a23] bg-[#2e2418] px-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8]"
            >
              {label}
            </span>
          ))}
          {hasConflictFocus && (
            <button
              type="button"
              className="inline-flex min-h-7 items-center rounded-full border border-white/10 bg-white/[0.05] px-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-white transition"
              onClick={onFocusConflict}
            >
              показать зону спора
            </button>
          )}
        </div>
      )}

      {reviewMessages.length > 0 && (
        <div className="space-y-1.5">
          {reviewMessages.map((message) => (
            <p key={message} className="text-[12px] leading-5 text-[#f0d7b5]">
              {message}
            </p>
          ))}
        </div>
      )}

      {reviewCandidates.length === 0 ? (
        <p className="text-sm text-[#d9bf9b]">
          Бэкенд отметил спор, но не прикрепил явные альтернативы. Открой зону спора и проверь точку через лупу.
        </p>
      ) : (
        <div className="space-y-2">
          {reviewCandidates.map((item) => (
            <AmbiguityReviewCandidateCard
              key={item.candidate.candidateId}
              item={item}
              onSelectCandidate={onSelectCandidate}
              onMoveMarker={onMoveMarker}
            />
          ))}
        </div>
      )}

      {showDeferredPass && (
        <div className="rounded-[0.85rem] border border-[#5a5f69] bg-[#1d2026] px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#d7dbe2]">
            Финальное решение
          </p>
          <p className="mt-1 text-[12px] text-[#aeb4be]">
            Эта точка уже была отложена раньше. В этом проходе skip больше не нужен: осталось либо подтвердить её, либо убрать как ложную.
          </p>
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        <button
          type="button"
          className="inline-flex h-9 w-full items-center justify-center rounded-[0.8rem] border border-white/10 bg-white/[0.05] px-3 text-[12px] font-semibold text-[#d7dbe2] transition disabled:cursor-not-allowed disabled:opacity-35"
          disabled={!canSkip || busy}
          onClick={onSkip}
        >
          {showDeferredPass ? "Пропуск не нужен" : "Пропустить"}
        </button>
        <button
          type="button"
          className="inline-flex h-9 w-full items-center justify-center rounded-[0.8rem] border border-[#7b2d2d] bg-[#331d1e] px-3 text-[12px] font-semibold text-[#ffcccc] transition disabled:cursor-not-allowed disabled:opacity-35"
          disabled={busy}
          onClick={onDelete}
        >
          Ложный и дальше
        </button>
        <button
          type="button"
          className="inline-flex h-9 w-full items-center justify-center rounded-[0.8rem] border border-[#3e5f2b] bg-[#1c2718] px-3 text-[12px] font-semibold text-[#d7f5c9] transition disabled:cursor-not-allowed disabled:opacity-35"
          disabled={!canConfirm || busy}
          onClick={onConfirm}
        >
          Подтвердить и дальше
        </button>
      </div>
      <p className="text-[11px] text-[#9fa6b2]">
        {showDeferredPass
          ? "Отложенный проход: ←/A и →/D переключают точки • Enter подтверждает • Delete помечает как ложную"
          : "←/A и →/D переключают спорные точки • S пропускает • Enter подтверждает • Delete помечает как ложную"}
      </p>
    </div>
  );
}

function CandidateReviewNotice({
  conflictCount,
  hasAssociations,
  candidateQueuePosition,
  totalPendingCandidates,
  currentPassCount,
  deferredCount,
  onOpenReview,
  onOpenDeferred
}: {
  conflictCount: number;
  hasAssociations: boolean;
  candidateQueuePosition: number | null;
  totalPendingCandidates: number;
  currentPassCount: number;
  deferredCount: number;
  onOpenReview: () => void;
  onOpenDeferred: () => void;
}) {
  const hasQueueActions = currentPassCount > 0 || deferredCount > 0;
  const explanation =
    conflictCount > 1
      ? `Сейчас открыт конфликтный кандидат: рядом спорят ${conflictCount} вариантов за одно место.`
      : hasAssociations
        ? "Сейчас открыт кандидат с явными shape↔text связями, поэтому сначала проще быстро проверить его здесь."
        : "Сейчас открыт кандидат для быстрой ручной проверки перед постановкой точки.";
  const queueHint =
    currentPassCount > 0
      ? "Спорные AI-точки не потерялись: к ним можно вернуться сразу после этого шага."
      : deferredCount > 0
        ? "Спорные AI-точки сейчас лежат в отложенной очереди и ждут возврата."
        : null;

  return (
    <div className="rounded-[1rem] border border-[#2b4b67] bg-[#162433] p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#bfe1ff]">Почему открыт этот режим</p>
          <p className="mt-1 text-sm text-[#dceeff]">{explanation}</p>
          {queueHint && <p className="mt-1 text-[12px] leading-5 text-[#9fc6e8]">{queueHint}</p>}
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {candidateQueuePosition !== null && totalPendingCandidates > 0 && (
            <span className="inline-flex min-h-8 items-center rounded-full border border-[#2b4b67] bg-[#10202e] px-3 text-[11px] font-semibold text-[#bfe1ff]">
              кандидат {candidateQueuePosition} из {totalPendingCandidates}
            </span>
          )}
          {(conflictCount > 1 || hasAssociations) && (
            <span className="inline-flex min-h-8 items-center rounded-full border border-[#2b4b67] bg-[#10202e] px-3 text-[11px] font-semibold text-[#bfe1ff]">
              {conflictCount > 1 ? `конфликт ${conflictCount}` : "есть связи"}
            </span>
          )}
        </div>
      </div>

      {hasQueueActions && (
        <div className="mt-3 flex flex-wrap gap-2">
          {currentPassCount > 0 && (
            <button
              type="button"
              className="inline-flex min-h-8 items-center rounded-full border border-[#7a5a23] bg-[#2e2418] px-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8] transition"
              onClick={onOpenReview}
            >
              к AI review {currentPassCount}
            </button>
          )}
          {deferredCount > 0 && (
            <button
              type="button"
              className="inline-flex min-h-8 items-center rounded-full border border-[#2b4b67] bg-[#10202e] px-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#bfe1ff] transition"
              onClick={onOpenDeferred}
            >
              к отложенным ai {deferredCount}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function MarkerRailFooter({
  sectionTitleClass,
  displayedCount,
  totalCount,
  showAmbiguityMarkersOnly,
  activeQueueLength,
  activeQueueLabel,
  totalAmbiguityCount,
  showDeferredAmbiguityMarkersOnly,
  skippedCount,
  currentPassCount,
  currentPosition,
  progress,
  filterMode,
  deferredCount,
  selectedIndex,
  reviewCompleted,
  hasDeferred,
  selectedMarkerLabel,
  hasSelectedAmbiguityReview,
  selectedReviewTooltip,
  selectedMarkerIsDraft,
  onSetMode,
  onFocusAmbiguityMarker
}: {
  sectionTitleClass: string;
  displayedCount: number;
  totalCount: number;
  showAmbiguityMarkersOnly: boolean;
  activeQueueLength: number;
  activeQueueLabel: string;
  totalAmbiguityCount: number;
  showDeferredAmbiguityMarkersOnly: boolean;
  skippedCount: number;
  currentPassCount: number;
  currentPosition: number;
  progress: number;
  filterMode: MarkerQueueMode;
  deferredCount: number;
  selectedIndex: number;
  reviewCompleted: boolean;
  hasDeferred: boolean;
  selectedMarkerLabel: string | null;
  hasSelectedAmbiguityReview: boolean;
  selectedReviewTooltip: string;
  selectedMarkerIsDraft: boolean;
  onSetMode: (mode: MarkerQueueMode) => void;
  onFocusAmbiguityMarker: (delta: -1 | 1) => void;
}) {
  return (
    <div className="flex items-center justify-between border-t border-white/8 px-4 py-3">
      <div>
        <p className={sectionTitleClass}>Точки</p>
        <p className="mt-1 text-sm text-[#c8ccd3]">
          {displayedCount}
          {showAmbiguityMarkersOnly ? ` из ${totalCount}` : ""} в списке
        </p>
        {activeQueueLength > 0 && (
          <div className="mt-2 space-y-1.5">
            <div className="flex items-center gap-2 text-[11px] text-[#aeb4be]">
              <span>
                {showAmbiguityMarkersOnly
                  ? `Очередь: ${activeQueueLabel} ${activeQueueLength}`
                  : `Всего спорных: ${totalAmbiguityCount}`}
              </span>
              {!showDeferredAmbiguityMarkersOnly && skippedCount > 0 && (
                <>
                  <span className="text-[#6f7681]">•</span>
                  <span>Отложено: {skippedCount}</span>
                </>
              )}
              {!showAmbiguityMarkersOnly && currentPassCount > 0 && (
                <>
                  <span className="text-[#6f7681]">•</span>
                  <span>В review: {currentPassCount}</span>
                </>
              )}
              {currentPosition > 0 && (
                <>
                  <span className="text-[#6f7681]">•</span>
                  <span>Сейчас: {currentPosition} из {activeQueueLength}</span>
                </>
              )}
            </div>
            <div className="h-1.5 w-32 overflow-hidden rounded-full bg-white/8">
              <div
                className="h-full rounded-full bg-[#f5d0a8] transition-[width] duration-200"
                style={{ width: currentPosition > 0 ? `${Math.max(8, Math.round(progress * 100))}%` : "0%" }}
              />
            </div>
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2">
        {(currentPassCount > 0 || deferredCount > 0) && (
          <>
            <div className="inline-flex items-center rounded-full border border-white/10 bg-white/5 p-1">
              <button
                type="button"
                className={classNames(
                  "inline-flex min-h-7 items-center rounded-full px-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] transition",
                  filterMode === "all" ? "bg-white/12 text-white" : "text-[#b8bec8]"
                )}
                onClick={() => onSetMode("all")}
              >
                все {totalAmbiguityCount}
              </button>
              <button
                type="button"
                className={classNames(
                  "inline-flex min-h-7 items-center rounded-full px-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] transition disabled:cursor-not-allowed disabled:opacity-35",
                  filterMode === "current" ? "bg-[#2e2418] text-[#f5d0a8]" : "text-[#d0d4db]"
                )}
                disabled={currentPassCount === 0}
                onClick={() => onSetMode("current")}
              >
                review {currentPassCount}
              </button>
              <button
                type="button"
                className={classNames(
                  "inline-flex min-h-7 items-center rounded-full px-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] transition disabled:cursor-not-allowed disabled:opacity-35",
                  filterMode === "deferred" ? "bg-[#1d2026] text-[#d7dbe2]" : "text-[#d0d4db]"
                )}
                disabled={deferredCount === 0}
                onClick={() => onSetMode("deferred")}
              >
                отложенные {deferredCount}
              </button>
            </div>
            {activeQueueLength > 0 && (
              <div className="inline-flex items-center rounded-full border border-white/10 bg-white/5 p-1">
                <button
                  type="button"
                  aria-label="Предыдущая спорная точка"
                  className="inline-flex h-6 w-6 items-center justify-center rounded-full text-sm text-white transition disabled:opacity-35"
                  disabled={activeQueueLength < 2}
                  onClick={() => onFocusAmbiguityMarker(-1)}
                >
                  ←
                </button>
                <span className="min-w-[2.75rem] text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-[#aeb4be]">
                  {selectedIndex >= 0 ? `${selectedIndex + 1}/${activeQueueLength}` : `0/${activeQueueLength}`}
                </span>
                <button
                  type="button"
                  aria-label="Следующая спорная точка"
                  className="inline-flex h-6 w-6 items-center justify-center rounded-full text-sm text-white transition disabled:opacity-35"
                  disabled={activeQueueLength < 2}
                  onClick={() => onFocusAmbiguityMarker(1)}
                >
                  →
                </button>
              </div>
            )}
          </>
        )}
        {currentPassCount === 0 && reviewCompleted && (
          <div
            className={classNames(
              "rounded-[0.85rem] px-3 py-2 text-right",
              hasDeferred ? "border border-[#5a5f69] bg-[#1d2026]" : "border border-[#3e5f2b] bg-[#1c2718]"
            )}
          >
            <p
              className={classNames(
                "text-[11px] font-semibold uppercase tracking-[0.12em]",
                hasDeferred ? "text-[#d7dbe2]" : "text-[#d7f5c9]"
              )}
            >
              {hasDeferred ? "Текущий проход завершён" : "Спорные точки закончились"}
            </p>
            <p className={classNames("mt-1 text-[11px]", hasDeferred ? "text-[#aeb4be]" : "text-[#b9d9ab]")}>
              {hasDeferred
                ? `Осталось ${deferredCount} отложенных точек. Можно сразу открыть отдельный проход по ним.`
                : "Режим review закрыт, список снова полный."}
            </p>
            {hasDeferred && (
              <div className="mt-2 flex justify-end">
                <button
                  type="button"
                  className="inline-flex min-h-8 items-center rounded-full border border-[#5a5f69] bg-white/5 px-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#d7dbe2] transition"
                  onClick={() => onSetMode("deferred")}
                >
                  открыть отложенные {deferredCount}
                </button>
              </div>
            )}
          </div>
        )}
        {hasDeferred && !reviewCompleted && (
          <div className="rounded-[0.85rem] border border-[#5a5f69] bg-[#1d2026] px-3 py-2 text-right">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#d7dbe2]">
              Отложено на потом
            </p>
            <p className="mt-1 text-[11px] text-[#aeb4be]">
              Осталось {deferredCount} точек. Кнопка `только отложенные` откроет отдельный проход.
            </p>
          </div>
        )}

        {selectedMarkerLabel && (
          <span className="inline-flex min-h-8 items-center rounded-full border border-white/10 bg-white/5 px-2.5 text-xs font-medium text-white">
            {selectedMarkerLabel}
          </span>
        )}
        {hasSelectedAmbiguityReview && (
          <span
            title={selectedReviewTooltip || "Есть спор по AI-разметке"}
            className="inline-flex min-h-8 items-center rounded-full border border-[#7a5a23] bg-[#2e2418] px-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8]"
          >
            AI review
          </span>
        )}
        {selectedMarkerIsDraft && (
          <span className="inline-flex min-h-8 items-center rounded-full border border-[#6d4a1a] bg-[#2a2118] px-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8]">
            черновик
          </span>
        )}
      </div>
    </div>
  );
}

function AmbiguityWorkspaceBanner({
  currentPassCount,
  deferredCount,
  reviewCompleted,
  onContinue,
  onOpenDeferred
}: {
  currentPassCount: number;
  deferredCount: number;
  reviewCompleted: boolean;
  onContinue: () => void;
  onOpenDeferred: () => void;
}) {
  const hasUnresolved = currentPassCount > 0 || deferredCount > 0;
  const primaryQueueContext = currentPassCount > 0 ? "review" : deferredCount > 0 ? "deferred" : null;

  if (!hasUnresolved && !reviewCompleted) {
    return null;
  }

  if (!hasUnresolved) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="mt-3 rounded-[1rem] border border-[#3e5f2b] bg-[#182018] px-3 py-3 text-white"
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#d7f5c9]">ambiguity-review завершён</p>
        <p className="mt-1 text-sm text-[#c6d8be]">Спорных AI-точек больше не осталось.</p>
      </div>
    );
  }

  const summaryText =
    currentPassCount > 0 && deferredCount > 0
      ? `Сейчас ждут решения ${currentPassCount} спорных точки, ещё ${deferredCount} отложено на отдельный проход.`
      : currentPassCount > 0
        ? `Сейчас ждут решения ${currentPassCount} спорных точки в активной очереди.`
        : `В активной очереди уже пусто. Осталось ${deferredCount} отложенных ambiguity-кейсов.`;

  return (
    <div
      role="status"
      aria-live="polite"
      className="mt-3 rounded-[1rem] border border-[#6d4a1a] bg-[#231d15] px-3 py-3 text-white"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8]">ambiguity-review</p>
          <p className="mt-1 text-sm text-[#f4e1c7]">{summaryText}</p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {currentPassCount > 0 && <HistoryQueueChip queueContext="review" count={currentPassCount} />}
            {deferredCount > 0 && <HistoryQueueChip queueContext="deferred" count={deferredCount} />}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          {primaryQueueContext && (
            <div className="inline-flex items-center gap-1.5 rounded-full border border-[#3e5f2b] bg-[#1c2718] px-1.5 py-1">
              <HistoryQueueChip queueContext={primaryQueueContext} />
              <button
                type="button"
                className="inline-flex min-h-8 items-center rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#d7f5c9] transition"
                onClick={onContinue}
              >
                {historyActionLabels.nextCase}
              </button>
            </div>
          )}
          {currentPassCount > 0 && deferredCount > 0 && (
            <button
              type="button"
              className="inline-flex min-h-7 items-center rounded-full border border-[#2b4b67] bg-[#162433] px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#bfe1ff] transition"
              onClick={onOpenDeferred}
            >
              {historyActionLabels.toDeferred} {deferredCount}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

type SnapCandidate = {
  x: number;
  y: number;
  score: number;
  source: "frame" | "merged" | "ink" | "region";
};

type MarkerConflict = {
  key: string;
  label: string;
  markers: Marker[];
  markerIds: string[];
  centerX: number;
  centerY: number;
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
};

type LocalAmbiguityHistoryEntry = {
  historyId: string;
  createdAt: string;
  actor: "human";
  decision: "skipped" | "restored";
  queueCount?: number;
  markerId: string;
  label: string | null;
  pointType: MarkerPointType;
  status: MarkerStatus;
  x: number;
  y: number;
};

type RenderedHistoryEntry = {
  id: string;
  actor: "human" | "ai" | "system";
  createdAt: string;
  markerId: string | null;
  ambiguityDecision: "confirmed" | "deleted" | "skipped" | "restored" | null;
  presentation: {
    title: string;
    details: string[];
  };
};

function passthroughImageLoader({ src }: ImageLoaderProps) {
  return src;
}

function buildMarkerConflicts(markers: Marker[]) {
  const byLabel = new Map<string, Marker[]>();
  for (const marker of markers) {
    const normalizedLabel = normalizeConflictLabel(marker.label);
    if (!normalizedLabel) {
      continue;
    }
    const bucket = byLabel.get(normalizedLabel) ?? [];
    bucket.push(marker);
    byLabel.set(normalizedLabel, bucket);
  }

  const conflicts: MarkerConflict[] = [];

  for (const [normalizedLabel, group] of byLabel.entries()) {
    if (group.length < 2) {
      continue;
    }

    const threshold = normalizedLabel.length > 2 ? 48 : 40;
    const visited = new Set<string>();

    for (const marker of group) {
      if (visited.has(marker.markerId)) {
        continue;
      }

      const stack = [marker];
      const cluster: Marker[] = [];

      while (stack.length > 0) {
        const current = stack.pop();
        if (!current || visited.has(current.markerId)) {
          continue;
        }

        visited.add(current.markerId);
        cluster.push(current);

        for (const candidate of group) {
          if (visited.has(candidate.markerId)) {
            continue;
          }

          const distance = Math.hypot(candidate.x - current.x, candidate.y - current.y);
          if (distance <= threshold) {
            stack.push(candidate);
          }
        }
      }

      if (cluster.length < 2) {
        continue;
      }

      const xs = cluster.map((item) => item.x);
      const ys = cluster.map((item) => item.y);
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);

      conflicts.push({
        key: `${normalizedLabel}-${cluster.map((item) => item.markerId).join("-")}`,
        label: cluster[0]?.label ?? normalizedLabel,
        markers: cluster,
        markerIds: cluster.map((item) => item.markerId),
        centerX: (minX + maxX) / 2,
        centerY: (minY + maxY) / 2,
        minX,
        minY,
        maxX,
        maxY
      });
    }
  }

  return conflicts.sort((left, right) => {
    if (left.label === right.label) {
      return left.minY - right.minY;
    }
    return left.label.localeCompare(right.label, "ru");
  });
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function formatCandidateConfidence(value: number | null | undefined) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return null;
  }

  return `${Math.round(value * 100)}%`;
}

function isEditableKeyboardTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false;
  }

  if (target.isContentEditable) {
    return true;
  }

  return Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
}

function getPayloadString(payload: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }

  return null;
}

function getPayloadNumber(payload: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }

  return null;
}

function formatHistoryMarkerRef(payload: Record<string, unknown>) {
  const label = getPayloadString(payload, "label");
  if (label) {
    return label;
  }

  const markerId = getPayloadString(payload, "markerId");
  if (markerId) {
    return `id ${markerId.slice(0, 8)}`;
  }

  return "без подписи";
}

function formatHistoryPosition(payload: Record<string, unknown>) {
  const x = getPayloadNumber(payload, "x");
  const y = getPayloadNumber(payload, "y");
  if (x === null || y === null) {
    return null;
  }

  return `X ${Math.round(x)} • Y ${Math.round(y)}`;
}

function formatHistoryMarkerMeta(payload: Record<string, unknown>) {
  const parts: string[] = [];
  const pointType = getPayloadString(payload, "pointType");
  const status = getPayloadString(payload, "status");
  const confidence = getPayloadNumber(payload, "confidence");

  if (pointType === "center" || pointType === "top_left") {
    parts.push(pointTypeLabels[pointType]);
  }
  if (status && status in markerStatusLabels) {
    parts.push(markerStatusLabels[status as MarkerStatus]);
  }

  const confidenceLabel = formatCandidateConfidence(confidence);
  if (confidenceLabel) {
    parts.push(confidenceLabel);
  }

  return parts;
}

function formatHistoryAction(entry: ActionLogEntry) {
  const payload = (entry.payload ?? {}) as Record<string, unknown>;
  const markerRef = formatHistoryMarkerRef(payload);
  const markerMeta = formatHistoryMarkerMeta(payload);
  const position = formatHistoryPosition(payload);

  switch (entry.type) {
    case "session_created": {
      const title = getPayloadString(payload, "title");
      return {
        title: "Сессия создана",
        details: title ? [`Новая сессия: ${title}`] : []
      };
    }
    case "document_uploaded": {
      const fileName = getPayloadString(payload, "file_name", "fileName");
      const width = getPayloadNumber(payload, "width");
      const height = getPayloadNumber(payload, "height");
      const parts = [fileName, width !== null && height !== null ? `${width}×${height}` : null].filter(Boolean);
      return {
        title: "Загружен документ",
        details: parts.length > 0 ? [parts.join(" • ")] : []
      };
    }
    case "candidates_detected": {
      const count = getPayloadNumber(payload, "count");
      return {
        title: "Найдены кандидаты",
        details: count !== null ? [`Найдено ${count} объектов`] : []
      };
    }
    case "auto_annotation_completed": {
      const importedFromJobId = getPayloadString(payload, "source_job_id", "sourceJobId");
      const candidateCount = getPayloadNumber(payload, "candidateCount");
      const autoAccepted = getPayloadNumber(payload, "autoAccepted");
      const autoReview = getPayloadNumber(payload, "autoReview");
      const pendingCandidates = getPayloadNumber(payload, "pendingCandidates");
      const parts = [
        candidateCount !== null ? `кандидатов ${candidateCount}` : null,
        autoAccepted !== null ? `сразу ${autoAccepted}` : null,
        autoReview !== null ? `на review ${autoReview}` : null,
        pendingCandidates !== null ? `в ожидании ${pendingCandidates}` : null
      ].filter(Boolean);
      return {
        title: importedFromJobId ? "Результат распознавания импортирован" : "Авторазметка завершена",
        details: parts.length > 0 ? [parts.join(" • ")] : []
      };
    }
    case "candidate_rejected": {
      const candidateId = getPayloadString(payload, "candidateId");
      return {
        title: "Кандидат отклонён",
        details: [candidateId ? `Убран из подбора: ${candidateId.slice(0, 8)}` : "Кандидат убран из подбора"]
      };
    }
    case "marker_created":
      return {
        title: "Точка добавлена",
        details: [
          [markerRef, ...markerMeta].join(" • "),
          ...(position ? [position] : [])
        ]
      };
    case "marker_moved":
      return {
        title: "Точка перемещена",
        details: [markerRef, ...(position ? [position] : [])]
      };
    case "marker_updated":
      return {
        title: "Точка изменена",
        details: [
          [markerRef, ...markerMeta].join(" • "),
          ...(position ? [position] : [])
        ]
      };
    case "marker_confirmed":
      return {
        title: "Точка подтверждена",
        details: [[markerRef, ...markerMeta].join(" • ")]
      };
    case "marker_rejected":
      return {
        title: "Точка помечена как ложная",
        details: [[markerRef, ...markerMeta].join(" • ")]
      };
    case "marker_deleted":
      return {
        title: "Точка удалена",
        details: [markerRef]
      };
    case "markers_cleared":
      return {
        title: "Все точки очищены",
        details: ["Список маркеров сброшен"]
      };
    default:
      return {
        title: entry.type.replace(/_/g, " "),
        details: Object.keys(payload).length > 0 ? [JSON.stringify(payload)] : []
      };
  }
}

function decorateHistoryAction(entry: ActionLogEntry, ambiguityDecision: "confirmed" | "deleted" | null) {
  const base = formatHistoryAction(entry);

  if (ambiguityDecision === "confirmed") {
    return {
      title: "Спорная AI-точка подтверждена",
      details: [
        ...base.details,
        "Решение принято человеком после ambiguity review."
      ]
    };
  }

  if (ambiguityDecision === "deleted") {
    return {
      title: "Спорная AI-точка удалена как ложная",
      details: [
        ...base.details,
        "Ложный AI-marker снят после ambiguity review."
      ]
    };
  }

  return base;
}

function classifyAmbiguityHistoryDecision(entry: ActionLogEntry, trackedMarkerIds: Set<string>) {
  const payload = (entry.payload ?? {}) as Record<string, unknown>;
  const markerId = typeof payload.markerId === "string" ? payload.markerId : null;
  const ambiguityTracked = markerId ? trackedMarkerIds.has(markerId) : false;

  if (ambiguityTracked && entry.type === "marker_confirmed") {
    return "confirmed" as const;
  }

  if (ambiguityTracked && entry.type === "marker_deleted") {
    return "deleted" as const;
  }

  return null;
}

function formatLocalAmbiguityHistoryEntry(entry: LocalAmbiguityHistoryEntry) {
  const markerDetails = [
    entry.label?.trim() ? entry.label.trim() : `id ${entry.markerId.slice(0, 8)}`,
    pointTypeLabels[entry.pointType],
    markerStatusLabels[entry.status]
  ].join(" • ");

  if (entry.decision === "restored") {
    return {
      title: entry.queueCount && entry.queueCount > 1 ? "Отложенные AI-точки возвращены в review" : "Спорная AI-точка возвращена в review",
      details: [
        markerDetails,
        `X ${Math.round(entry.x)} • Y ${Math.round(entry.y)}`,
        entry.queueCount && entry.queueCount > 1
          ? `Новый проход начат для ${entry.queueCount} отложенных точек.`
          : "К точке вернулись через отложенный проход."
      ]
    };
  }

  return {
    title: "Спорная AI-точка отложена",
    details: [markerDetails, `X ${Math.round(entry.x)} • Y ${Math.round(entry.y)}`, "Точка сознательно отложена на следующий проход."]
  };
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="h-5 w-5">
      <path
        d="M4 7h16m-11 0V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7m-8 0 1 11a1.5 1.5 0 0 0 1.5 1.36h5A1.5 1.5 0 0 0 16 18l1-11m-6 3.5v5m3-5v5"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="h-4 w-4">
      <path
        d="M7 7L17 17M17 7L7 17"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function CompactPointTypeSwitch({
  value,
  onChange
}: {
  value: MarkerPointType;
  onChange: (nextValue: MarkerPointType) => void;
}) {
  const options: Array<{ value: MarkerPointType; label: string; tone: string }> = [
    { value: "center", label: "Центр", tone: "bg-[#d92d20]" },
    { value: "top_left", label: "Угол", tone: "bg-[#16a34a]" }
  ];
  const activeIndex = options.findIndex((option) => option.value === value);

  return (
    <div className="relative grid grid-cols-2 rounded-full bg-[#1f1814] p-[3px] shadow-[inset_0_1px_0_rgba(255,255,255,0.04),0_0_0_1px_rgba(58,43,34,0.6)]">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute bottom-[3px] top-[3px] rounded-full bg-[#f5eee6] shadow-[0_12px_26px_rgba(12,10,8,0.32),inset_0_1px_0_rgba(255,255,255,0.7)] transition-transform duration-200 ease-out"
        style={{
          left: 3,
          width: "calc(50% - 3px)",
          transform: `translateX(${activeIndex <= 0 ? "0%" : "100%"})`
        }}
      />
      {options.map((option) => {
        const active = value === option.value;
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={classNames(
              "relative z-10 inline-flex min-h-10 items-center justify-center gap-2 rounded-full px-3.5 text-[13px] font-semibold tracking-[-0.01em] transition",
              active ? "text-[#15171b]" : "text-[#c4c7ce]"
            )}
            aria-pressed={active}
          >
            <span className={classNames("h-2.5 w-2.5 rounded-full", option.tone)} />
            <span>{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function AnnotationWorkspace({ sessionId }: { sessionId: string }) {
  const [session, setSession] = useState<AnnotationSession | null>(null);
  const [localViewport, setLocalViewport] = useState<Viewport | null>(null);
  const [selectedMarkerId, setSelectedMarkerId] = useState<string | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const [isInlineEditorOpen, setIsInlineEditorOpen] = useState(false);
  const [placementPointType, setPlacementPointType] = useState<MarkerPointType>("center");
  const [draftPointType, setDraftPointType] = useState<MarkerPointType>("center");
  const [draftLabel, setDraftLabel] = useState("");
  const [draftStatus, setDraftStatus] = useState<MarkerStatus>("human_draft");
  const [draftConfidence, setDraftConfidence] = useState("");
  const [ambiguityMarkerFilterMode, setAmbiguityMarkerFilterMode] = useState<"all" | "current" | "deferred">("all");
  const [ambiguityReviewCompleted, setAmbiguityReviewCompleted] = useState(false);
  const [draftMarkerX, setDraftMarkerX] = useState("");
  const [draftMarkerY, setDraftMarkerY] = useState("");
  const [draftViewportX, setDraftViewportX] = useState("");
  const [draftViewportY, setDraftViewportY] = useState("");
  const [draftViewportZoom, setDraftViewportZoom] = useState("");
  const [mode, setMode] = useState<"pan" | "place">("pan");
  const [isCtrlPressed, setIsCtrlPressed] = useState(false);
  const [isMiddlePanActive, setIsMiddlePanActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSessionLoading, setIsSessionLoading] = useState(true);
  const [isReloadingSession, setIsReloadingSession] = useState(false);
  const [busy, setBusy] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isAutoAnnotating, setIsAutoAnnotating] = useState(false);
  const [isSummaryOpen, setIsSummaryOpen] = useState(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [isMarkerRailOpen, setIsMarkerRailOpen] = useState(false);
  const [isInspectorOpen, setIsInspectorOpen] = useState(true);
  const [isHistoryHeaderCompact, setIsHistoryHeaderCompact] = useState(false);
  const [isHistoryHeaderCompactPinned, setIsHistoryHeaderCompactPinned] = useState(false);
  const [showOnlyAmbiguityHistory, setShowOnlyAmbiguityHistory] = useState(false);
  const [reviewedAmbiguityMarkerIds, setReviewedAmbiguityMarkerIds] = useState<string[]>([]);
  const [skippedAmbiguityMarkerIds, setSkippedAmbiguityMarkerIds] = useState<string[]>([]);
  const [localAmbiguityHistory, setLocalAmbiguityHistory] = useState<LocalAmbiguityHistoryEntry[]>([]);
  const [activeConflictIndex, setActiveConflictIndex] = useState(0);
  const [precisionZoomLevel, setPrecisionZoomLevel] = useState(5);
  const [, setImageDataVersion] = useState(0);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const inlineInputRef = useRef<HTMLInputElement | null>(null);
  const viewportRef = useRef<Viewport | null>(null);
  const viewportCommitRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedViewportRef = useRef<Viewport | null>(null);
  const suppressCanvasClickRef = useRef(false);
  const sourceImageDataRef = useRef<ImageData | null>(null);
  const autoAnnotateKeyRef = useRef<string | null>(null);
  const previousAmbiguityCountRef = useRef(0);
  const knownAmbiguityMarkerIdsRef = useRef<Set<string>>(new Set());
  const isWorkspaceBusy = busy || isAutoAnnotating || isReloadingSession || isSessionLoading;
  const ambiguityHotkeyHandlerRef = useRef<(event: KeyboardEvent) => void>(() => {});
  const runAutoAnnotateRef = useRef<((options?: { quiet?: boolean }) => Promise<AnnotationSession | null>) | null>(null);
  const sessionLoadRequestIdRef = useRef(0);
  const dragRef = useRef<{
    startX: number;
    startY: number;
    startCenterX: number;
    startCenterY: number;
    moved: boolean;
  } | null>(null);

  const document = session?.document ?? null;
  const importedJobEntry =
    session?.actionLog.find(
      (entry) =>
        entry.type === "auto_annotation_completed" &&
        getPayloadString((entry.payload ?? {}) as Record<string, unknown>, "source_job_id", "sourceJobId") != null
    ) ?? null;
  const importedJobSourceId =
    importedJobEntry == null
      ? null
      : getPayloadString((importedJobEntry.payload ?? {}) as Record<string, unknown>, "source_job_id", "sourceJobId");
  const isImportedJobPreviewSession = importedJobSourceId != null;
  const selectedMarker = session?.markers.find((item) => item.markerId === selectedMarkerId) ?? null;
  const pendingCandidates = (session?.candidates ?? []).filter((item) => item.reviewStatus === "pending");
  const selectedCandidate =
    (session?.candidates ?? []).find((item) => item.candidateId === selectedCandidateId) ?? null;
  const hasInspectorContentFocus = selectedMarker != null || selectedCandidate != null;
  const selectedCandidateQueueIndex =
    selectedCandidate == null ? -1 : pendingCandidates.findIndex((item) => item.candidateId === selectedCandidate.candidateId);
  const selectedCandidateQueuePosition = selectedCandidateQueueIndex >= 0 ? selectedCandidateQueueIndex + 1 : null;
  const selectedCandidateConflict =
    selectedCandidate?.conflictGroup
      ? pendingCandidates.filter((item) => item.conflictGroup === selectedCandidate.conflictGroup)
      : [];
  const pageVocabulary = session?.pageVocabulary ?? [];
  const candidateAssociations = session?.candidateAssociations ?? [];
  const missingLabels = session?.missingLabels ?? [];
  const pipelineConflicts = session?.pipelineConflicts ?? [];
  const candidatesById = new Map((session?.candidates ?? []).map((candidate) => [candidate.candidateId, candidate]));
  const associationsByTextCandidateId = new Map<string, CandidateAssociation[]>();
  const associationsByShapeCandidateId = new Map<string, CandidateAssociation[]>();
  for (const association of candidateAssociations) {
    const textBucket = associationsByTextCandidateId.get(association.textCandidateId) ?? [];
    textBucket.push(association);
    associationsByTextCandidateId.set(association.textCandidateId, textBucket);

    const shapeBucket = associationsByShapeCandidateId.get(association.shapeCandidateId) ?? [];
    shapeBucket.push(association);
    associationsByShapeCandidateId.set(association.shapeCandidateId, shapeBucket);
  }
  for (const bucket of associationsByTextCandidateId.values()) {
    bucket.sort((left, right) => right.score - left.score);
  }
  for (const bucket of associationsByShapeCandidateId.values()) {
    bucket.sort((left, right) => right.score - left.score);
  }
  const candidateAssociationCountById = new Map<string, number>();
  for (const [candidateId, members] of associationsByTextCandidateId.entries()) {
    candidateAssociationCountById.set(candidateId, members.length);
  }
  for (const [candidateId, members] of associationsByShapeCandidateId.entries()) {
    candidateAssociationCountById.set(candidateId, members.length);
  }
  const selectedCandidateAssociations =
    selectedCandidate == null
      ? []
      : (
          selectedCandidate.kind === "text"
            ? associationsByTextCandidateId.get(selectedCandidate.candidateId)
            : associationsByShapeCandidateId.get(selectedCandidate.candidateId)
        ) ?? [];
  const markerAmbiguityConflictsById = new Map<string, SessionPipelineConflict[]>();
  for (const conflict of pipelineConflicts) {
    if (conflict.type !== "association_ambiguity" && conflict.type !== "candidate_ambiguity") {
      continue;
    }

    for (const markerId of conflict.markerIds) {
      const bucket = markerAmbiguityConflictsById.get(markerId) ?? [];
      bucket.push(conflict);
      markerAmbiguityConflictsById.set(markerId, bucket);
    }
  }
  const {
    ambiguityReviewMarkers,
    showAmbiguityMarkersOnly,
    showDeferredAmbiguityMarkersOnly,
    deferredAmbiguityMarkers,
    currentPassAmbiguityMarkers,
    currentPassAmbiguityMarkerIds,
    deferredAmbiguityMarkerIds,
    activeAmbiguityQueueMarkers,
    activeAmbiguityQueueLabel,
    hasDeferredAmbiguityMarkers,
    selectedMarkerAmbiguityConflicts,
    selectedAmbiguityReviewCandidates,
    selectedAmbiguityReviewMessages,
    selectedAmbiguityReviewTypeLabels,
    hasSelectedAmbiguityReview,
    canConfirmSelectedAmbiguityReview,
    canSkipSelectedAmbiguityReview,
    firstAmbiguityMarkerId,
    displayedMarkers,
    selectedAmbiguityMarkerIndex,
    ambiguityReviewCurrentPosition,
    ambiguityReviewProgress,
    selectedAmbiguityQueueTitle,
    selectedAmbiguityQueueHint,
    hasUnresolvedAmbiguityMarkers
  } = buildAmbiguityReviewState({
    sessionMarkers: session?.markers ?? [],
    markerAmbiguityConflictsById,
    reviewedAmbiguityMarkerIds,
    skippedAmbiguityMarkerIds,
    ambiguityMarkerFilterMode,
    selectedMarker,
    pendingCandidates,
    candidatesById,
    candidateAssociationCountById,
    selectedMarkerId,
    draftLabel
  });
  const selectedMarkerHasNearTieAmbiguity = selectedMarkerAmbiguityConflicts.some((conflict) => hasNearTieAmbiguity(conflict.message));
  const historyAlternateQueueAction = buildHistoryAlternateQueueAction({
    showDeferredAmbiguityMarkersOnly,
    currentPassCount: currentPassAmbiguityMarkers.length,
    deferredCount: deferredAmbiguityMarkers.length,
    toReviewLabel: historyActionLabels.toReview,
    toDeferredLabel: historyActionLabels.toDeferred
  });
  const isHistoryHeaderCompactActive = isHistoryHeaderCompact || isHistoryHeaderCompactPinned;
  for (const marker of ambiguityReviewMarkers) {
    knownAmbiguityMarkerIdsRef.current.add(marker.markerId);
  }
  ambiguityHotkeyHandlerRef.current = (event: KeyboardEvent) => {
    if (!hasSelectedAmbiguityReview || isWorkspaceBusy) {
      return;
    }

    if (event.ctrlKey || event.metaKey || event.altKey) {
      return;
    }

    if (isEditableKeyboardTarget(event.target)) {
      return;
    }

    const normalizedKey = event.key.toLowerCase();

    if (event.key === "ArrowLeft" || normalizedKey === "a") {
      event.preventDefault();
      focusAmbiguityMarker(-1);
      return;
    }

    if (event.key === "ArrowRight" || normalizedKey === "d") {
      event.preventDefault();
      focusAmbiguityMarker(1);
      return;
    }

    if (event.key === "Enter") {
      if (!canConfirmSelectedAmbiguityReview || event.repeat) {
        return;
      }

      event.preventDefault();
      void confirmSelectedMarkerAndAdvance();
      return;
    }

    if (normalizedKey === "s") {
      if (!canSkipSelectedAmbiguityReview || event.repeat) {
        return;
      }

      event.preventDefault();
      void skipSelectedMarkerAndAdvance();
      return;
    }

    if (event.key === "Delete" || event.key === "Backspace") {
      if (event.repeat) {
        return;
      }

      event.preventDefault();
      void deleteSelectedMarkerAndAdvance();
    }
  };
  const associationConflictCount = pipelineConflicts.filter((conflict) => conflict.type === "association_ambiguity").length;
  const markerConflicts = buildMarkerConflicts(session?.markers ?? []);
  const markerConflictById = new Map<string, MarkerConflict>();
  for (const conflict of markerConflicts) {
    for (const markerId of conflict.markerIds) {
      markerConflictById.set(markerId, conflict);
    }
  }
  const selectedConflict = selectedMarkerId ? markerConflictById.get(selectedMarkerId) ?? null : null;
  const activeConflict = selectedConflict ?? markerConflicts[activeConflictIndex] ?? null;
  const measuredWidth = containerSize.width || Math.round(containerRef.current?.getBoundingClientRect().width ?? 0);
  const measuredHeight = containerSize.height || Math.round(containerRef.current?.getBoundingClientRect().height ?? 0);
  const desktopPreferredLeftRailWidth = measuredWidth >= 1700 ? 232 : measuredWidth >= 1400 ? 216 : 196;
  const desktopPreferredRightRailWidth = measuredWidth >= 1700 ? 276 : measuredWidth >= 1400 ? 256 : 236;
  const desktopRailGap = measuredWidth >= 1400 ? 14 : measuredWidth >= 900 ? 10 : 8;
  const desktopMinCanvasWidth = measuredWidth >= 1400 ? 320 : measuredWidth >= 900 ? 280 : 220;
  const desktopMinLeftRailWidth = measuredWidth >= 900 ? 180 : 144;
  const desktopMinRightRailWidth = measuredWidth >= 900 ? 208 : 156;
  const desktopMaxRailBudget = Math.max(
    measuredWidth - desktopMinCanvasWidth - desktopRailGap * 2,
    desktopMinLeftRailWidth + desktopMinRightRailWidth
  );
  const desktopPreferredRailBudget = desktopPreferredLeftRailWidth + desktopPreferredRightRailWidth;
  const desktopRailCompressionRatio =
    desktopPreferredRailBudget > 0 ? Math.min(1, desktopMaxRailBudget / desktopPreferredRailBudget) : 1;
  const desktopLeftRailWidth = Math.max(
    desktopMinLeftRailWidth,
    Math.min(desktopPreferredLeftRailWidth, Math.round(desktopPreferredLeftRailWidth * desktopRailCompressionRatio))
  );
  const desktopRightRailWidth = Math.max(
    desktopMinRightRailWidth,
    Math.min(
      desktopPreferredRightRailWidth,
      Math.max(desktopMaxRailBudget - desktopLeftRailWidth, Math.round(desktopPreferredRightRailWidth * desktopRailCompressionRatio))
    )
  );
  const desktopCanvasStageWidth = Math.max(
    measuredWidth - (desktopLeftRailWidth + desktopRailGap) - (desktopRightRailWidth + desktopRailGap),
    desktopMinCanvasWidth
  );
  const isCompactWorkspace = measuredWidth > 0 && desktopCanvasStageWidth < 500;
  const isUltraCompactWorkspace = measuredWidth > 0 && desktopCanvasStageWidth < 360;
  const preferredLeftRailWidth = isUltraCompactWorkspace
    ? 188
    : isCompactWorkspace
      ? 212
      : desktopPreferredLeftRailWidth;
  const preferredRightRailWidth = isCompactWorkspace ? 0 : desktopPreferredRightRailWidth;
  const railGap = desktopRailGap;
  const minCanvasWidth = isUltraCompactWorkspace ? 260 : isCompactWorkspace ? 320 : desktopMinCanvasWidth;
  const minLeftRailWidth = isUltraCompactWorkspace ? 164 : isCompactWorkspace ? 184 : desktopMinLeftRailWidth;
  const minRightRailWidth = isCompactWorkspace ? 0 : desktopMinRightRailWidth;
  const maxRailBudget = Math.max(
    measuredWidth - minCanvasWidth - railGap * 2,
    minLeftRailWidth + minRightRailWidth
  );
  const preferredRailBudget = preferredLeftRailWidth + preferredRightRailWidth;
  const railCompressionRatio =
    preferredRailBudget > 0 ? Math.min(1, maxRailBudget / preferredRailBudget) : 1;
  const leftRailWidth = Math.max(
    minLeftRailWidth,
    Math.min(preferredLeftRailWidth, Math.round(preferredLeftRailWidth * railCompressionRatio))
  );
  const rightRailWidth = Math.max(
    minRightRailWidth,
    Math.min(
      preferredRightRailWidth,
      Math.max(maxRailBudget - leftRailWidth, Math.round(preferredRightRailWidth * railCompressionRatio))
    )
  );
  const canvasLeftInset = (isCompactWorkspace ? 0 : leftRailWidth) + railGap;
  const canvasRightInset = (isCompactWorkspace ? 0 : rightRailWidth) + railGap;
  const canvasBottomInset = 84;
  const canvasStageWidth = Math.max(measuredWidth - canvasLeftInset - canvasRightInset, minCanvasWidth);
  const canvasStageHeight = Math.max(measuredHeight - railGap * 2 - canvasBottomInset, 240);
  const canvasCenterX = canvasLeftInset + canvasStageWidth / 2;
  const canvasCenterY = railGap + canvasStageHeight / 2;
  const fitScale =
    document && measuredWidth > 0 && measuredHeight > 0
      ? Math.min((canvasStageWidth * 0.94) / document.width, (canvasStageHeight * 0.94) / document.height)
      : 1;
  const scale = fitScale * (localViewport?.zoom ?? 1);
  const partialInverseScale = scale > 0 ? 1 / Math.sqrt(scale) : 1;
  const translateX = document && localViewport ? canvasCenterX - localViewport.centerX * scale : 0;
  const translateY = document && localViewport ? canvasCenterY - localViewport.centerY * scale : 0;
  const isPanInteractionActive = mode === "pan" || isCtrlPressed || isMiddlePanActive;
  const inlineEditorPosition =
    isInlineEditorOpen && selectedMarker && measuredWidth > 0 && measuredHeight > 0
      ? {
          left: Math.min(
            Math.max(translateX + selectedMarker.x * scale + 18, canvasLeftInset + 12),
            Math.max(measuredWidth - canvasRightInset - 188, canvasLeftInset + 12)
          ),
          top: Math.min(
            Math.max(translateY + selectedMarker.y * scale - 56, railGap + 8),
            Math.max(measuredHeight - canvasBottomInset - 20, railGap + 8)
          )
        }
      : null;
  const precisionLensSize = Math.min(Math.max((isCompactWorkspace ? 236 : rightRailWidth) - 28, 184), 228);
  const floatingMarkerRailWidth = Math.min(
    Math.max(measuredWidth - (isUltraCompactWorkspace ? 20 : 28), isUltraCompactWorkspace ? 252 : 308),
    isUltraCompactWorkspace ? 332 : 372
  );
  const floatingInspectorWidth = Math.min(
    Math.max(measuredWidth - (isUltraCompactWorkspace ? 20 : 28), isUltraCompactWorkspace ? 228 : 268),
    isUltraCompactWorkspace ? 300 : 344
  );
  const floatingOverlayWidth = Math.min(
    Math.max(measuredWidth - (isUltraCompactWorkspace ? 20 : 28), isUltraCompactWorkspace ? 260 : 304),
    392
  );
  const inspectorToggleLabel = selectedCandidate ? "Кандидат" : selectedMarker ? "Точка" : "Инспектор";
  const defaultPrecisionZoom = 5;
  const precisionZoom = precisionZoomLevel || defaultPrecisionZoom;
  const precisionBackgroundSize =
    document ? `${document.width * precisionZoom}px ${document.height * precisionZoom}px` : undefined;
  const precisionBackgroundPosition =
    document && selectedMarker
      ? `${precisionLensSize / 2 - selectedMarker.x * precisionZoom}px ${precisionLensSize / 2 - selectedMarker.y * precisionZoom}px`
      : undefined;
  const precisionCandidates = selectedMarker
    ? collectSnapCandidates({ x: selectedMarker.x, y: selectedMarker.y }, selectedMarker.pointType ?? "center").candidates
        .filter((candidate) => Math.hypot(candidate.x - selectedMarker.x, candidate.y - selectedMarker.y) >= 1)
        .slice(0, 3)
    : [];

  function defaultViewportForSession(nextSession: AnnotationSession): Viewport | null {
    if (!nextSession.document) {
      return null;
    }

    return {
      centerX: nextSession.document.width / 2,
      centerY: nextSession.document.height / 2,
      zoom: 1
    };
  }

  function formatZoomValue(value: number) {
    return value.toFixed(2).replace(/\.00$/, "").replace(/(\.\d)0$/, "$1");
  }

  function viewportMatches(left: Viewport | null, right: Viewport | null) {
    if (!left || !right) {
      return false;
    }

    return (
      Math.abs(left.centerX - right.centerX) < 0.5 &&
      Math.abs(left.centerY - right.centerY) < 0.5 &&
      Math.abs(left.zoom - right.zoom) < 0.01
    );
  }

  function syncSession(nextSession: AnnotationSession, options?: { preferFit?: boolean; preserveLocalViewport?: boolean }) {
    setSession(nextSession);
    const shouldPreserveViewport = options?.preserveLocalViewport && viewportRef.current;
    const nextViewport =
      options?.preferFit && nextSession.markers.length === 0
        ? defaultViewportForSession(nextSession) ?? nextSession.viewport
        : shouldPreserveViewport
          ? (viewportRef.current ?? nextSession.viewport)
          : nextSession.viewport;
    setLocalViewport(nextViewport);
    viewportRef.current = nextViewport;
    if (!shouldPreserveViewport) {
      lastSavedViewportRef.current = nextSession.viewport;
      if (viewportCommitRef.current) {
        clearTimeout(viewportCommitRef.current);
        viewportCommitRef.current = null;
      }
    }

    const shouldKeepCandidateReviewContext =
      selectedMarkerId == null &&
      selectedCandidateId != null &&
      nextSession.candidates.some((item) => item.reviewStatus === "pending");

    if (!shouldKeepCandidateReviewContext && !nextSession.markers.some((item) => item.markerId === selectedMarkerId)) {
      setSelectedMarkerId(nextSession.markers[0]?.markerId ?? null);
      setIsInlineEditorOpen(false);
    }

    if (!nextSession.candidates.some((item) => item.candidateId === selectedCandidateId)) {
      setSelectedCandidateId(nextSession.candidates.find((item) => item.reviewStatus === "pending")?.candidateId ?? null);
    }
  }

  function updateLocalViewport(nextViewport: Viewport) {
    setLocalViewport(nextViewport);
    viewportRef.current = nextViewport;
  }

  function selectMarker(markerId: string, options?: { openInline?: boolean; focus?: boolean }) {
    const marker = session?.markers.find((item) => item.markerId === markerId);
    setSelectedMarkerId(markerId);
    setSelectedCandidateId(null);
    setIsInlineEditorOpen(Boolean(options?.openInline));
    const selectedConflictIndex = markerConflicts.findIndex((conflict) => conflict.markerIds.includes(markerId));
    if (selectedConflictIndex >= 0) {
      setActiveConflictIndex(selectedConflictIndex);
    }

    if (!marker || !options?.focus || !localViewport) {
      return;
    }

    const nextViewport = {
      ...localViewport,
      centerX: marker.x,
      centerY: marker.y
    };
    updateLocalViewport(nextViewport);
    scheduleViewportCommit(nextViewport, 500);
  }

  function selectCandidate(candidateId: string, options?: { focus?: boolean }) {
    const candidate = session?.candidates.find((item) => item.candidateId === candidateId) ?? null;
    setSelectedCandidateId(candidateId);
    setSelectedMarkerId(null);
    setIsInlineEditorOpen(false);

    if (!candidate || !options?.focus || !localViewport || !document) {
      return;
    }

    const paddedWidth = Math.max(candidate.bboxWidth * 2.4, 64);
    const paddedHeight = Math.max(candidate.bboxHeight * 2.4, 64);
    const nextViewport = {
      centerX: candidate.centerX,
      centerY: candidate.centerY,
      zoom: clamp(Math.min(document.width / paddedWidth, document.height / paddedHeight), 0.5, 12)
    };
    updateLocalViewport(nextViewport);
    scheduleViewportCommit(nextViewport, 400);
  }

  function jumpToHistoryMarker(markerId: string | null) {
    if (!markerId) {
      return;
    }

    setIsHistoryOpen(false);

    if (currentPassAmbiguityMarkerIds.has(markerId)) {
      setAmbiguityMarkerFilterMode("current");
    } else if (deferredAmbiguityMarkerIds.has(markerId)) {
      setAmbiguityMarkerFilterMode("deferred");
    } else {
      setAmbiguityMarkerFilterMode("all");
    }

    selectMarker(markerId, { focus: true });
  }

  function openAmbiguityQueueFromHistory(nextMode: "current" | "deferred") {
    setIsHistoryOpen(false);
    setAmbiguityQueueMode(nextMode, { focusFirst: true });
  }

  function continueAmbiguityReviewFromHistory() {
    if (currentPassAmbiguityMarkers.length > 0) {
      openAmbiguityQueueFromHistory("current");
      return;
    }

    if (deferredAmbiguityMarkers.length > 0) {
      openAmbiguityQueueFromHistory("deferred");
    }
  }

  function focusAmbiguityMarker(step: 1 | -1) {
    if (activeAmbiguityQueueMarkers.length === 0) {
      return;
    }

    const currentIndex =
      selectedMarkerId == null ? -1 : activeAmbiguityQueueMarkers.findIndex((marker) => marker.markerId === selectedMarkerId);
    const nextIndex =
      currentIndex >= 0
        ? (currentIndex + step + activeAmbiguityQueueMarkers.length) % activeAmbiguityQueueMarkers.length
        : step > 0
          ? 0
          : activeAmbiguityQueueMarkers.length - 1;

    selectMarker(activeAmbiguityQueueMarkers[nextIndex].markerId, { focus: true });
  }

  function setAmbiguityQueueMode(nextMode: "all" | "current" | "deferred", options?: { focusFirst?: boolean }) {
    if (nextMode === ambiguityMarkerFilterMode) {
      if (options?.focusFirst) {
        const firstMarker =
          nextMode === "current"
            ? currentPassAmbiguityMarkers[0]
            : nextMode === "deferred"
              ? deferredAmbiguityMarkers[0]
              : null;
        if (firstMarker) {
          selectMarker(firstMarker.markerId, { focus: true });
        }
      }
      return;
    }

    if (nextMode === "current" && currentPassAmbiguityMarkers.length === 0) {
      return;
    }

    if (nextMode === "deferred" && deferredAmbiguityMarkers.length === 0) {
      return;
    }

    if (nextMode === "deferred") {
      const restoredMarker = deferredAmbiguityMarkers[0];
      if (restoredMarker) {
        const restoredEntry: LocalAmbiguityHistoryEntry = {
          historyId: `local-restore-${restoredMarker.markerId}-${Date.now()}`,
          createdAt: new Date().toISOString(),
          actor: "human",
          decision: "restored",
          queueCount: deferredAmbiguityMarkers.length,
          markerId: restoredMarker.markerId,
          label: restoredMarker.label,
          pointType: restoredMarker.pointType,
          status: restoredMarker.status,
          x: restoredMarker.x,
          y: restoredMarker.y
        };
        setLocalAmbiguityHistory((current) => [restoredEntry, ...current].slice(0, 24));
      }
    }

    setAmbiguityMarkerFilterMode(nextMode);

    if (nextMode === "current" && (options?.focusFirst || !currentPassAmbiguityMarkerIds.has(selectedMarkerId ?? ""))) {
      selectMarker(currentPassAmbiguityMarkers[0].markerId, { focus: true });
      return;
    }

    if (nextMode === "deferred" && (options?.focusFirst || !deferredAmbiguityMarkerIds.has(selectedMarkerId ?? ""))) {
      selectMarker(deferredAmbiguityMarkers[0].markerId, { focus: true });
    }
  }

  useEffect(() => {
    if (!showAmbiguityMarkersOnly) {
      return;
    }

    if (!firstAmbiguityMarkerId) {
      setAmbiguityMarkerFilterMode("all");
      return;
    }

    if (selectedAmbiguityMarkerIndex < 0) {
      setSelectedMarkerId(firstAmbiguityMarkerId);
      setSelectedCandidateId(null);
      setIsInlineEditorOpen(false);
    }
  }, [firstAmbiguityMarkerId, selectedAmbiguityMarkerIndex, showAmbiguityMarkersOnly]);

  useEffect(() => {
    const previousCount = previousAmbiguityCountRef.current;

    if (previousCount > 0 && currentPassAmbiguityMarkers.length === 0) {
      setAmbiguityReviewCompleted(true);
    } else if (currentPassAmbiguityMarkers.length > 0) {
      setAmbiguityReviewCompleted(false);
    }

    previousAmbiguityCountRef.current = currentPassAmbiguityMarkers.length;
  }, [currentPassAmbiguityMarkers.length]);

  useEffect(() => {
    const activeAmbiguityMarkerIds = new Set(ambiguityReviewMarkers.map((marker) => marker.markerId));
    setReviewedAmbiguityMarkerIds((current) => {
      const next = current.filter((markerId) => activeAmbiguityMarkerIds.has(markerId));
      return next.length === current.length ? current : next;
    });
    setSkippedAmbiguityMarkerIds((current) => {
      const next = current.filter((markerId) => activeAmbiguityMarkerIds.has(markerId));
      return next.length === current.length ? current : next;
    });
  }, [ambiguityReviewMarkers]);

  useEffect(() => {
    if (!ambiguityReviewCompleted || hasDeferredAmbiguityMarkers) {
      return;
    }

    setIsSummaryOpen(false);
    setShowOnlyAmbiguityHistory(true);
    setIsHistoryOpen(true);

    return undefined;
  }, [ambiguityReviewCompleted, hasDeferredAmbiguityMarkers]);

  useEffect(() => {
    if (!isHistoryOpen) {
      setIsHistoryHeaderCompact(false);
      setIsHistoryHeaderCompactPinned(false);
    }
  }, [isHistoryOpen]);

  useEffect(() => {
    if (!ambiguityReviewCompleted) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setAmbiguityReviewCompleted(false);
    }, 5000);

    return () => window.clearTimeout(timeoutId);
  }, [ambiguityReviewCompleted]);

  useEffect(() => {
    function handleAmbiguityReviewHotkey(event: KeyboardEvent) {
      ambiguityHotkeyHandlerRef.current(event);
    }

    window.addEventListener("keydown", handleAmbiguityReviewHotkey);
    return () => {
      window.removeEventListener("keydown", handleAmbiguityReviewHotkey);
    };
  }, []);

  async function runCommand(
    payload: Parameters<typeof applySessionCommand>[1],
    options?: { preserveLocalViewport?: boolean; quiet?: boolean }
  ) {
    if (isWorkspaceBusy) {
      return null;
    }

    if (!options?.quiet) {
      setBusy(true);
    }
    setError(null);
    try {
      const response = await applySessionCommand(sessionId, payload);
      syncSession(response.session, { preserveLocalViewport: options?.preserveLocalViewport });
      return response.session;
    } catch (commandError) {
      setError(commandError instanceof Error ? commandError.message : "Command failed.");
    } finally {
      if (!options?.quiet) {
        setBusy(false);
      }
    }

    return null;
  }

  async function runAutoAnnotate(options?: { quiet?: boolean }) {
    if (!document) {
      return null;
    }

    if (isAutoAnnotating || isReloadingSession || isSessionLoading) {
      return null;
    }

    if (!options?.quiet) {
      setBusy(true);
    }
    setIsAutoAnnotating(true);
    setError(null);

    try {
      const response = await autoAnnotateSession(sessionId);
      syncSession(response.session, { preserveLocalViewport: true });
      setSelectedCandidateId(response.session.candidates.find((item) => item.reviewStatus === "pending")?.candidateId ?? null);
      return response.session;
    } catch (candidateError) {
      setError(candidateError instanceof Error ? candidateError.message : "Не удалось запустить авторазметку.");
      return null;
    } finally {
      setIsAutoAnnotating(false);
      if (!options?.quiet) {
        setBusy(false);
      }
    }
  }

  runAutoAnnotateRef.current = runAutoAnnotate;

  async function rejectCandidate(candidateId: string) {
    if (isWorkspaceBusy) {
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const response = await rejectSessionCandidate(sessionId, candidateId);
      syncSession(response.session, { preserveLocalViewport: true });
      const nextPending = response.session.candidates.find((item) => item.reviewStatus === "pending");
      setSelectedCandidateId(nextPending?.candidateId ?? null);
    } catch (candidateError) {
      setError(candidateError instanceof Error ? candidateError.message : "Не удалось убрать кандидата.");
    } finally {
      setBusy(false);
    }
  }

  async function commitViewport(nextViewport: Viewport) {
    if (viewportMatches(nextViewport, lastSavedViewportRef.current)) {
      return;
    }

    const nextSession = await runCommand(
      {
        type: "set_viewport",
        actor: "human",
        centerX: nextViewport.centerX,
        centerY: nextViewport.centerY,
        zoom: nextViewport.zoom
      },
      { preserveLocalViewport: true, quiet: true }
    );

    if (nextSession) {
      lastSavedViewportRef.current = nextViewport;
    }
  }

  function scheduleViewportCommit(nextViewport: Viewport, delay = 700) {
    if (viewportCommitRef.current) {
      clearTimeout(viewportCommitRef.current);
    }

    viewportCommitRef.current = setTimeout(() => {
      viewportCommitRef.current = null;
      const latestViewport = viewportRef.current ?? nextViewport;
      void commitViewport(latestViewport);
    }, delay);
  }

  async function reloadCurrentSession(options?: {
    preferFit?: boolean;
    preserveLocalViewport?: boolean;
    manual?: boolean;
  }) {
    const requestId = ++sessionLoadRequestIdRef.current;
    const isManual = Boolean(options?.manual);

    if (isManual) {
      setIsReloadingSession(true);
    } else {
      setIsSessionLoading(true);
    }
    setError(null);

    try {
      const nextSession = await refreshSession(sessionId);

      if (requestId !== sessionLoadRequestIdRef.current) {
        return null;
      }

      syncSession(nextSession, {
        preferFit: options?.preferFit,
        preserveLocalViewport: options?.preserveLocalViewport
      });
      return nextSession;
    } catch (loadError) {
      if (requestId === sessionLoadRequestIdRef.current) {
        setError(loadError instanceof Error ? loadError.message : "Не удалось открыть сессию.");
      }
      return null;
    } finally {
      if (requestId === sessionLoadRequestIdRef.current) {
        if (isManual) {
          setIsReloadingSession(false);
        } else {
          setIsSessionLoading(false);
        }
      }
    }
  }

  useEffect(() => {
    void reloadCurrentSession({ preferFit: true });
    return () => {
      sessionLoadRequestIdRef.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (!session?.document) {
      autoAnnotateKeyRef.current = null;
      return;
    }

    const detectionKey = `${session.sessionId}:${session.document.documentId}`;
    if (session.candidates.length > 0 || session.markers.length > 0) {
      autoAnnotateKeyRef.current = detectionKey;
      return;
    }

    if (autoAnnotateKeyRef.current === detectionKey) {
      return;
    }

    autoAnnotateKeyRef.current = detectionKey;
    void runAutoAnnotateRef.current?.({ quiet: true });
  }, [session]);

  useEffect(() => {
    if (markerConflicts.length === 0) {
      if (activeConflictIndex !== 0) {
        setActiveConflictIndex(0);
      }
      return;
    }

    if (activeConflictIndex > markerConflicts.length - 1) {
      setActiveConflictIndex(0);
    }
  }, [activeConflictIndex, markerConflicts.length]);

  useEffect(() => {
    sourceImageDataRef.current = null;
    setImageDataVersion((current) => current + 1);

    if (!document) {
      return;
    }

    let cancelled = false;
    const image = new window.Image();
    image.decoding = "async";
    image.crossOrigin = "anonymous";
    image.onload = () => {
      if (cancelled) {
        return;
      }

      const canvas = window.document.createElement("canvas");
      canvas.width = document.width;
      canvas.height = document.height;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) {
        return;
      }

      context.drawImage(image, 0, 0, document.width, document.height);
      sourceImageDataRef.current = context.getImageData(0, 0, document.width, document.height);
      setImageDataVersion((current) => current + 1);
    };
    image.onerror = () => {
      sourceImageDataRef.current = null;
      setImageDataVersion((current) => current + 1);
    };
    image.src = resolveAssetUrl(document.storageUrl);

    return () => {
      cancelled = true;
    };
  }, [document]);

  useLayoutEffect(() => {
    if (!containerRef.current) {
      return;
    }

    const node = containerRef.current;
    let frameId = 0;

    const measure = () => {
      const bounds = node.getBoundingClientRect();
      const width = Math.round(bounds.width);
      const height = Math.round(bounds.height);

      if (width > 0 && height > 0) {
        setContainerSize({ width, height });
        return;
      }

      frameId = window.requestAnimationFrame(measure);
    };

    measure();

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      setContainerSize({
        width: Math.round(entry.contentRect.width),
        height: Math.round(entry.contentRect.height)
      });
    });
    observer.observe(node);
    return () => {
      observer.disconnect();
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
    };
  }, [session?.sessionId]);

  useEffect(() => {
    if (!isCompactWorkspace) {
      setIsMarkerRailOpen(true);
      return;
    }

    if (selectedMarkerId == null && selectedCandidateId == null) {
      setIsMarkerRailOpen(false);
    }
  }, [isCompactWorkspace, selectedCandidateId, selectedMarkerId]);

  useEffect(() => {
    if (!isCompactWorkspace) {
      setIsInspectorOpen(true);
      return;
    }

    if (selectedMarkerId != null || selectedCandidateId != null) {
      setIsMarkerRailOpen(false);
      setIsInspectorOpen(true);
      return;
    }

    setIsInspectorOpen(false);
  }, [isCompactWorkspace, selectedCandidateId, selectedMarkerId]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Control") {
        setIsCtrlPressed(true);
      }
    }

    function handleKeyUp(event: KeyboardEvent) {
      if (event.key === "Control") {
        setIsCtrlPressed(false);
      }
    }

    function handleBlur() {
      setIsCtrlPressed(false);
      setIsMiddlePanActive(false);
    }

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    window.addEventListener("blur", handleBlur);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
      window.removeEventListener("blur", handleBlur);
    };
  }, []);

  useEffect(() => {
    function handlePointerMove(event: PointerEvent) {
      if (!dragRef.current || !session?.document || !viewportRef.current) {
        return;
      }

      const currentScale = fitScale * (viewportRef.current.zoom || 1);
      if (!currentScale) {
        return;
      }

      const dx = event.clientX - dragRef.current.startX;
      const dy = event.clientY - dragRef.current.startY;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
        dragRef.current.moved = true;
      }

      updateLocalViewport({
        centerX: dragRef.current.startCenterX - dx / currentScale,
        centerY: dragRef.current.startCenterY - dy / currentScale,
        zoom: viewportRef.current.zoom
      });
    }

    async function handlePointerUp() {
      if (!dragRef.current || !viewportRef.current) {
        dragRef.current = null;
        setIsMiddlePanActive(false);
        return;
      }

      const moved = dragRef.current.moved;
      dragRef.current = null;
      setIsMiddlePanActive(false);

      if (moved) {
        scheduleViewportCommit(viewportRef.current, 850);
      }
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitScale, session]);

  useEffect(() => {
    const marker = session?.markers.find((item) => item.markerId === selectedMarkerId) ?? null;
    setDraftLabel(marker?.label ?? "");
    if (marker) {
      setDraftPointType(marker.pointType ?? "center");
    }
    setDraftStatus(marker?.status ?? "human_draft");
    setDraftConfidence(marker?.confidence == null ? "" : String(marker.confidence));
    setDraftMarkerX(marker == null ? "" : String(Math.round(marker.x)));
    setDraftMarkerY(marker == null ? "" : String(Math.round(marker.y)));
    if (!marker) {
      setIsInlineEditorOpen(false);
    }
  }, [selectedMarkerId, session]);

  useEffect(() => {
    if (!selectedMarker || !isInlineEditorOpen || !inlineInputRef.current) {
      return;
    }

    inlineInputRef.current.focus();
    inlineInputRef.current.select();
  }, [isInlineEditorOpen, selectedMarker]);

  useEffect(() => {
    if (!document || localViewport) {
      return;
    }

    const initialViewport = {
      centerX: document.width / 2,
      centerY: document.height / 2,
      zoom: 1
    };
    setLocalViewport(initialViewport);
    viewportRef.current = initialViewport;
    lastSavedViewportRef.current = initialViewport;
  }, [document, localViewport]);

  useEffect(() => {
    if (!localViewport) {
      setDraftViewportX("");
      setDraftViewportY("");
      setDraftViewportZoom("");
      return;
    }

    setDraftViewportX(String(Math.round(localViewport.centerX)));
    setDraftViewportY(String(Math.round(localViewport.centerY)));
    setDraftViewportZoom(`${formatZoomValue(localViewport.zoom)}x`);
  }, [localViewport]);

  useEffect(() => {
    setPrecisionZoomLevel(defaultPrecisionZoom);
  }, [defaultPrecisionZoom, document?.storageUrl]);

  useEffect(() => {
    return () => {
      if (viewportCommitRef.current) {
        clearTimeout(viewportCommitRef.current);
      }
    };
  }, []);

  function toDocumentCoordinates(clientX: number, clientY: number) {
    if (!containerRef.current || !document || !localViewport) {
      return null;
    }

    const bounds = containerRef.current.getBoundingClientRect();
    return {
      x: Math.min(Math.max((clientX - bounds.left - translateX) / scale, 0), document.width),
      y: Math.min(Math.max((clientY - bounds.top - translateY) / scale, 0), document.height)
    };
  }

  function collectSnapCandidates(point: { x: number; y: number }, pointType: MarkerPointType) {
    if (!document) {
      return { candidates: [] as SnapCandidate[], searchRadius: 0 };
    }

    const imageData = sourceImageDataRef.current;
    if (!imageData || imageData.width !== document.width || imageData.height !== document.height) {
      return { candidates: [] as SnapCandidate[], searchRadius: 0 };
    }

    const searchRadius = clamp(Math.round(Math.max(document.width, document.height) * 0.015), 14, 28);
    const startX = clamp(Math.round(point.x) - searchRadius, 0, document.width - 1);
    const endX = clamp(Math.round(point.x) + searchRadius, 0, document.width - 1);
    const startY = clamp(Math.round(point.y) - searchRadius, 0, document.height - 1);
    const endY = clamp(Math.round(point.y) + searchRadius, 0, document.height - 1);
    const patchWidth = endX - startX + 1;
    const patchHeight = endY - startY + 1;

    if (patchWidth < 3 || patchHeight < 3) {
      return { candidates: [] as SnapCandidate[], searchRadius };
    }

    const pixelData = imageData.data;
    const darkness = new Float32Array(patchWidth * patchHeight);
    let maxDarkness = 0;

    for (let y = 0; y < patchHeight; y += 1) {
      for (let x = 0; x < patchWidth; x += 1) {
        const sourceIndex = ((startY + y) * imageData.width + (startX + x)) * 4;
        const alpha = pixelData[sourceIndex + 3];
        if (alpha < 30) {
          continue;
        }

        const luminance =
          pixelData[sourceIndex] * 0.299 +
          pixelData[sourceIndex + 1] * 0.587 +
          pixelData[sourceIndex + 2] * 0.114;
        const darknessValue = 255 - luminance;
        const patchIndex = y * patchWidth + x;
        darkness[patchIndex] = darknessValue;
        if (darknessValue > maxDarkness) {
          maxDarkness = darknessValue;
        }
      }
    }

    if (maxDarkness < 24) {
      return { candidates: [] as SnapCandidate[], searchRadius };
    }

    const darknessThreshold = Math.max(22, Math.min(68, maxDarkness * 0.2));
    const darkMask = new Uint8Array(patchWidth * patchHeight);
    let darkPixels = 0;
    for (let index = 0; index < darkness.length; index += 1) {
      if (darkness[index] >= darknessThreshold) {
        darkMask[index] = 1;
        darkPixels += 1;
      }
    }

    if (darkPixels === 0) {
      return { candidates: [] as SnapCandidate[], searchRadius };
    }

    const dilateRadius = searchRadius >= 20 ? 2 : 1;
    const dilatedMask = new Uint8Array(patchWidth * patchHeight);
    for (let y = 0; y < patchHeight; y += 1) {
      for (let x = 0; x < patchWidth; x += 1) {
        const index = y * patchWidth + x;
        if (!darkMask[index]) {
          continue;
        }

        for (let dy = -dilateRadius; dy <= dilateRadius; dy += 1) {
          const nextY = y + dy;
          if (nextY < 0 || nextY >= patchHeight) {
            continue;
          }
          for (let dx = -dilateRadius; dx <= dilateRadius; dx += 1) {
            const nextX = x + dx;
            if (nextX < 0 || nextX >= patchWidth) {
              continue;
            }
            dilatedMask[nextY * patchWidth + nextX] = 1;
          }
        }
      }
    }

    const visited = new Uint8Array(patchWidth * patchHeight);
    const queue = new Int32Array(patchWidth * patchHeight);
    const components: Array<{
      minX: number;
      maxX: number;
      minY: number;
      maxY: number;
      centerX: number;
      centerY: number;
      inkMass: number;
      score: number;
    }> = [];

    for (let startIndex = 0; startIndex < dilatedMask.length; startIndex += 1) {
      if (!dilatedMask[startIndex] || visited[startIndex]) {
        continue;
      }

      let head = 0;
      let tail = 0;
      queue[tail] = startIndex;
      tail += 1;
      visited[startIndex] = 1;

      let minX = patchWidth;
      let maxX = 0;
      let minY = patchHeight;
      let maxY = 0;
      let inkMass = 0;

      while (head < tail) {
        const currentIndex = queue[head];
        head += 1;
        const currentY = Math.floor(currentIndex / patchWidth);
        const currentX = currentIndex - currentY * patchWidth;

        if (currentX < minX) {
          minX = currentX;
        }
        if (currentX > maxX) {
          maxX = currentX;
        }
        if (currentY < minY) {
          minY = currentY;
        }
        if (currentY > maxY) {
          maxY = currentY;
        }

        inkMass += darkness[currentIndex];

        const neighbors = [
          currentIndex - 1,
          currentIndex + 1,
          currentIndex - patchWidth,
          currentIndex + patchWidth
        ];

        for (const neighborIndex of neighbors) {
          if (neighborIndex < 0 || neighborIndex >= dilatedMask.length) {
            continue;
          }

          const neighborY = Math.floor(neighborIndex / patchWidth);
          const neighborX = neighborIndex - neighborY * patchWidth;
          if (Math.abs(neighborX - currentX) + Math.abs(neighborY - currentY) !== 1) {
            continue;
          }

          if (!dilatedMask[neighborIndex] || visited[neighborIndex]) {
            continue;
          }

          visited[neighborIndex] = 1;
          queue[tail] = neighborIndex;
          tail += 1;
        }
      }

      const componentCenterX = startX + (minX + maxX) / 2;
      const componentCenterY = startY + (minY + maxY) / 2;
      const distance = Math.hypot(componentCenterX - point.x, componentCenterY - point.y);
      const componentWidth = maxX - minX + 1;
      const componentHeight = maxY - minY + 1;
      const oversizePenalty =
        componentWidth > patchWidth * 0.8 || componentHeight > patchHeight * 0.8 ? 120 : 0;
      const score = inkMass / (1 + distance * 1.8) - oversizePenalty;
      components.push({
        minX: startX + minX,
        maxX: startX + maxX,
        minY: startY + minY,
        maxY: startY + maxY,
        centerX: componentCenterX,
        centerY: componentCenterY,
        inkMass,
        score
      });
    }

    if (!components.length) {
      return { candidates: [] as SnapCandidate[], searchRadius };
    }

    const sortedComponents = [...components].sort((left, right) => right.score - left.score);
    const bestComponent = sortedComponents[0];
    const mergeGap = clamp(Math.round(searchRadius * 0.45), 6, 12);
    const maxUnionWidth = clamp(Math.round(searchRadius * 2.2), 26, 64);
    const maxUnionHeight = clamp(Math.round(searchRadius * 2.2), 26, 64);
    let mergedMinX = bestComponent.minX;
    let mergedMaxX = bestComponent.maxX;
    let mergedMinY = bestComponent.minY;
    let mergedMaxY = bestComponent.maxY;
    let mergedSomething = true;

    while (mergedSomething) {
      mergedSomething = false;
      for (const component of components) {
        const gapX = Math.max(0, Math.max(component.minX - mergedMaxX - 1, mergedMinX - component.maxX - 1));
        const gapY = Math.max(0, Math.max(component.minY - mergedMaxY - 1, mergedMinY - component.maxY - 1));
        const gap = Math.max(gapX, gapY);
        const distanceFromClick = Math.hypot(component.centerX - point.x, component.centerY - point.y);
        const nextMinX = Math.min(mergedMinX, component.minX);
        const nextMaxX = Math.max(mergedMaxX, component.maxX);
        const nextMinY = Math.min(mergedMinY, component.minY);
        const nextMaxY = Math.max(mergedMaxY, component.maxY);
        const nextWidth = nextMaxX - nextMinX + 1;
        const nextHeight = nextMaxY - nextMinY + 1;

        if (
          gap <= mergeGap &&
          distanceFromClick <= searchRadius * 1.1 &&
          nextWidth <= maxUnionWidth &&
          nextHeight <= maxUnionHeight &&
          (component.minX < mergedMinX ||
            component.maxX > mergedMaxX ||
            component.minY < mergedMinY ||
            component.maxY > mergedMaxY)
        ) {
          mergedMinX = nextMinX;
          mergedMaxX = nextMaxX;
          mergedMinY = nextMinY;
          mergedMaxY = nextMaxY;
          mergedSomething = true;
        }
      }
    }

    const localMergedMinX = mergedMinX - startX;
    const localMergedMaxX = mergedMaxX - startX;
    const localMergedMinY = mergedMinY - startY;
    const localMergedMaxY = mergedMaxY - startY;
    const searchMargin = clamp(Math.round(searchRadius * 0.55), 6, 14);
    const bandTop = clamp(localMergedMinY - searchMargin, 0, patchHeight - 1);
    const bandBottom = clamp(localMergedMaxY + searchMargin, 0, patchHeight - 1);
    const bandLeft = clamp(localMergedMinX - searchMargin, 0, patchWidth - 1);
    const bandRight = clamp(localMergedMaxX + searchMargin, 0, patchWidth - 1);
    const verticalLineThreshold = clamp(Math.round((bandBottom - bandTop + 1) * 0.16), 3, 12);
    const horizontalLineThreshold = clamp(Math.round((bandRight - bandLeft + 1) * 0.16), 3, 12);

    const columnCounts = new Int16Array(patchWidth);
    const rowCounts = new Int16Array(patchHeight);

    for (let y = bandTop; y <= bandBottom; y += 1) {
      for (let x = bandLeft; x <= bandRight; x += 1) {
        if (!darkMask[y * patchWidth + x]) {
          continue;
        }
        columnCounts[x] += 1;
        rowCounts[y] += 1;
      }
    }

    const findColumn = (from: number, to: number, step: number) => {
      for (let x = from; step > 0 ? x <= to : x >= to; x += step) {
        if (columnCounts[x] >= verticalLineThreshold) {
          return x;
        }
      }
      return null;
    };

    const findRow = (from: number, to: number, step: number) => {
      for (let y = from; step > 0 ? y <= to : y >= to; y += step) {
        if (rowCounts[y] >= horizontalLineThreshold) {
          return y;
        }
      }
      return null;
    };

    const frameLeft = findColumn(clamp(localMergedMinX - 1, 0, patchWidth - 1), bandLeft, -1);
    const frameRight = findColumn(clamp(localMergedMaxX + 1, 0, patchWidth - 1), bandRight, 1);
    const frameTop = findRow(clamp(localMergedMinY - 1, 0, patchHeight - 1), bandTop, -1);
    const frameBottom = findRow(clamp(localMergedMaxY + 1, 0, patchHeight - 1), bandBottom, 1);

    const hasFrame =
      frameLeft != null &&
      frameRight != null &&
      frameTop != null &&
      frameBottom != null &&
      frameRight - frameLeft >= 10 &&
      frameBottom - frameTop >= 10;

    const frameMinX = hasFrame ? startX + frameLeft : mergedMinX;
    const frameMaxX = hasFrame ? startX + frameRight : mergedMaxX;
    const frameMinY = hasFrame ? startY + frameTop : mergedMinY;
    const frameMaxY = hasFrame ? startY + frameBottom : mergedMaxY;

    const anchorForBounds = (bounds: { minX: number; maxX: number; minY: number; maxY: number }) => ({
      x: clamp(pointType === "top_left" ? bounds.minX : (bounds.minX + bounds.maxX) / 2, 0, document.width),
      y: clamp(pointType === "top_left" ? bounds.minY : (bounds.minY + bounds.maxY) / 2, 0, document.height)
    });

    const localPointX = clamp(Math.round(point.x) - startX, 0, patchWidth - 1);
    const localPointY = clamp(Math.round(point.y) - startY, 0, patchHeight - 1);
    const brightSeedRadius = clamp(Math.round(searchRadius * 0.32), 3, 8);
    let seedX = localPointX;
    let seedY = localPointY;
    let bestSeedDarkness = darkness[localPointY * patchWidth + localPointX] || 255;

    for (let dy = -brightSeedRadius; dy <= brightSeedRadius; dy += 1) {
      const nextY = localPointY + dy;
      if (nextY < 0 || nextY >= patchHeight) {
        continue;
      }
      for (let dx = -brightSeedRadius; dx <= brightSeedRadius; dx += 1) {
        const nextX = localPointX + dx;
        if (nextX < 0 || nextX >= patchWidth) {
          continue;
        }
        const index = nextY * patchWidth + nextX;
        const value = darkness[index];
        if (value < bestSeedDarkness) {
          bestSeedDarkness = value;
          seedX = nextX;
          seedY = nextY;
        }
      }
    }

    const lightThreshold = Math.max(12, darknessThreshold * 0.72);
    let enclosedRegionCandidate: SnapCandidate | null = null;
    if (bestSeedDarkness <= lightThreshold) {
      const regionVisited = new Uint8Array(patchWidth * patchHeight);
      const regionQueue = new Int32Array(patchWidth * patchHeight);
      let regionHead = 0;
      let regionTail = 0;
      const seedIndex = seedY * patchWidth + seedX;
      regionQueue[regionTail] = seedIndex;
      regionTail += 1;
      regionVisited[seedIndex] = 1;

      let regionMinX = seedX;
      let regionMaxX = seedX;
      let regionMinY = seedY;
      let regionMaxY = seedY;
      let regionArea = 0;
      let touchesPatchEdge = false;

      while (regionHead < regionTail) {
        const currentIndex = regionQueue[regionHead];
        regionHead += 1;
        const currentY = Math.floor(currentIndex / patchWidth);
        const currentX = currentIndex - currentY * patchWidth;
        regionArea += 1;

        if (currentX <= 0 || currentX >= patchWidth - 1 || currentY <= 0 || currentY >= patchHeight - 1) {
          touchesPatchEdge = true;
        }
        if (currentX < regionMinX) {
          regionMinX = currentX;
        }
        if (currentX > regionMaxX) {
          regionMaxX = currentX;
        }
        if (currentY < regionMinY) {
          regionMinY = currentY;
        }
        if (currentY > regionMaxY) {
          regionMaxY = currentY;
        }

        const neighbors = [
          currentIndex - 1,
          currentIndex + 1,
          currentIndex - patchWidth,
          currentIndex + patchWidth
        ];

        for (const neighborIndex of neighbors) {
          if (neighborIndex < 0 || neighborIndex >= patchWidth * patchHeight || regionVisited[neighborIndex]) {
            continue;
          }

          const neighborY = Math.floor(neighborIndex / patchWidth);
          const neighborX = neighborIndex - neighborY * patchWidth;
          if (Math.abs(neighborX - currentX) + Math.abs(neighborY - currentY) !== 1) {
            continue;
          }

          if (darkness[neighborIndex] > lightThreshold) {
            continue;
          }

          regionVisited[neighborIndex] = 1;
          regionQueue[regionTail] = neighborIndex;
          regionTail += 1;
        }
      }

      const regionWidth = regionMaxX - regionMinX + 1;
      const regionHeight = regionMaxY - regionMinY + 1;
      const regionMinXDoc = startX + regionMinX;
      const regionMaxXDoc = startX + regionMaxX;
      const regionMinYDoc = startY + regionMinY;
      const regionMaxYDoc = startY + regionMaxY;

      const countDarkOnHorizontal = (y: number, fromX: number, toX: number) => {
        let count = 0;
        for (let x = fromX; x <= toX; x += 1) {
          if (y < 0 || y >= patchHeight || x < 0 || x >= patchWidth) {
            continue;
          }
          if (darkMask[y * patchWidth + x]) {
            count += 1;
          }
        }
        return count;
      };

      const countDarkOnVertical = (x: number, fromY: number, toY: number) => {
        let count = 0;
        for (let y = fromY; y <= toY; y += 1) {
          if (x < 0 || x >= patchWidth || y < 0 || y >= patchHeight) {
            continue;
          }
          if (darkMask[y * patchWidth + x]) {
            count += 1;
          }
        }
        return count;
      };

      const topEdgeDark = countDarkOnHorizontal(regionMinY - 1, regionMinX - 1, regionMaxX + 1);
      const bottomEdgeDark = countDarkOnHorizontal(regionMaxY + 1, regionMinX - 1, regionMaxX + 1);
      const leftEdgeDark = countDarkOnVertical(regionMinX - 1, regionMinY - 1, regionMaxY + 1);
      const rightEdgeDark = countDarkOnVertical(regionMaxX + 1, regionMinY - 1, regionMaxY + 1);
      const horizontalSupport = Math.max(topEdgeDark, bottomEdgeDark);
      const verticalSupport = Math.max(leftEdgeDark, rightEdgeDark);

      const horizontalThreshold = clamp(Math.round(regionWidth * 0.22), 3, 16);
      const verticalThreshold = clamp(Math.round(regionHeight * 0.22), 3, 16);
      const supportedSides = [
        topEdgeDark >= horizontalThreshold,
        bottomEdgeDark >= horizontalThreshold,
        leftEdgeDark >= verticalThreshold,
        rightEdgeDark >= verticalThreshold
      ].filter(Boolean).length;
      const looksLikeEnclosedLabel =
        !touchesPatchEdge &&
        regionArea >= 24 &&
        regionArea <= patchWidth * patchHeight * 0.45 &&
        regionWidth >= 10 &&
        regionHeight >= 10 &&
        (supportedSides >= 3 || (horizontalSupport >= horizontalThreshold && verticalSupport >= verticalThreshold));

      if (looksLikeEnclosedLabel) {
        const regionAnchor = anchorForBounds({
          minX: regionMinXDoc,
          maxX: regionMaxXDoc,
          minY: regionMinYDoc,
          maxY: regionMaxYDoc
        });
        enclosedRegionCandidate = {
          ...regionAnchor,
          score: bestComponent.score + 150,
          source: "region"
        };
      }
    }

    const dedupedCandidates: SnapCandidate[] = [];
    const pushCandidate = (candidate: SnapCandidate | null) => {
      if (!candidate) {
        return;
      }

      const distanceFromClick = Math.hypot(candidate.x - point.x, candidate.y - point.y);
      if (distanceFromClick > searchRadius * 1.25) {
        return;
      }

      const existingIndex = dedupedCandidates.findIndex(
        (existing) => Math.hypot(existing.x - candidate.x, existing.y - candidate.y) < 4
      );
      if (existingIndex >= 0) {
        if (candidate.score > dedupedCandidates[existingIndex].score) {
          dedupedCandidates[existingIndex] = candidate;
        }
        return;
      }

      dedupedCandidates.push(candidate);
    };

    pushCandidate(enclosedRegionCandidate);

    if (hasFrame) {
      const frameAnchor = anchorForBounds({
        minX: frameMinX,
        maxX: frameMaxX,
        minY: frameMinY,
        maxY: frameMaxY
      });
      pushCandidate({
        ...frameAnchor,
        score: bestComponent.score + 120,
        source: "frame"
      });
    }

    const mergedAnchor = anchorForBounds({
      minX: mergedMinX,
      maxX: mergedMaxX,
      minY: mergedMinY,
      maxY: mergedMaxY
    });
    pushCandidate({
      ...mergedAnchor,
      score: bestComponent.score + 70,
      source: "merged"
    });

    for (const component of sortedComponents.slice(0, 4)) {
      const componentAnchor = anchorForBounds({
        minX: component.minX,
        maxX: component.maxX,
        minY: component.minY,
        maxY: component.maxY
      });
      pushCandidate({
        ...componentAnchor,
        score: component.score,
        source: "ink"
      });
    }

    dedupedCandidates.sort((left, right) => right.score - left.score);
    return {
      candidates: dedupedCandidates.slice(0, 3),
      searchRadius
    };
  }

  function snapPointToNearestInk(point: { x: number; y: number }, pointType: MarkerPointType) {
    const { candidates, searchRadius } = collectSnapCandidates(point, pointType);
    const bestPoint = candidates[0];
    if (!bestPoint) {
      return point;
    }

    const snapDistance = Math.hypot(bestPoint.x - point.x, bestPoint.y - point.y);
    if (snapDistance < 1.25 || snapDistance > searchRadius * 0.95) {
      return point;
    }

    return bestPoint;
  }

  async function createMarkerFromCandidate(candidate: CalloutCandidate) {
    if (isWorkspaceBusy) {
      return;
    }

    const nextSession = await runCommand(
      {
        type: "place_marker",
        actor: "human",
        candidateId: candidate.candidateId,
        pointType: placementPointType,
        label: null,
        status: "human_draft",
        confidence: null
      },
      { preserveLocalViewport: true }
    );

    if (!nextSession?.markers.length) {
      return;
    }

    const createdMarker = nextSession.markers[nextSession.markers.length - 1];
    setSelectedCandidateId(null);
    selectMarker(createdMarker.markerId, { openInline: true, focus: true });
    setDraftLabel(createdMarker.label ?? "");
  }

  async function handleCanvasClick(event: React.MouseEvent<HTMLDivElement>) {
    if (suppressCanvasClickRef.current) {
      suppressCanvasClickRef.current = false;
      return;
    }

    if (isWorkspaceBusy || mode !== "place" || isCtrlPressed || event.ctrlKey || !document || dragRef.current?.moved) {
      return;
    }

    const point = toDocumentCoordinates(event.clientX, event.clientY);
    if (!point) {
      return;
    }
    setSelectedCandidateId(null);
    const snappedPoint = snapPointToNearestInk(point, placementPointType);

    const nextSession = await runCommand(
      {
        type: "place_marker",
        actor: "human",
        x: snappedPoint.x,
        y: snappedPoint.y,
        pointType: placementPointType,
        label: null,
        status: draftStatus,
        confidence: draftConfidence ? Number(draftConfidence) : null
      },
      { preserveLocalViewport: true }
    );

    if (!nextSession?.markers.length) {
      return;
    }

    const createdMarker = nextSession.markers[nextSession.markers.length - 1];
    selectMarker(createdMarker.markerId, { openInline: true });
    setDraftLabel(createdMarker.label ?? "");
  }

  function handleCanvasPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    const shouldPan = !!localViewport && (mode === "pan" || event.button === 1 || event.ctrlKey || isCtrlPressed);
    if (!shouldPan || !localViewport) {
      return;
    }

    if (event.button === 1) {
      event.preventDefault();
      setIsMiddlePanActive(true);
    }

    if (mode === "place" && (event.button === 1 || event.ctrlKey || isCtrlPressed)) {
      suppressCanvasClickRef.current = true;
    }

    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      startCenterX: localViewport.centerX,
      startCenterY: localViewport.centerY,
      moved: false
    };
  }

  function handleWheel(event: React.WheelEvent<HTMLDivElement>) {
    if (!document || !localViewport || !containerRef.current) {
      return;
    }

    event.preventDefault();
    const bounds = containerRef.current.getBoundingClientRect();
    const pointerX = event.clientX - bounds.left;
    const pointerY = event.clientY - bounds.top;
    const docX = (pointerX - translateX) / scale;
    const docY = (pointerY - translateY) / scale;
    const nextZoom = Math.min(Math.max(localViewport.zoom * (event.deltaY < 0 ? 1.12 : 0.9), 0.35), 12);
    const nextScale = fitScale * nextZoom;
    const nextViewport = {
      centerX: docX - (pointerX - canvasCenterX) / nextScale,
      centerY: docY - (pointerY - canvasCenterY) / nextScale,
      zoom: nextZoom
    };

    updateLocalViewport(nextViewport);
    scheduleViewportCommit(nextViewport, 900);
  }

  async function handleUploadToExistingSession() {
    if (!uploadFile || isWorkspaceBusy) {
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const response = await uploadDocument(sessionId, uploadFile);
      syncSession(response.session, { preferFit: true });
      setUploadFile(null);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleExportSession() {
    if (!session?.document || isExporting || isWorkspaceBusy) {
      return;
    }

    setIsExporting(true);
    setError(null);
    try {
      await downloadSessionExport(session.sessionId, session.title);
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "Не удалось выгрузить архив.");
    } finally {
      setIsExporting(false);
    }
  }

  async function saveMarkerChanges() {
    if (!selectedMarker || isWorkspaceBusy) {
      return;
    }

    await runCommand(
      {
        type: "update_marker",
        actor: "human",
        markerId: selectedMarker.markerId,
        label: draftLabel || null,
        pointType: draftPointType,
        status: draftStatus,
        confidence: draftConfidence ? Number(draftConfidence) : null
      },
      { preserveLocalViewport: true }
    );
  }

  async function saveMarkerPatch(patch: Partial<Pick<NonNullable<AnnotationSession["markers"]>[number], "label" | "pointType" | "status" | "confidence">>) {
    if (!selectedMarker || isWorkspaceBusy) {
      return;
    }

    await runCommand(
      {
        type: "update_marker",
        actor: "human",
        markerId: selectedMarker.markerId,
        ...patch
      },
      { preserveLocalViewport: true }
    );
  }

  async function confirmSelectedMarker() {
    if (!selectedMarker || isWorkspaceBusy) {
      return null;
    }

    const trimmedDraftLabel = draftLabel.trim();
    const draftConfidenceValue = draftConfidence.trim() ? Number(draftConfidence) : null;
    const needsDraftSave =
      selectedMarker.status === "human_draft" &&
      (
        trimmedDraftLabel !== (selectedMarker.label ?? "").trim() ||
        draftPointType !== selectedMarker.pointType ||
        draftStatus !== selectedMarker.status ||
        draftConfidenceValue !== (selectedMarker.confidence ?? null)
      );

    if (needsDraftSave) {
      const updatedSession = await runCommand(
        {
          type: "update_marker",
          actor: "human",
          markerId: selectedMarker.markerId,
          label: trimmedDraftLabel || null,
          pointType: draftPointType,
          status: draftStatus,
          confidence: draftConfidenceValue
        },
        { preserveLocalViewport: true }
      );

      if (!updatedSession) {
        return null;
      }
    }

    const markerId = selectedMarker.markerId;
    const wasAmbiguityReview = hasSelectedAmbiguityReview;
    const nextSession = await runCommand(
      {
        type: "confirm_marker",
        actor: "human",
        markerId,
        status: "human_corrected"
      },
        { preserveLocalViewport: true }
      );

    if (wasAmbiguityReview && nextSession) {
      setReviewedAmbiguityMarkerIds((current) => (current.includes(markerId) ? current : [...current, markerId]));
    }

    return nextSession;
  }

  async function confirmSelectedMarkerAndAdvance() {
    if (!selectedMarker || currentPassAmbiguityMarkers.length === 0) {
      await confirmSelectedMarker();
      return;
    }

    const currentMarkerId = selectedMarker.markerId;
    const nextMarkerId = getNextMarkerIdInQueue(currentPassAmbiguityMarkers, currentMarkerId);

    const nextSession = await confirmSelectedMarker();

    if (nextSession && nextMarkerId && nextMarkerId !== currentMarkerId) {
      selectMarker(nextMarkerId, { focus: true });
    }
  }

  async function deleteSelectedMarkerAndAdvance() {
    if (!selectedMarker || currentPassAmbiguityMarkers.length === 0) {
      await deleteSelectedMarker();
      return;
    }

    const currentMarkerId = selectedMarker.markerId;
    const nextMarkerId = getNextMarkerIdInQueue(currentPassAmbiguityMarkers, currentMarkerId);

    const nextSession = await deleteSelectedMarker();

    if (nextSession && nextMarkerId && nextMarkerId !== currentMarkerId) {
      selectMarker(nextMarkerId, { focus: true });
    }
  }

  async function skipSelectedMarkerAndAdvance() {
    if (!selectedMarker || currentPassAmbiguityMarkers.length === 0 || isWorkspaceBusy) {
      return;
    }

    const currentMarkerSnapshot: LocalAmbiguityHistoryEntry = {
      historyId: `local-skip-${selectedMarker.markerId}-${Date.now()}`,
      createdAt: new Date().toISOString(),
      actor: "human",
      decision: "skipped",
      markerId: selectedMarker.markerId,
      label: selectedMarker.label,
      pointType: selectedMarker.pointType,
      status: selectedMarker.status,
      x: selectedMarker.x,
      y: selectedMarker.y
    };
    const currentMarkerId = selectedMarker.markerId;
    const nextMarkerId = getNextMarkerIdInQueue(currentPassAmbiguityMarkers, currentMarkerId);

    setSkippedAmbiguityMarkerIds((current) => (current.includes(currentMarkerId) ? current : [...current, currentMarkerId]));
    setLocalAmbiguityHistory((current) => [currentMarkerSnapshot, ...current].slice(0, 24));

    if (nextMarkerId && nextMarkerId !== currentMarkerId) {
      selectMarker(nextMarkerId, { focus: true });
    }
  }

  async function nudgeMarker(deltaX: number, deltaY: number) {
    if (!selectedMarker || isWorkspaceBusy) {
      return;
    }

    await runCommand(
      {
        type: "move_marker",
        actor: "human",
        markerId: selectedMarker.markerId,
        deltaX,
        deltaY
      },
      { preserveLocalViewport: true }
    );
  }

  async function deleteSelectedMarker() {
    if (!selectedMarker || isWorkspaceBusy) {
      return null;
    }

    const markerId = selectedMarker.markerId;
    const wasAmbiguityReview = hasSelectedAmbiguityReview;
    const nextSession = await runCommand(
      {
        type: "delete_marker",
        actor: "human",
        markerId
      },
      { preserveLocalViewport: true }
    );

    if (wasAmbiguityReview && nextSession) {
      setReviewedAmbiguityMarkerIds((current) => (current.includes(markerId) ? current : [...current, markerId]));
    }

    if (!nextSession) {
      return null;
    }

    if (!nextSession?.markers.some((item) => item.markerId === markerId)) {
      setSelectedMarkerId(nextSession?.markers[0]?.markerId ?? null);
      setIsInlineEditorOpen(false);
    }

    return nextSession;
  }

  async function applyMarkerCoordinates() {
    if (!selectedMarker || isWorkspaceBusy) {
      return;
    }

    const nextX = Number(draftMarkerX);
    const nextY = Number(draftMarkerY);
    if (!Number.isFinite(nextX) || !Number.isFinite(nextY)) {
      setError("Координаты точки должны быть числами.");
      return;
    }

    await runCommand(
      {
        type: "move_marker",
        actor: "human",
        markerId: selectedMarker.markerId,
        x: nextX,
        y: nextY
      },
      { preserveLocalViewport: true }
    );
  }

  async function moveMarkerToCoordinates(nextX: number, nextY: number) {
    if (!selectedMarker || !document || isWorkspaceBusy) {
      return;
    }

    const safeX = clamp(Math.round(nextX), 0, document.width);
    const safeY = clamp(Math.round(nextY), 0, document.height);

    setDraftMarkerX(String(safeX));
    setDraftMarkerY(String(safeY));

    await runCommand(
      {
        type: "move_marker",
        actor: "human",
        markerId: selectedMarker.markerId,
        x: safeX,
        y: safeY
      },
      { preserveLocalViewport: true }
    );
  }

  async function handlePrecisionLensClick(event: React.MouseEvent<HTMLButtonElement>) {
    if (!selectedMarker || !document || isWorkspaceBusy) {
      return;
    }

    const bounds = event.currentTarget.getBoundingClientRect();
    const localX = event.clientX - bounds.left;
    const localY = event.clientY - bounds.top;
    const nextX = selectedMarker.x + (localX - bounds.width / 2) / precisionZoom;
    const nextY = selectedMarker.y + (localY - bounds.height / 2) / precisionZoom;

    await moveMarkerToCoordinates(nextX, nextY);
  }

  function applyViewportFields() {
    if (!localViewport) {
      return;
    }

    const nextX = Number(draftViewportX.trim().replace(",", "."));
    const nextY = Number(draftViewportY.trim().replace(",", "."));
    const nextZoomValue = draftViewportZoom.trim().toLowerCase().replace(",", ".").replace(/x$/, "");
    const nextZoom = Number(nextZoomValue);

    if (!Number.isFinite(nextX) || !Number.isFinite(nextY) || !Number.isFinite(nextZoom)) {
      setError("Координаты и zoom должны быть числами.");
      return;
    }

    const nextViewport = {
      centerX: nextX,
      centerY: nextY,
      zoom: Math.min(Math.max(nextZoom, 0.35), 12)
    };

    updateLocalViewport(nextViewport);
    void commitViewport(nextViewport);
  }

  function resetView() {
    if (!localViewport || !document) {
      return;
    }

    const nextViewport = {
      centerX: document.width / 2,
      centerY: document.height / 2,
      zoom: 1
    };
    updateLocalViewport(nextViewport);
    scheduleViewportCommit(nextViewport, 450);
  }

  function applyZoom(multiplier: number) {
    if (!localViewport) {
      return;
    }

    const next = {
      ...localViewport,
      zoom: Math.min(Math.max(localViewport.zoom * multiplier, 0.35), 12)
    };
    updateLocalViewport(next);
    scheduleViewportCommit(next, 550);
  }

  function zoomToConflict(conflict: MarkerConflict) {
    if (!document) {
      return;
    }

    const padding = 72;
    const regionWidth = Math.max(120, conflict.maxX - conflict.minX + padding);
    const regionHeight = Math.max(120, conflict.maxY - conflict.minY + padding);
    const nextViewport = {
      centerX: clamp(conflict.centerX, 0, document.width),
      centerY: clamp(conflict.centerY, 0, document.height),
      zoom: clamp(Math.min(document.width / regionWidth, document.height / regionHeight), 1.1, 12)
    };

    updateLocalViewport(nextViewport);
    scheduleViewportCommit(nextViewport, 450);
    if (!selectedMarkerId && conflict.markerIds[0]) {
      setSelectedMarkerId(conflict.markerIds[0]);
    }
  }

  function focusPipelineConflict(conflict: SessionPipelineConflict) {
    if (!document) {
      return;
    }

    const marker = conflict.markerIds[0]
      ? session?.markers.find((item) => item.markerId === conflict.markerIds[0]) ?? null
      : null;
    const candidate = !marker && conflict.candidateIds[0]
      ? session?.candidates.find((item) => item.candidateId === conflict.candidateIds[0]) ?? null
      : null;

    if (marker) {
      setSelectedMarkerId(marker.markerId);
      setSelectedCandidateId(null);
    } else if (candidate) {
      setSelectedCandidateId(candidate.candidateId);
      setSelectedMarkerId(null);
    }

    const focusX =
      conflict.bboxX != null && conflict.bboxWidth != null
        ? conflict.bboxX + conflict.bboxWidth / 2
        : marker?.x ?? candidate?.centerX ?? document.width / 2;
    const focusY =
      conflict.bboxY != null && conflict.bboxHeight != null
        ? conflict.bboxY + conflict.bboxHeight / 2
        : marker?.y ?? candidate?.centerY ?? document.height / 2;
    const regionWidth = Math.max(120, conflict.bboxWidth ?? candidate?.bboxWidth ?? 64);
    const regionHeight = Math.max(120, conflict.bboxHeight ?? candidate?.bboxHeight ?? 64);

    const nextViewport = {
      centerX: clamp(focusX, 0, document.width),
      centerY: clamp(focusY, 0, document.height),
      zoom: clamp(Math.min(document.width / (regionWidth + 80), document.height / (regionHeight + 80)), 1.2, 12)
    };

    updateLocalViewport(nextViewport);
    scheduleViewportCommit(nextViewport, 450);
  }

  async function clearMarkers() {
    if (!session?.markers.length) {
      return;
    }

    const confirmed = window.confirm("Удалить все точки с этого чертежа?");
    if (!confirmed) {
      return;
    }

    await runCommand(
      {
        type: "clear_markers",
        actor: "human"
      },
      { preserveLocalViewport: true }
    );
  }

  async function submitInlineLabel() {
    if (!selectedMarker) {
      return;
    }

    const nextSession = await runCommand(
      {
        type: "update_marker",
        actor: "human",
        markerId: selectedMarker.markerId,
        label: draftLabel.trim() || null
      },
      { preserveLocalViewport: true }
    );

    if (nextSession) {
      setIsInlineEditorOpen(false);
    }
  }

  if (!session) {
    return (
      <Card className="border-[#d8dade] bg-white/80">
        <div className="space-y-3">
          <div>
            <p className="text-sm font-semibold text-[#1f2937]">
              {error ? "Не удалось открыть рабочее поле" : "Загружаю рабочее поле…"}
            </p>
            <p className="mt-1 text-sm text-[#5f636b]">{error ?? "Подтягиваю сессию и документ для разметки."}</p>
          </div>
          {error && (
            <button
              type="button"
              className="inline-flex min-h-9 items-center rounded-full border border-[#1f2937]/12 bg-white px-3 text-sm font-medium text-[#1f2937] transition disabled:opacity-50"
              disabled={isSessionLoading || isReloadingSession}
              onClick={() => void reloadCurrentSession({ preferFit: true })}
            >
              {isSessionLoading || isReloadingSession ? "Повторяю…" : "Повторить загрузку"}
            </button>
          )}
        </div>
      </Card>
    );
  }

  const summaryStats = [
    ["Всего точек", session.summary.totalMarkers],
    ["AI нашёл", session.summary.aiDetected],
    ["На проверку", session.summary.aiReview],
    ["Подтверждено", session.summary.humanConfirmed],
    ["Исправлено", session.summary.humanCorrected],
    ["Удалено", session.summary.rejected],
    ["В словаре", pageVocabulary.length],
    ["Пропущено", missingLabels.length],
    ["Pipeline", pipelineConflicts.length]
  ] as const;
  const autoMarkerCount = session.markers.filter((item) => item.createdBy === "ai").length;
  const hardPipelineConflictCount = pipelineConflicts.filter((item) => item.severity === "error").length;
  const renderedBackendHistoryEntries: RenderedHistoryEntry[] = session.actionLog.map((entry) => {
    const ambiguityDecision = classifyAmbiguityHistoryDecision(entry, knownAmbiguityMarkerIdsRef.current);
    const markerId = getPayloadString(entry.payload, "markerId");

    return {
      id: entry.actionId,
      actor: entry.actor,
      createdAt: entry.createdAt,
      markerId,
      ambiguityDecision,
      presentation: decorateHistoryAction(entry, ambiguityDecision)
    };
  });
  const renderedLocalHistoryEntries: RenderedHistoryEntry[] = localAmbiguityHistory.map((entry) => ({
    id: entry.historyId,
    actor: entry.actor,
    createdAt: entry.createdAt,
    markerId: entry.markerId,
    ambiguityDecision: entry.decision,
    presentation: formatLocalAmbiguityHistoryEntry(entry)
  }));
  const mergedRecentHistoryEntries = [...renderedBackendHistoryEntries, ...renderedLocalHistoryEntries]
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
    .slice(0, 8);
  const ambiguityHistorySummary = [...renderedBackendHistoryEntries, ...renderedLocalHistoryEntries].reduce(
    (summary, entry) => {
      if (entry.ambiguityDecision === "confirmed") {
        summary.confirmed += 1;
      } else if (entry.ambiguityDecision === "deleted") {
        summary.deleted += 1;
      } else if (entry.ambiguityDecision === "skipped") {
        summary.skipped += 1;
      } else if (entry.ambiguityDecision === "restored") {
        summary.restored += 1;
      }
      return summary;
    },
    { confirmed: 0, deleted: 0, skipped: 0, restored: 0 }
  );
  const ambiguityHistoryEntries = mergedRecentHistoryEntries.filter((entry) => entry.ambiguityDecision !== null);
  const ambiguityRouteByMarkerId = ambiguityHistoryEntries
    .slice()
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt))
    .reduce<Record<string, NonNullable<RenderedHistoryEntry["ambiguityDecision"]>[]>>((routes, entry) => {
      if (!entry.markerId || entry.ambiguityDecision == null) {
        return routes;
      }

      const route = routes[entry.markerId] ?? [];
      if (route[route.length - 1] !== entry.ambiguityDecision) {
        route.push(entry.ambiguityDecision);
      }
      routes[entry.markerId] = route;
      return routes;
    }, {});
  const unresolvedAmbiguityCount = ambiguityReviewMarkers.length;
  const primaryHistoryQueueContext = getPrimaryHistoryQueueContext(
    currentPassAmbiguityMarkers.length,
    deferredAmbiguityMarkers.length
  );
  const nextUnresolvedHistoryQueueCount = getQueueCountByContext(
    primaryHistoryQueueContext,
    currentPassAmbiguityMarkers.length,
    deferredAmbiguityMarkers.length
  );
  const historyModeScopeLabel = showOnlyAmbiguityHistory ? historyStatusLabels.ambiguity : historyStatusLabels.all;
  const historyModeQueueLabel = primaryHistoryQueueContext ? historyQueueContextLabels[primaryHistoryQueueContext] : historyStatusLabels.noQueue;
  const ambiguityHistoryNextStepHint =
    unresolvedAmbiguityCount > 0
      ? deferredAmbiguityMarkers.length > 0
        ? `Ждут финального решения ${unresolvedAmbiguityCount} точек, из них ${deferredAmbiguityMarkers.length} отложено на отдельный проход.`
        : `Ждут финального решения ${unresolvedAmbiguityCount} спорных точек.`
      : ambiguityHistoryEntries.length > 0
        ? "Все спорные точки в этой сессии уже закрыты."
        : null;
  const sessionMarkerIds = new Set(session.markers.map((marker) => marker.markerId));
  const displayedHistoryEntries = showOnlyAmbiguityHistory ? ambiguityHistoryEntries : mergedRecentHistoryEntries;
  const railShellClass =
    "absolute inset-y-0 z-30 flex min-h-0 flex-col overflow-hidden border-[#2f241d] bg-[#16120f] text-[#fff7ef] shadow-[0_26px_72px_rgba(8,6,5,0.34)]";
  const railSectionClass = "px-3.5 py-3";
  const railSectionTitleClass = "text-[10px] font-semibold uppercase tracking-[0.18em] text-[#b7a28f]";
  const inspectorInputClass =
    "h-9 w-full rounded-[0.8rem] border border-[#3a2d24] bg-[#120f0d] px-3 text-sm font-medium text-[#fff7ef] outline-none transition-none placeholder:text-[#7f7065] focus:border-[#7d6350] focus:ring-2 focus:ring-[#7d6350]/20";
  const toolbarButtonClass =
    "inline-flex h-9 items-center justify-center rounded-[0.85rem] border border-[#3a2b22] bg-[#1a1410] px-3 text-[12px] font-medium text-[#f6efe7] transition-none disabled:cursor-not-allowed disabled:opacity-35";
  const toolbarIconButtonClass = classNames(toolbarButtonClass, "w-9 px-0 text-[1.05rem] font-semibold");
  const toolbarSegmentClass =
    "inline-flex min-h-9 items-center justify-center rounded-[0.85rem] px-3 text-[12px] font-medium text-[#c7b9ad] transition-none";
  const toolbarSegmentActiveClass = "bg-[#f5eee6] text-[#1c1713] shadow-[0_12px_26px_rgba(12,10,8,0.3)]";

  return (
    <div className="relative h-full min-h-0 overflow-hidden bg-[#d7dade]">
      <section className="absolute inset-0 z-10 min-h-0 min-w-0">
        <div className="relative h-full w-full overflow-hidden bg-[radial-gradient(circle_at_top,#f4f4f5,rgba(220,223,228,0.96))]">
          <div
            ref={containerRef}
            className={classNames(
              "relative h-full overflow-hidden",
              isPanInteractionActive ? "cursor-grab" : "cursor-crosshair"
            )}
            onClick={handleCanvasClick}
            onPointerDown={handleCanvasPointerDown}
            onWheel={handleWheel}
          >
            <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(122,126,134,0.18)_1px,transparent_1px),linear-gradient(rgba(122,126,134,0.18)_1px,transparent_1px)] bg-[size:40px_40px]" />
            {document ? (
              <>
                <div
                  className="absolute left-0 top-0"
                  style={{
                    left: translateX,
                    top: translateY,
                    width: document.width,
                    height: document.height,
                    transform: `scale(${scale})`,
                    transformOrigin: "top left"
                  }}
                >
                  <div
                    aria-label={document.fileName}
                    role="img"
                    className="absolute left-0 top-0 block select-none shadow-[0_28px_72px_rgba(15,18,32,0.14)]"
                    style={{
                      left: 0,
                      top: 0,
                      width: document.width,
                      height: document.height,
                      maxWidth: "none",
                      pointerEvents: "none",
                      backgroundImage: `url(${resolveAssetUrl(document.storageUrl)})`,
                      backgroundPosition: "center",
                      backgroundRepeat: "no-repeat",
                      backgroundSize: "100% 100%"
                    }}
                  />

                  {session.markers.map((marker) => {
                    const isTopLeftPoint = marker.pointType === "top_left";
                    const isConflict = markerConflictById.has(marker.markerId);
                    const markerColorClass = isTopLeftPoint ? "bg-[#16a34a]" : "bg-[#d92d20]";
                    const markerShadowClass = isTopLeftPoint
                      ? "shadow-[0_3px_12px_rgba(22,163,74,0.34)]"
                      : "shadow-[0_3px_12px_rgba(217,45,32,0.38)]";
                    const markerSelectedShadowClass = isTopLeftPoint
                      ? "shadow-[0_0_0_2px_rgba(255,255,255,0.96),0_6px_14px_rgba(22,163,74,0.38)]"
                      : "shadow-[0_0_0_2px_rgba(255,255,255,0.96),0_6px_14px_rgba(217,45,32,0.42)]";

                    return (
                      <div
                        key={marker.markerId}
                        className="absolute left-0 top-0"
                        style={{ left: marker.x, top: marker.y }}
                      >
                        <button
                          type="button"
                          className={classNames(
                            "absolute left-0 top-0 h-4 w-4 rounded-full border-2 border-white transition",
                            markerColorClass,
                            markerShadowClass,
                            isConflict && "ring-2 ring-[#f59e0b]/85 ring-offset-0",
                            marker.markerId === selectedMarkerId && classNames("ring-2 ring-white/95 ring-offset-0", markerSelectedShadowClass)
                          )}
                          style={{
                            transform: isTopLeftPoint
                              ? `scale(${partialInverseScale})`
                              : `translate(-50%, -50%) scale(${partialInverseScale})`,
                            transformOrigin: isTopLeftPoint ? "top left" : "center center"
                          }}
                          aria-label={marker.label ? `Marker ${marker.label}` : "Marker"}
                          title={marker.label ? `Marker ${marker.label}` : "Marker"}
                          onClick={(event) => {
                            event.stopPropagation();
                            selectMarker(marker.markerId, { openInline: true });
                          }}
                        />
                      </div>
                    );
                  })}

                  {session.markers.map((marker) => {
                    if (!marker.label || (isInlineEditorOpen && marker.markerId === selectedMarkerId)) {
                      return null;
                    }

                    const isSelected = marker.markerId === selectedMarkerId;
                    const isConflict = markerConflictById.has(marker.markerId);
                    const isTopLeftPoint = marker.pointType === "top_left";
                    const offsetX =
                      (isTopLeftPoint ? (isSelected ? 20 : 18) : isSelected ? 13 : 10) * partialInverseScale;
                    const offsetY =
                      (isTopLeftPoint ? (isSelected ? -4 : -2) : isSelected ? -15 : -12) * partialInverseScale;

                    return (
                      <div
                        key={`${marker.markerId}-label`}
                        className="pointer-events-none absolute left-0 top-0"
                        style={{
                          left: marker.x + offsetX,
                          top: marker.y + offsetY,
                          transform: `scale(${partialInverseScale})`,
                          transformOrigin: "top left"
                        }}
                      >
                        <div
                          className={classNames(
                            "text-[13px] font-extrabold leading-none",
                            isTopLeftPoint ? "text-[#16803d]" : "text-[#d92d20]",
                            isConflict && "underline decoration-[#f59e0b] decoration-2 underline-offset-[3px]"
                          )}
                          style={{
                            textShadow:
                              "-1px 0 0 rgba(255,255,255,0.98), 1px 0 0 rgba(255,255,255,0.98), 0 -1px 0 rgba(255,255,255,0.98), 0 1px 0 rgba(255,255,255,0.98), -1px -1px 0 rgba(255,255,255,0.98), 1px -1px 0 rgba(255,255,255,0.98), -1px 1px 0 rgba(255,255,255,0.98), 1px 1px 0 rgba(255,255,255,0.98)"
                          }}
                        >
                          {marker.label}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {selectedMarker && inlineEditorPosition && (
                  <form
                    className="absolute z-50 flex items-center gap-2 rounded-[1rem] border border-[#d6d8de] bg-white/96 px-3 py-2 shadow-[0_20px_45px_rgba(12,14,18,0.16)] backdrop-blur"
                    style={inlineEditorPosition}
                    onSubmit={(event) => {
                      event.preventDefault();
                      void submitInlineLabel();
                    }}
                    onClick={(event) => event.stopPropagation()}
                    onPointerDown={(event) => event.stopPropagation()}
                  >
                    <input
                      ref={inlineInputRef}
                      value={draftLabel}
                      onChange={(event) => setDraftLabel(event.target.value)}
                      inputMode="numeric"
                      placeholder="Номер"
                      className="h-10 w-24 rounded-[0.85rem] border border-[#d6d8de] bg-white px-3 text-sm font-semibold text-ink outline-none transition focus:border-[#6a7078] focus:ring-2 focus:ring-[#aeb3bb]"
                    />
                    <button
                      type="submit"
                      className="inline-flex h-10 w-10 items-center justify-center rounded-[0.85rem] border border-transparent bg-ink text-sm font-semibold text-white shadow-[0_10px_24px_rgba(15,18,32,0.18)] transition"
                    >
                      &gt;
                    </button>
                    <button
                      type="button"
                      aria-label="Удалить точку"
                      className="inline-flex h-10 w-10 items-center justify-center rounded-[0.85rem] border border-[#f2c2be] bg-[#fff4f3] text-[#c9362a] shadow-[0_10px_24px_rgba(201,54,42,0.08)] transition"
                      onClick={() => void deleteSelectedMarker()}
                    >
                      <TrashIcon />
                    </button>
                  </form>
                )}
              </>
            ) : (
              <div className="absolute inset-0 flex items-center justify-center text-sm text-[#6b7079]">
                Загрузи чертёж, чтобы открыть поле.
              </div>
            )}
          </div>
        </div>
      </section>

      {(selectedCandidateConflict.length > 1 || activeConflict) && (
        <div className="absolute top-3 z-40 -translate-x-1/2" style={{ left: canvasCenterX }}>
          <div className="flex items-center gap-2 rounded-full border border-[#9a6b4a] bg-[#f5eee6] px-3 py-2 text-[#3a2b22] shadow-[0_18px_44px_rgba(8,10,14,0.22)] backdrop-blur">
            <span className="inline-flex h-2.5 w-2.5 rounded-full bg-[#f59e0b]" />
            <span className="text-sm font-semibold text-[#3a2b22]">
              {selectedCandidateConflict.length > 1 ? "Сначала разберём конфликт кандидатов" : `Конфликт: ${activeConflict?.label}`}
            </span>
            {(selectedCandidateConflict.length > 1 || (activeConflict?.markers.length ?? 0) > 1) && (
              <span className="rounded-full border border-[#c7b6a6] bg-[#fdf7f1] px-2 py-0.5 text-[11px] font-medium text-[#5a4638]">
                {selectedCandidateConflict.length > 1 ? `${selectedCandidateConflict.length} рядом` : `${activeConflict?.markers.length ?? 0} точки`}
              </span>
            )}
            {currentPassAmbiguityMarkers.length > 0 ? (
              <button
                type="button"
                className="inline-flex h-8 items-center justify-center rounded-full border border-[#7a5a23] bg-[#2e2418] px-3 text-[11px] font-semibold text-[#f5d0a8] transition"
                onClick={() => setAmbiguityQueueMode("current", { focusFirst: true })}
              >
                К AI review {currentPassAmbiguityMarkers.length}
              </button>
            ) : session.summary.aiReview > 0 ? (
              <span className="rounded-full border border-[#7a5a23] bg-[#2e2418] px-2 py-0.5 text-[11px] font-medium text-[#f5d0a8]">
                AI review отдельно: {session.summary.aiReview}
              </span>
            ) : null}
            {deferredAmbiguityMarkers.length > 0 && (
              <button
                type="button"
                className="inline-flex h-8 items-center justify-center rounded-full border border-[#2b4b67] bg-[#162433] px-3 text-[11px] font-semibold text-[#bfe1ff] transition"
                onClick={() => setAmbiguityQueueMode("deferred", { focusFirst: true })}
              >
                К отложенным {deferredAmbiguityMarkers.length}
              </button>
            )}
            <button
              type="button"
              className="inline-flex h-8 items-center justify-center rounded-full border border-[#7c4734] bg-[#2b1d18] px-3 text-xs font-semibold text-white transition"
              onClick={() => {
                if (selectedCandidate) {
                  selectCandidate(selectedCandidate.candidateId, { focus: true });
                  return;
                }
                if (activeConflict) {
                  zoomToConflict(activeConflict);
                }
              }}
            >
              Приблизить
            </button>
          </div>
        </div>
      )}

      {isCompactWorkspace && (
        <button
          type="button"
          className="absolute left-3 top-3 z-40 inline-flex min-h-10 items-center rounded-full border border-[#30343c] bg-[#17191f]/94 px-3 text-[12px] font-semibold text-white shadow-[0_16px_36px_rgba(8,10,14,0.28)] backdrop-blur transition"
          onClick={() => {
            setIsMarkerRailOpen((current) => {
              const nextState = !current;
              if (nextState) {
                setIsInspectorOpen(false);
              }
              return nextState;
            });
          }}
        >
          {isMarkerRailOpen ? "Скрыть список" : "Список"}
        </button>
      )}

      {(!isCompactWorkspace || isMarkerRailOpen) && (
      <aside
        className={classNames(
          railShellClass,
          isCompactWorkspace ? "bottom-24 left-3 right-auto top-16 z-40 rounded-[1.1rem] border" : "left-0 border-r"
        )}
        style={{ width: isCompactWorkspace ? floatingMarkerRailWidth : leftRailWidth }}
      >
        <div className="scrollbar-hidden flex-1 overflow-y-auto pb-6">
        <div className={railSectionClass}>
          {isCompactWorkspace && (
            <div className="mb-3 flex items-center justify-between gap-3">
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#8f949d]">Список точек</p>
              <button
                type="button"
                aria-label="Скрыть список точек"
                className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-white/5 text-[#d0d4db] transition"
                onClick={() => setIsMarkerRailOpen(false)}
              >
                <CloseIcon />
              </button>
            </div>
          )}
          <div className="min-w-0">
            <Link href="/" className="text-sm font-medium text-[#e9d7c5]">
              К сессиям
            </Link>
            {document ? (
              <div className="mt-3 space-y-1.5">
                <p className="break-words text-[12px] leading-5 text-[#d5c4b4]">{document.fileName}</p>
                <p className="text-[12px] leading-5 text-[#a9927d]">{document.width}×{document.height}</p>
              </div>
            ) : (
              <p className="mt-3 break-words text-[12px] leading-5 text-[#9f8d7d]">Чертёж ещё не загружен.</p>
            )}
          </div>

          {error && (
            <div className="mt-3 rounded-[0.95rem] border border-[#7b2d2d] bg-[#241617] px-3 py-2.5">
              <p className="text-sm font-medium text-[#ffcdcd]">Не получилось синхронизировать сессию.</p>
              <p className="mt-1 text-xs leading-5 text-[#f3b8b8]">{error}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="inline-flex min-h-8 items-center rounded-full border border-white/10 bg-white/5 px-2.5 text-[11px] font-medium text-white transition disabled:opacity-40"
                  disabled={isReloadingSession}
                  onClick={() =>
                    void reloadCurrentSession({
                      preserveLocalViewport: true,
                      manual: true
                    })
                  }
                >
                  {isReloadingSession ? "Обновляю…" : "Обновить сессию"}
                </button>
                <button
                  type="button"
                  className="inline-flex min-h-8 items-center rounded-full border border-white/10 bg-transparent px-2.5 text-[11px] font-medium text-[#f6d3d3] transition"
                  onClick={() => setError(null)}
                >
                  Скрыть
                </button>
              </div>
            </div>
          )}

          <AmbiguityWorkspaceBanner
            currentPassCount={currentPassAmbiguityMarkers.length}
            deferredCount={deferredAmbiguityMarkers.length}
            reviewCompleted={ambiguityReviewCompleted}
            onContinue={continueAmbiguityReviewFromHistory}
            onOpenDeferred={() => setAmbiguityQueueMode("deferred", { focusFirst: true })}
          />

          <div className={classNames("mt-4 grid gap-2", isUltraCompactWorkspace ? "grid-cols-1" : "grid-cols-2")}>
            <div className="min-w-0">
              <button
                type="button"
                className={toolbarButtonClass}
                onClick={() => setIsSummaryOpen(true)}
              >
                Сводка
              </button>
            </div>
            <div className="min-w-0">
              <button
                type="button"
                className={toolbarButtonClass}
                onClick={() => setIsHistoryOpen(true)}
              >
                История
              </button>
            </div>
          </div>
          <button
            type="button"
            className={classNames(toolbarButtonClass, "mt-2 w-full justify-center")}
            disabled={!document || isExporting || isWorkspaceBusy}
            onClick={() => void handleExportSession()}
          >
            {isExporting ? "Готовлю ZIP…" : "Экспорт ZIP"}
          </button>
          {(hardPipelineConflictCount > 0 || missingLabels.length > 0) && (
            <div className="mt-2 rounded-[0.95rem] border border-[#6d4a1a] bg-[#2a2118] px-3 py-2.5">
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#f5d0a8]">Экспорт пока заблокирован</p>
              <p className="mt-1 text-sm leading-5 text-[#f1ddc1]">
                {hardPipelineConflictCount > 0 && missingLabels.length > 0
                  ? `Нужно закрыть ${hardPipelineConflictCount} блокеров проверки и ${missingLabels.length} пропущенных меток.`
                  : hardPipelineConflictCount > 0
                    ? `Нужно закрыть ${hardPipelineConflictCount} блокеров проверки.`
                    : `Нужно закрыть ${missingLabels.length} пропущенных меток.`}
              </p>
              <p className="mt-1 text-xs leading-5 text-[#d5b78b]">
                {isImportedJobPreviewSession
                  ? "Подсказки ниже в блоках «Результат AI» и «Словарь страницы»."
                  : "Подсказки ниже в блоках «Авторазметка» и «Словарь страницы»."}
              </p>
            </div>
          )}
        </div>

        <div className="px-4 py-3">
          <div className={classNames("flex flex-wrap gap-2", isCompactWorkspace ? "flex-col items-start" : "items-start justify-between")}>
            <div>
              <p className={railSectionTitleClass}>{isImportedJobPreviewSession ? "Результат AI" : "Авторазметка"}</p>
              <p className="mt-1 text-sm text-[#eadccd]">
                {isImportedJobPreviewSession ? `${autoMarkerCount} точек перенесено из распознавания` : `${autoMarkerCount} точек уже выставлено`}
              </p>
            </div>
            {isImportedJobPreviewSession ? (
              <span className={classNames(
                "inline-flex min-h-8 items-center rounded-full border border-[#5a4435] bg-[#241c17] px-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#f0d4b7]",
                isCompactWorkspace && "w-full justify-center"
              )}>
                импортировано
              </span>
            ) : (
              <button
                type="button"
                className={classNames(
                  "inline-flex min-h-8 max-w-full items-center rounded-full border border-white/10 bg-white/5 px-2.5 text-[11px] font-medium text-white transition disabled:opacity-40",
                  isCompactWorkspace && "w-full justify-center"
                )}
                disabled={!document || isWorkspaceBusy}
                onClick={() => void runAutoAnnotate()}
              >
                {isAutoAnnotating ? "Идёт…" : "Прогнать"}
              </button>
            )}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs lg:grid-cols-3">
            <div className="min-w-0 rounded-[0.85rem] border border-white/8 bg-white/[0.03] px-3 py-2">
              <div className="text-[11px] leading-tight text-[#b39d8a]">AI</div>
              <div className="mt-1 font-semibold text-white">{session.summary.aiDetected + session.summary.aiReview}</div>
            </div>
            <div className="min-w-0 rounded-[0.85rem] border border-white/8 bg-white/[0.03] px-3 py-2">
              <div className="text-[11px] leading-tight text-[#b39d8a] break-words">Кандидаты</div>
              <div className="mt-1 font-semibold text-white">{pendingCandidates.length}</div>
            </div>
            <div className={classNames("min-w-0 rounded-[0.85rem] border border-white/8 bg-white/[0.03] px-3 py-2", isCompactWorkspace && "col-span-2")}>
              <div className="text-[11px] leading-tight text-[#b39d8a] break-words">Блокеры</div>
              <div className="mt-1 font-semibold text-white">{hardPipelineConflictCount}</div>
            </div>
          </div>
        </div>

        <div className="px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className={railSectionTitleClass}>Словарь страницы</p>
              <p className="mt-1 text-sm text-[#eadccd]">{pageVocabulary.length} меток найдено по странице</p>
              <p className="mt-1 text-xs text-[#ae9886]">
                {candidateAssociations.length} связей между номером и фигурой{associationConflictCount > 0 ? ` • ${associationConflictCount} спорных` : ""}
              </p>
            </div>
            {missingLabels.length > 0 && (
              <span className="inline-flex min-h-8 items-center rounded-full border border-[#6d4a1a] bg-[#2a2118] px-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8]">
                Пропущено: {missingLabels.length}
              </span>
            )}
          </div>

          {missingLabels.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {missingLabels.slice(0, 10).map((label) => (
                <span
                  key={label}
                  className="inline-flex min-h-6 items-center rounded-full border border-[#5b2020] bg-[#2a1717] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-[#ffb4b4]"
                >
                  {label}
                </span>
              ))}
              {missingLabels.length > 10 && (
                <span className="inline-flex min-h-6 items-center rounded-full border border-white/8 bg-white/[0.03] px-2 py-0.5 text-[10px] font-medium text-[#aeb4be]">
                  +{missingLabels.length - 10}
                </span>
              )}
            </div>
          )}

          <div className="mt-3">
            {pipelineConflicts.length === 0 ? (
              <div className="rounded-[0.9rem] border border-[#224532] bg-[#14251d] px-3 py-2 text-sm text-[#b8e7c8]">
                Жёстких конфликтов сейчас нет.
              </div>
            ) : (
              <div className="space-y-1.5">
                {pipelineConflicts.slice(0, 6).map((conflict) => (
                  <button
                    key={conflict.conflictId}
                    type="button"
                    onClick={() => focusPipelineConflict(conflict)}
                    className={classNames(
                      "block w-full rounded-[0.9rem] border px-3 py-2 text-left transition",
                      conflict.severity === "error"
                        ? "border-[#6d2a2a] bg-[#241617]"
                        : "border-[#5a4a1a] bg-[#231d15]"
                    )}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <p className="truncate text-[12px] font-semibold text-white">
                        {conflict.label ? `Конфликт ${conflict.label}` : "Неоднозначное место"}
                      </p>
                      <span
                        className={classNames(
                          "inline-flex min-h-6 items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]",
                          conflict.severity === "error"
                            ? "border border-[#7b2d2d] bg-[#331d1e] text-[#ffb4b4]"
                            : "border border-[#6d4a1a] bg-[#2a2118] text-[#f5d0a8]"
                        )}
                      >
                        {conflict.severity === "error" ? "error" : "warning"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-[#c8ccd3]">{conflict.message}</p>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between px-4 py-3">
          <div>
            <p className={railSectionTitleClass}>{isImportedJobPreviewSession ? "Очередь проверки" : "Кандидаты на проверку"}</p>
            <p className="mt-1 text-sm text-[#c8ccd3]">
              {isImportedJobPreviewSession ? `${pendingCandidates.length} точек и кандидатов ждут решения` : `${pendingCandidates.length} кандидатов ждут решения`}
            </p>
            <p className="mt-1 text-xs leading-5 text-[#8f949d]">
              {isImportedJobPreviewSession
                ? "Здесь лежат кандидаты и спорные места, которые стоит проверить перед финальным экспортом."
                : "Здесь сначала разбираются найденные варианты по номеру и фигуре. Спорные AI-точки идут отдельной очередью в списке точек ниже."}
            </p>
          </div>
        </div>

        <div className="scrollbar-hidden max-h-[28vh] overflow-y-auto px-2 py-2">
          {pendingCandidates.length === 0 ? (
            <div className="px-3 py-4 text-sm text-[#969ba5]">
              {isImportedJobPreviewSession ? "Новых мест для обязательной проверки сейчас нет." : "Новых кандидатов пока нет."}
            </div>
          ) : (
            <div className="space-y-1.5">
              {pendingCandidates.map((candidate, index) => {
                const selected = candidate.candidateId === selectedCandidateId;
                const isConflict = (candidate.conflictCount ?? 0) > 1;
                const associationCount = candidateAssociationCountById.get(candidate.candidateId) ?? 0;

                return (
                  <button
                    key={candidate.candidateId}
                    type="button"
                    onClick={() => selectCandidate(candidate.candidateId, { focus: true })}
                    className={classNames(
                      "block w-full rounded-[0.9rem] border px-3 py-2 text-left transition",
                      selected
                        ? "border-[#474c55] bg-[#22262d] shadow-[0_10px_24px_rgba(10,12,16,0.28)]"
                        : "border-transparent bg-transparent",
                      isConflict && "border-[#6d4a1a] bg-[#231d15]/70"
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <span
                        className={classNames(
                          "mt-1 h-2.5 w-2.5 rounded-full",
                          candidate.kind === "box"
                            ? "bg-[#16a34a]"
                            : candidate.kind === "text"
                              ? "bg-[#60a5fa]"
                              : "bg-[#f59e0b]"
                        )}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-[13px] font-semibold text-white">
                            {candidate.suggestedLabel ? `№ ${candidate.suggestedLabel}` : `${candidateKindLabels[candidate.kind]} ${index + 1}`}
                          </p>
                          {isConflict && (
                            <span className="inline-flex items-center rounded-full border border-[#6d4a1a] bg-[#2a2118] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8]">
                              конфликт
                            </span>
                          )}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                          <span className="text-xs text-[#9ba1ab]">
                            {Math.round(candidate.centerX)}, {Math.round(candidate.centerY)}
                          </span>
                          <span className="text-xs text-[#6f7681]">•</span>
                          <span className="text-xs text-[#9ba1ab]">уверенность {Math.round(candidate.score)}</span>
                          {candidate.suggestedConfidence != null && (
                            <>
                              <span className="text-xs text-[#6f7681]">•</span>
                              <span className="text-xs text-[#d6dae1]">{formatCandidateConfidence(candidate.suggestedConfidence)}</span>
                            </>
                          )}
                        </div>
                        {(candidate.suggestedLabel || candidate.suggestedSource) && (
                          <div className="mt-1.5 flex flex-wrap gap-1.5">
                            {candidate.suggestedLabel && (
                              <span className="inline-flex min-h-6 items-center rounded-full border border-[#3e5f2b] bg-[#1c2718] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-[#d7f5c9]">
                                найден номер {candidate.suggestedLabel}
                              </span>
                            )}
                            {candidate.suggestedSource && (
                              <span className="inline-flex min-h-6 items-center rounded-full border border-white/8 bg-white/[0.03] px-2 py-0.5 text-[10px] font-medium text-[#aeb4be]">
                                источник: {candidate.suggestedSource}
                              </span>
                            )}
                            {associationCount > 0 && (
                              <span className="inline-flex min-h-6 items-center rounded-full border border-[#2b4b67] bg-[#162433] px-2 py-0.5 text-[10px] font-medium text-[#bfe1ff]">
                                связей {associationCount}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <MarkerRailFooter
          sectionTitleClass={railSectionTitleClass}
          displayedCount={displayedMarkers.length}
          totalCount={session.markers.length}
          showAmbiguityMarkersOnly={showAmbiguityMarkersOnly}
          activeQueueLength={activeAmbiguityQueueMarkers.length}
          activeQueueLabel={activeAmbiguityQueueLabel}
          totalAmbiguityCount={ambiguityReviewMarkers.length}
          showDeferredAmbiguityMarkersOnly={showDeferredAmbiguityMarkersOnly}
          skippedCount={skippedAmbiguityMarkerIds.length}
          currentPassCount={currentPassAmbiguityMarkers.length}
          currentPosition={ambiguityReviewCurrentPosition}
          progress={ambiguityReviewProgress}
          filterMode={ambiguityMarkerFilterMode}
          deferredCount={deferredAmbiguityMarkers.length}
          selectedIndex={selectedAmbiguityMarkerIndex}
          reviewCompleted={ambiguityReviewCompleted}
          hasDeferred={hasDeferredAmbiguityMarkers}
          selectedMarkerLabel={selectedMarker?.label ?? null}
          hasSelectedAmbiguityReview={hasSelectedAmbiguityReview}
          selectedReviewTooltip={selectedAmbiguityReviewMessages.join(" • ")}
          selectedMarkerIsDraft={selectedMarker?.status === "human_draft"}
          onSetMode={setAmbiguityQueueMode}
          onFocusAmbiguityMarker={focusAmbiguityMarker}
        />

        <div className="scrollbar-hidden flex-1 overflow-y-auto px-2 py-2">
          {displayedMarkers.length === 0 ? (
            <div className="px-3 py-6 text-sm text-[#969ba5]">
              {showDeferredAmbiguityMarkersOnly
                ? "Отложенных ambiguity-точек сейчас нет."
                : showAmbiguityMarkersOnly
                  ? "Спорных AI review-точек сейчас нет."
                  : isImportedJobPreviewSession
                    ? "AI не поставил точки. Их можно добавить вручную на холсте."
                    : "Пока точек нет."}
            </div>
          ) : (
            <div className="space-y-1.5">
              {displayedMarkers.map((marker) => {
                const selected = marker.markerId === selectedMarkerId;
                const isConflict = markerConflictById.has(marker.markerId);
                const ambiguityConflicts = markerAmbiguityConflictsById.get(marker.markerId) ?? [];
                const hasAmbiguityReview = marker.status === "ai_review" && ambiguityConflicts.length > 0;
                const hasNearTieReview = ambiguityConflicts.some((conflict) => hasNearTieAmbiguity(conflict.message));
                const ambiguityTooltip = Array.from(
                  new Set(ambiguityConflicts.map((conflict) => conflict.message.trim()).filter(Boolean))
                ).join(" • ");

                return (
                  <MarkerListItem
                    key={marker.markerId}
                    marker={marker}
                    selected={selected}
                    tone={isConflict ? "conflict" : "normal"}
                    hasAmbiguityReview={hasAmbiguityReview}
                    hasNearTieReview={hasNearTieReview}
                    ambiguityTooltip={ambiguityTooltip}
                    onSelect={() => selectMarker(marker.markerId, { focus: true })}
                  />
                );
              })}
            </div>
          )}
        </div>

        {!document && (
          <div className="border-t border-white/8 px-5 py-4">
            <div className="space-y-3">
              <p className={railSectionTitleClass}>Загрузить чертёж</p>
              <label className="flex cursor-pointer items-center justify-between rounded-[1rem] border border-white/10 bg-[#111317] px-4 py-3 text-sm text-[#c8ccd3]">
                <span className="truncate">{uploadFile ? uploadFile.name : "Выбери изображение"}</span>
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
                />
              </label>
              <button
                type="button"
                className={classNames(toolbarButtonClass, "w-full justify-center")}
                disabled={!uploadFile || isWorkspaceBusy}
                onClick={handleUploadToExistingSession}
              >
                {busy ? "Загружаю…" : "Загрузить"}
              </button>
            </div>
          </div>
        )}
        </div>
      </aside>
      )}

      {isSummaryOpen && (
        <div
          className="absolute left-4 top-4 z-40"
          style={{ width: isCompactWorkspace ? floatingOverlayWidth : Math.min(Math.max(leftRailWidth + 72, 260), 344) }}
        >
          <div className="rounded-[1.1rem] border border-[#2b2e35] bg-[#17191f] p-4 text-white shadow-[0_24px_60px_rgba(8,10,14,0.38)]">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-white">Сводка</h2>
              <button
                type="button"
                aria-label="Закрыть сводку"
                className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-white/5 text-[#d0d4db] transition"
                onClick={() => setIsSummaryOpen(false)}
              >
                <CloseIcon />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2 text-sm">
              {summaryStats.map(([label, value]) => (
                <div key={label} className="rounded-[0.95rem] border border-white/8 bg-[#111317] p-3">
                  <p className="text-[11px] uppercase tracking-[0.16em] text-[#9499a3]">{label}</p>
                  <p className="mt-2 text-lg font-semibold text-white">{value}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {isHistoryOpen && (
        <div
          className="absolute bottom-20 left-4 z-40"
          style={{ width: isCompactWorkspace ? floatingOverlayWidth : Math.min(Math.max(leftRailWidth + 96, 280), 380) }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="annotation-history-title"
            className="flex max-h-[min(68vh,620px)] flex-col rounded-[1.1rem] border border-[#2b2e35] bg-[#17191f] p-4 text-white shadow-[0_24px_60px_rgba(8,10,14,0.38)]"
          >
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <h2 id="annotation-history-title" className="text-base font-semibold text-white">
                  История
                </h2>
              </div>
              <button
                type="button"
                aria-label="Закрыть историю"
                className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-white/5 text-[#d0d4db] transition"
                onClick={() => setIsHistoryOpen(false)}
              >
                <CloseIcon />
              </button>
            </div>

              <div
                className="scrollbar-hidden min-h-0 flex-1 overflow-y-auto pr-1"
                onScroll={(event) => {
                  const nextCompact = event.currentTarget.scrollTop > 24;
                  setIsHistoryHeaderCompact((current) => (current === nextCompact ? current : nextCompact));
                }}
              >
                <div
                  className={classNames(
                    "sticky top-0 z-10 -mx-1 border-b border-white/8 bg-[#17191f]/95 px-1 backdrop-blur transition-[padding,margin] duration-150",
                    isHistoryHeaderCompactActive ? "mb-2 pb-2" : "mb-3 pb-3"
                  )}
                >
                  {!isHistoryHeaderCompactActive && (
                    <p className="text-[11px] text-[#9499a3]">
                      {showOnlyAmbiguityHistory ? `Спорных решений: ${ambiguityHistoryEntries.length}` : `Последних действий: ${mergedRecentHistoryEntries.length}`}
                    </p>
                  )}
                  <HistorySummaryChips summary={ambiguityHistorySummary} compact={isHistoryHeaderCompactActive} />
                  <HistoryNextStepPanel
                    compact={isHistoryHeaderCompactActive}
                    hint={ambiguityHistoryNextStepHint}
                    currentPassCount={currentPassAmbiguityMarkers.length}
                    deferredCount={deferredAmbiguityMarkers.length}
                    primaryQueueContext={primaryHistoryQueueContext}
                    onContinue={continueAmbiguityReviewFromHistory}
                    onOpenQueue={openAmbiguityQueueFromHistory}
                  />
                  <HistoryStickyToolbar
                    compact={isHistoryHeaderCompactActive}
                    hasUnresolved={hasUnresolvedAmbiguityMarkers}
                    primaryQueueContext={primaryHistoryQueueContext}
                    nextUnresolvedCount={nextUnresolvedHistoryQueueCount}
                    historyAlternateQueueAction={historyAlternateQueueAction}
                    showOnlyAmbiguityHistory={showOnlyAmbiguityHistory}
                    ambiguityHistoryCount={ambiguityHistoryEntries.length}
                    isCompactPinned={isHistoryHeaderCompactPinned}
                    historyModeScopeLabel={historyModeScopeLabel}
                    historyModeQueueLabel={historyModeQueueLabel}
                    onContinue={continueAmbiguityReviewFromHistory}
                    onOpenQueue={openAmbiguityQueueFromHistory}
                    onToggleScope={() => setShowOnlyAmbiguityHistory((current) => !current)}
                    onToggleCompactPinned={() => setIsHistoryHeaderCompactPinned((current) => !current)}
                  />
                </div>
                {displayedHistoryEntries.length === 0 && (
                  <div className="rounded-[1rem] border border-white/8 bg-[#111317] px-3 py-4 text-sm text-[#969ba5]">
                    {showOnlyAmbiguityHistory ? (
                      <>
                        <p>В последних действиях нет ambiguity-решений.</p>
                        <p className="mt-1 text-xs text-[#7f8691]">Выключи фильтр, чтобы увидеть всю историю.</p>
                      </>
                    ) : (
                      "В истории пока нет действий."
                    )}
                  </div>
                )}
                {displayedHistoryEntries.map((entry) => (
                  (() => {
                    const viewModel = buildHistoryEntryCardViewModel({
                      entry,
                      ambiguityRouteByMarkerId,
                      currentPassAmbiguityMarkerIds,
                      deferredAmbiguityMarkerIds,
                      sessionMarkerIds
                    });

                    return (
                      <HistoryEntryCard
                        key={entry.id}
                        entry={entry}
                        route={viewModel.route}
                        canJumpToMarker={viewModel.canJumpToMarker}
                        historyJumpContext={viewModel.historyJumpContext}
                        nextStepHint={viewModel.nextStepHint}
                        ambiguityMetaLabel={viewModel.ambiguityMetaLabel}
                        onJumpToMarker={jumpToHistoryMarker}
                      />
                    );
                  })()
                ))}
              </div>
          </div>
        </div>
      )}

      {isCompactWorkspace && (hasInspectorContentFocus || isInspectorOpen) && (
        <button
          type="button"
          className="absolute right-3 top-3 z-40 inline-flex min-h-10 items-center rounded-full border border-[#30343c] bg-[#17191f]/94 px-3 text-[12px] font-semibold text-white shadow-[0_16px_36px_rgba(8,10,14,0.28)] backdrop-blur transition"
          onClick={() => {
            setIsInspectorOpen((current) => {
              const nextState = !current;
              if (nextState) {
                setIsMarkerRailOpen(false);
              }
              return nextState;
            });
          }}
        >
          {isInspectorOpen ? "Скрыть панель" : inspectorToggleLabel}
        </button>
      )}

      {(!isCompactWorkspace || (isInspectorOpen && hasInspectorContentFocus)) && (
      <aside
        className={classNames(
          railShellClass,
          isCompactWorkspace ? "bottom-24 right-3 left-auto top-16 z-40 rounded-[1.1rem] border" : "right-0 border-l"
        )}
        style={{ width: isCompactWorkspace ? floatingInspectorWidth : rightRailWidth }}
      >
        <div className="scrollbar-hidden flex-1 overflow-y-auto">
          <section className={railSectionClass}>
            {isCompactWorkspace && (
              <div className="mb-3 flex items-center justify-between gap-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#8f949d]">Панель проверки</p>
                <button
                  type="button"
                  aria-label="Скрыть панель проверки"
                  className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-white/5 text-[#d0d4db] transition"
                  onClick={() => setIsInspectorOpen(false)}
                >
                  <CloseIcon />
                </button>
              </div>
            )}
            {selectedCandidate && !selectedMarker ? (
              <div className="space-y-4">
                <CandidateReviewNotice
                  conflictCount={selectedCandidateConflict.length}
                  hasAssociations={selectedCandidateAssociations.length > 0}
                  candidateQueuePosition={selectedCandidateQueuePosition}
                  totalPendingCandidates={pendingCandidates.length}
                  currentPassCount={currentPassAmbiguityMarkers.length}
                  deferredCount={deferredAmbiguityMarkers.length}
                  onOpenReview={() => setAmbiguityQueueMode("current", { focusFirst: true })}
                  onOpenDeferred={() => setAmbiguityQueueMode("deferred", { focusFirst: true })}
                />
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#8f949d]">Кандидат</span>
                    <span className="text-[11px] font-medium text-[#c8ccd3]">
                      {candidateKindLabels[selectedCandidate.kind]} • уверенность {Math.round(selectedCandidate.score)}
                    </span>
                  </div>
                  <div className="overflow-hidden rounded-[1rem] border border-[#30343c] bg-[#111317]">
                    {selectedCandidate.cropUrl ? (
                      <Image
                        loader={passthroughImageLoader}
                        unoptimized
                        src={resolveAssetUrl(selectedCandidate.cropUrl)}
                        alt="candidate crop"
                        width={Math.max(Math.round(selectedCandidate.bboxWidth), 1)}
                        height={Math.max(Math.round(selectedCandidate.bboxHeight), 1)}
                        className="block h-auto w-full"
                        style={{ width: "100%", height: "auto" }}
                      />
                    ) : (
                      <div className="flex h-44 items-center justify-center text-sm text-[#9196a0]">Увеличенный фрагмент пока недоступен</div>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {selectedCandidate.conflictCount > 1 && (
                      <span className="inline-flex min-h-8 items-center rounded-full border border-[#6d4a1a] bg-[#2a2118] px-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#f5d0a8]">
                        конфликт рядом
                      </span>
                    )}
                    {selectedCandidate.suggestedLabel && (
                      <span className="inline-flex min-h-8 items-center rounded-full border border-[#3e5f2b] bg-[#1c2718] px-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#d7f5c9]">
                        найден номер {selectedCandidate.suggestedLabel}
                      </span>
                    )}
                    {selectedCandidate.suggestedConfidence != null && (
                      <span className="inline-flex min-h-8 items-center rounded-full border border-white/10 bg-white/5 px-3 text-[11px] font-medium text-white">
                        уверенность {formatCandidateConfidence(selectedCandidate.suggestedConfidence)}
                      </span>
                    )}
                    {selectedCandidate.suggestedSource && (
                      <span className="inline-flex min-h-8 items-center rounded-full border border-white/10 bg-white/5 px-3 text-[11px] font-medium text-[#c8ccd3]">
                        источник: {selectedCandidate.suggestedSource}
                      </span>
                    )}
                    <span className="inline-flex min-h-8 items-center rounded-full border border-white/10 bg-white/5 px-3 text-[11px] font-medium text-white">
                      x {Math.round(selectedCandidate.centerX)} • y {Math.round(selectedCandidate.centerY)}
                    </span>
                  </div>
                </div>

                <div className="rounded-[1rem] border border-[#3a3022] bg-[#1b1712] px-3 py-3">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#f5d0a8]">Что сделать сейчас</p>
                  <p className="mt-1 text-sm leading-5 text-[#d8dbe1]">
                    Сейчас нужно решить, это реальная точка или лишний вариант рядом.
                  </p>
                  <div className="mt-2 space-y-1.5 text-xs leading-5 text-[#d8dbe1]">
                    <p>Если это нужная точка - «Создать точку».</p>
                    <p>Если это ошибка или шум - «Ложный».</p>
                  </div>
                  <p className="mt-2 text-xs leading-5 text-[#aeb4be]">
                    Спорные AI-точки не потеряны: они идут отдельной очередью в списке точек как AI review.
                  </p>
                  <p className="mt-1 text-xs leading-5 text-[#aeb4be]">
                    {pendingCandidates.length > 1
                      ? "После решения этого кандидата откроется следующий кандидат из очереди."
                      : currentPassAmbiguityMarkers.length > 0
                        ? "После решения этого кандидата можно сразу вернуться к очереди AI review кнопкой выше."
                        : deferredAmbiguityMarkers.length > 0
                          ? "После решения этого кандидата останутся только отложенные AI-точки."
                          : "После решения этого кандидата в сессии не останется незакрытых шагов этого типа."}
                  </p>
                </div>

                <div className="space-y-2 rounded-[1rem] border border-[#30343c] bg-[#13161b] p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#8f949d]">Похожие варианты рядом</span>
                    <span className="text-[11px] font-medium text-[#c8ccd3]">{selectedCandidateAssociations.length}</span>
                  </div>
                  {selectedCandidateAssociations.length === 0 ? (
                    <p className="text-sm text-[#9196a0]">Для этого места пока нет явных связанных вариантов.</p>
                  ) : (
                    <div className="space-y-2">
                      {selectedCandidateAssociations.slice(0, 4).map((association) => {
                        const linkedCandidateId =
                          selectedCandidate.kind === "text" ? association.shapeCandidateId : association.textCandidateId;
                        const linkedCandidate = candidatesById.get(linkedCandidateId) ?? null;
                        const linkedKindLabel = linkedCandidate ? candidateKindLabels[linkedCandidate.kind] : "Связанный кандидат";

                        return (
                          <button
                            key={association.associationId}
                            type="button"
                            className="block w-full rounded-[0.9rem] border border-white/8 bg-white/[0.03] px-3 py-2 text-left transition"
                            onClick={() => selectCandidate(linkedCandidateId, { focus: true })}
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <p className="truncate text-[12px] font-semibold text-white">
                                  {linkedKindLabel}
                                  {association.label ? ` • ${association.label}` : ""}
                                </p>
                                <p className="mt-1 text-xs text-[#aeb4be]">
                                  похожесть {Math.round(association.score * 100)}%
                                  {association.topologyScore != null ? ` • рядом ${Math.round(association.topologyScore * 100)}%` : ""}
                                </p>
                              </div>
                              <span className="inline-flex min-h-7 items-center rounded-full border border-[#2b4b67] bg-[#162433] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#bfe1ff]">
                                перейти
                              </span>
                            </div>
                          </button>
                        );
                      })}
                      {selectedCandidateAssociations.length > 4 && (
                        <p className="text-xs text-[#8f949d]">Ещё {selectedCandidateAssociations.length - 4} похожих вариантов скрыто.</p>
                      )}
                    </div>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    className={toolbarButtonClass}
                    disabled={isWorkspaceBusy}
                    onClick={() => void createMarkerFromCandidate(selectedCandidate)}
                  >
                    Создать точку
                  </button>
                  <button
                    type="button"
                    className={classNames(toolbarButtonClass, "text-[#fca5a5]")}
                    disabled={isWorkspaceBusy}
                    onClick={() => void rejectCandidate(selectedCandidate.candidateId)}
                  >
                    Ложный
                  </button>
                </div>
              </div>
            ) : selectedMarker ? (
              <div className="space-y-4">
                {selectedMarker.status === "ai_review" && (selectedMarkerAmbiguityConflicts.length > 0 || selectedAmbiguityReviewCandidates.length > 0) && (
                  <AmbiguityReviewPanel
                    reviewCandidates={selectedAmbiguityReviewCandidates}
                    reviewConflictCount={selectedMarkerAmbiguityConflicts.length}
                    reviewTypeLabels={selectedAmbiguityReviewTypeLabels}
                    reviewMessages={selectedAmbiguityReviewMessages}
                    hasNearTieAmbiguity={selectedMarkerHasNearTieAmbiguity}
                    reviewQueueTitle={selectedAmbiguityQueueTitle}
                    reviewQueueHint={selectedAmbiguityQueueHint}
                    showDeferredPass={showDeferredAmbiguityMarkersOnly}
                    canSkip={canSkipSelectedAmbiguityReview}
                    canConfirm={canConfirmSelectedAmbiguityReview}
                    busy={isWorkspaceBusy}
                    hasConflictFocus={Boolean(selectedMarkerAmbiguityConflicts[0])}
                    onFocusConflict={() => {
                      if (selectedMarkerAmbiguityConflicts[0]) {
                        focusPipelineConflict(selectedMarkerAmbiguityConflicts[0]);
                      }
                    }}
                    onSelectCandidate={(candidateId) => selectCandidate(candidateId, { focus: true })}
                    onMoveMarker={(x, y) => void moveMarkerToCoordinates(x, y)}
                    onSkip={() => void skipSelectedMarkerAndAdvance()}
                    onDelete={() => void deleteSelectedMarkerAndAdvance()}
                    onConfirm={() => void confirmSelectedMarkerAndAdvance()}
                  />
                )}

                <div className="grid grid-cols-2 gap-2">
                  <button type="button" className={toolbarButtonClass} disabled={isWorkspaceBusy} onClick={() => void nudgeMarker(-1, 0)}>
                    X -1
                  </button>
                  <button type="button" className={toolbarButtonClass} disabled={isWorkspaceBusy} onClick={() => void nudgeMarker(1, 0)}>
                    X +1
                  </button>
                  <button type="button" className={toolbarButtonClass} disabled={isWorkspaceBusy} onClick={() => void nudgeMarker(0, -1)}>
                    Y -1
                  </button>
                  <button type="button" className={toolbarButtonClass} disabled={isWorkspaceBusy} onClick={() => void nudgeMarker(0, 1)}>
                    Y +1
                  </button>
                  <button type="button" className={toolbarButtonClass} disabled={isWorkspaceBusy} onClick={() => void nudgeMarker(-10, 0)}>
                    X -10
                  </button>
                  <button type="button" className={toolbarButtonClass} disabled={isWorkspaceBusy} onClick={() => void nudgeMarker(10, 0)}>
                    X +10
                  </button>
                  <button type="button" className={toolbarButtonClass} disabled={isWorkspaceBusy} onClick={() => void nudgeMarker(0, -10)}>
                    Y -10
                  </button>
                  <button type="button" className={toolbarButtonClass} disabled={isWorkspaceBusy} onClick={() => void nudgeMarker(0, 10)}>
                    Y +10
                  </button>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#8f949d]">Лупа</span>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-white/10 bg-white/[0.03] text-sm font-semibold text-white transition"
                        onClick={() => setPrecisionZoomLevel((current) => Number(clamp(current - 0.5, 2, 12).toFixed(1)))}
                        aria-label="Уменьшить увеличение лупы"
                      >
                        −
                      </button>
                      <span className="min-w-[3rem] text-center text-[11px] font-semibold text-[#b5bac3]">
                        {formatZoomValue(precisionZoom)}x
                      </span>
                      <button
                        type="button"
                        className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-white/10 bg-white/[0.03] text-sm font-semibold text-white transition"
                        onClick={() => setPrecisionZoomLevel((current) => Number(clamp(current + 0.5, 2, 12).toFixed(1)))}
                        aria-label="Увеличить увеличение лупы"
                      >
                        +
                      </button>
                    </div>
                  </div>
                  <p className="text-[11px] text-[#9196a0]">клик по лупе двигает точку</p>
                  <div
                    className={classNames(
                      "relative block overflow-hidden rounded-[1rem] border border-[#30343c] bg-[#111317] shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]",
                      isWorkspaceBusy ? "opacity-50" : ""
                    )}
                    style={{
                      width: precisionLensSize,
                      height: precisionLensSize,
                      maxWidth: "100%",
                      backgroundImage: document ? `url(${resolveAssetUrl(document.storageUrl)})` : undefined,
                      backgroundSize: precisionBackgroundSize,
                      backgroundPosition: precisionBackgroundPosition,
                      backgroundRepeat: "no-repeat"
                    }}
                  >
                    <button
                      type="button"
                      aria-label="Лупа для точной доводки"
                      className="absolute inset-0 z-0 cursor-crosshair rounded-[inherit] disabled:cursor-not-allowed"
                      disabled={isWorkspaceBusy}
                      onClick={(event) => void handlePrecisionLensClick(event)}
                    />
                    <span className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.06)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.06)_1px,transparent_1px)] bg-[size:20px_20px]" />
                    <span className="pointer-events-none absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-[#ffffff66]" />
                    <span className="pointer-events-none absolute left-0 top-1/2 h-px w-full -translate-y-1/2 bg-[#ffffff66]" />
                    {precisionCandidates.map((candidate, index) => {
                      const left = precisionLensSize / 2 + (candidate.x - selectedMarker.x) * precisionZoom;
                      const top = precisionLensSize / 2 + (candidate.y - selectedMarker.y) * precisionZoom;
                      if (left < -10 || left > precisionLensSize + 10 || top < -10 || top > precisionLensSize + 10) {
                        return null;
                      }

                      return (
                        <button
                          key={`${candidate.source}-${index}`}
                          type="button"
                          aria-label={`Вариант ${index + 1}`}
                          className="absolute h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white bg-[#f59e0b] shadow-[0_4px_14px_rgba(245,158,11,0.45)] transition"
                          style={{ left, top }}
                          onClick={(event) => {
                            event.stopPropagation();
                            void moveMarkerToCoordinates(candidate.x, candidate.y);
                          }}
                        />
                      );
                    })}
                    <span
                      className={classNames(
                        "pointer-events-none absolute left-1/2 top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 border-2 border-white shadow-[0_2px_10px_rgba(15,23,42,0.45)]",
                        selectedMarker.pointType === "top_left" ? "rounded-[0.45rem] bg-[#16a34a]" : "rounded-full bg-[#d92d20]"
                      )}
                    />
                  </div>
                  {selectedMarker.status === "human_draft" && (
                    <button
                      type="button"
                      className="inline-flex h-9 w-full items-center justify-center rounded-[0.8rem] border border-[#3e5f2b] bg-[#1c2718] px-3 text-[12px] font-semibold text-[#d7f5c9] transition disabled:cursor-not-allowed disabled:opacity-35"
                      disabled={!draftLabel.trim() || isWorkspaceBusy}
                      onClick={() => void confirmSelectedMarker()}
                    >
                      Подтвердить по лупе
                    </button>
                  )}
                  {precisionCandidates.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {precisionCandidates.map((candidate, index) => (
                        <button
                          key={`candidate-chip-${candidate.source}-${index}`}
                          type="button"
                          className="inline-flex h-7 items-center rounded-full border border-white/10 bg-white/[0.03] px-2.5 text-[11px] font-medium text-[#d2d6dd] transition"
                          disabled={isWorkspaceBusy}
                          onClick={() => void moveMarkerToCoordinates(candidate.x, candidate.y)}
                        >
                          Вариант {index + 1}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-sm leading-6 text-[#9aa1ab]">
                {isImportedJobPreviewSession
                  ? "Выбери точку или спорное место слева. Если AI ничего не нашёл, поставь точку вручную на холсте."
                  : "Запусти авторазметку, потом выбери кандидата слева для review или готовую точку для правки."}
              </p>
            )}
          </section>

          <section className={railSectionClass}>
            <div className="space-y-3 text-sm text-[#c8ccd3]">
              {selectedMarker ? (
                <form
                  className="space-y-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void applyMarkerCoordinates();
                  }}
                >
                  <label className="flex items-center gap-3">
                    <span className="w-14 shrink-0 text-sm font-medium text-white">x -</span>
                    <input value={draftMarkerX} onChange={(event) => setDraftMarkerX(event.target.value)} inputMode="numeric" className={inspectorInputClass} />
                  </label>
                  <label className="flex items-center gap-3">
                    <span className="w-14 shrink-0 text-sm font-medium text-white">y -</span>
                    <input value={draftMarkerY} onChange={(event) => setDraftMarkerY(event.target.value)} inputMode="numeric" className={inspectorInputClass} />
                  </label>
                </form>
              ) : (
                <form
                  className="space-y-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    applyViewportFields();
                  }}
                >
                  <label className="flex items-center gap-3">
                    <span className="w-14 shrink-0 text-sm font-medium text-white">x -</span>
                    <input value={draftViewportX} onChange={(event) => setDraftViewportX(event.target.value)} inputMode="numeric" className={inspectorInputClass} />
                  </label>
                  <label className="flex items-center gap-3">
                    <span className="w-14 shrink-0 text-sm font-medium text-white">y -</span>
                    <input value={draftViewportY} onChange={(event) => setDraftViewportY(event.target.value)} inputMode="numeric" className={inspectorInputClass} />
                  </label>
                </form>
              )}

              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  applyViewportFields();
                }}
              >
                <label className="flex items-center gap-3">
                  <span className="w-14 shrink-0 text-sm font-medium text-white">zoom -</span>
                  <input value={draftViewportZoom} onChange={(event) => setDraftViewportZoom(event.target.value)} className={inspectorInputClass} />
                </label>
              </form>

              {selectedMarker && (
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    void saveMarkerChanges();
                  }}
                >
                  <label className="flex items-center gap-3">
                    <span className="w-14 shrink-0 text-sm font-medium text-white">ярлык -</span>
                    <input value={draftLabel} onChange={(event) => setDraftLabel(event.target.value)} className={inspectorInputClass} />
                  </label>
                </form>
              )}

              {selectedMarker && (
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    void saveMarkerPatch({ confidence: draftConfidence ? Number(draftConfidence) : null });
                  }}
                >
                  <label className="flex items-center gap-3">
                    <span className="w-24 shrink-0 text-sm font-medium text-white">confidence -</span>
                    <input
                      value={draftConfidence}
                      onChange={(event) => setDraftConfidence(event.target.value)}
                      onBlur={() => void saveMarkerPatch({ confidence: draftConfidence ? Number(draftConfidence) : null })}
                      placeholder="0.92"
                      className={inspectorInputClass}
                    />
                  </label>
                </form>
              )}

              {selectedMarker && selectedMarker.status === "human_draft" && (
                <div className="rounded-[0.95rem] border border-[#6d4a1a] bg-[#2a2118] px-3 py-2 text-[12px] text-[#f5d0a8]">
                  Точка пока черновая. Проверь место через лупу и потом подтверди.
                </div>
              )}
            </div>
          </section>
        </div>
      </aside>
      )}

      <div
        className="pointer-events-none absolute bottom-3 z-40"
        style={{ left: canvasLeftInset, right: canvasRightInset }}
      >
        <div className="scrollbar-hidden pointer-events-auto mx-auto flex max-w-full items-center gap-1.5 overflow-x-auto rounded-[1rem] border border-[#2f241d] bg-[#15110e] p-1.5 text-white shadow-[0_26px_70px_rgba(10,8,6,0.45)]">
          <div className="inline-flex items-center rounded-[0.95rem] bg-[#1f1814] p-1">
            <button
              type="button"
              className={classNames(toolbarSegmentClass, mode === "pan" && toolbarSegmentActiveClass)}
              onClick={() => setMode("pan")}
            >
              Рука
            </button>
            <button
              type="button"
              className={classNames(toolbarSegmentClass, mode === "place" && toolbarSegmentActiveClass)}
              onClick={() => setMode("place")}
            >
              Точка
            </button>
          </div>

          <div className={classNames(isCompactWorkspace ? "min-w-[132px] w-[132px]" : "min-w-[172px]")}>
            <CompactPointTypeSwitch value={placementPointType} onChange={setPlacementPointType} />
          </div>

          <button type="button" className={toolbarIconButtonClass} aria-label="Отдалить" onClick={() => applyZoom(0.85)}>
            −
          </button>
          <button type="button" className={toolbarIconButtonClass} aria-label="Приблизить" onClick={() => applyZoom(1.2)}>
            +
          </button>
          <button type="button" className={toolbarButtonClass} onClick={resetView}>
            Сброс вида
          </button>

          <button type="button" className={toolbarButtonClass} disabled={!session.markers.length || isWorkspaceBusy} onClick={() => void clearMarkers()}>
            Очистить
          </button>
          <button
            type="button"
            className={classNames(toolbarButtonClass, "text-[#fca5a5]")}
            disabled={!selectedMarker || isWorkspaceBusy}
            onClick={() => void deleteSelectedMarker()}
          >
            Удалить
          </button>
        </div>
      </div>
    </div>
  );
}
