import { describe, expect, it } from "vitest";

import type { CalloutCandidate, Marker } from "../../lib/types";
import {
  buildAmbiguityQueueViewModel,
  buildHistoryAlternateQueueAction,
  buildSelectedAmbiguityReviewViewModel,
  getNextMarkerIdInQueue,
  getPrimaryHistoryQueueContext,
  getQueueCountByContext
} from "./annotation-workspace-state";

const now = "2026-04-09T12:00:00.000Z";

function makeMarker(overrides: Partial<Marker> = {}): Marker {
  return {
    markerId: "marker-1",
    label: "A1",
    x: 100,
    y: 120,
    pointType: "center",
    status: "ai_review",
    confidence: null,
    createdBy: "ai",
    updatedBy: "ai",
    createdAt: now,
    updatedAt: now,
    ...overrides
  };
}

function makeCandidate(overrides: Partial<CalloutCandidate> = {}): CalloutCandidate {
  return {
    candidateId: "candidate-1",
    kind: "circle",
    centerX: 105,
    centerY: 125,
    bboxX: 90,
    bboxY: 110,
    bboxWidth: 30,
    bboxHeight: 30,
    score: 0.9,
    cropUrl: null,
    suggestedLabel: null,
    suggestedConfidence: null,
    suggestedSource: null,
    topologyScore: null,
    topologySource: null,
    leaderAnchorX: null,
    leaderAnchorY: null,
    reviewStatus: "pending",
    conflictGroup: null,
    conflictCount: 0,
    createdAt: now,
    updatedAt: now,
    ...overrides
  };
}

describe("annotation-workspace-state", () => {
  it("prioritizes linked ambiguity candidates and nearby same-label options", () => {
    const selectedMarker = makeMarker();
    const linkedCandidate = makeCandidate({
      candidateId: "candidate-linked",
      score: 0.82,
      suggestedLabel: "B2"
    });
    const sameLabelCandidate = makeCandidate({
      candidateId: "candidate-same-label",
      centerX: 180,
      centerY: 210,
      score: 0.77,
      suggestedLabel: "a1"
    });
    const farOtherCandidate = makeCandidate({
      candidateId: "candidate-far",
      centerX: 600,
      centerY: 650,
      score: 0.99,
      suggestedLabel: "Z9"
    });

    const review = buildSelectedAmbiguityReviewViewModel({
      selectedMarker,
      selectedMarkerAmbiguityConflicts: [
        {
          conflictId: "conflict-1",
          type: "association_ambiguity",
          severity: "warning",
          label: "A1",
          message: "shape/text спор",
          candidateIds: ["candidate-linked"],
          markerIds: [selectedMarker.markerId],
          relatedLabels: ["A1"],
          bboxX: null,
          bboxY: null,
          bboxWidth: null,
          bboxHeight: null
        }
      ],
      pendingCandidates: [linkedCandidate, sameLabelCandidate, farOtherCandidate],
      candidatesById: new Map([
        [linkedCandidate.candidateId, linkedCandidate],
        [sameLabelCandidate.candidateId, sameLabelCandidate],
        [farOtherCandidate.candidateId, farOtherCandidate]
      ]),
      candidateAssociationCountById: new Map([
        [linkedCandidate.candidateId, 2],
        [sameLabelCandidate.candidateId, 1]
      ]),
      draftLabel: "",
      showDeferredAmbiguityMarkersOnly: false
    });

    expect(review.reviewCandidates.map((item) => item.candidate.candidateId)).toEqual([
      "candidate-linked",
      "candidate-same-label"
    ]);
    expect(review.reviewMessages).toEqual(["shape/text спор"]);
    expect(review.reviewTypeLabels).toEqual(["shape↔text спор"]);
    expect(review.hasSelectedReview).toBe(true);
    expect(review.canConfirm).toBe(true);
    expect(review.canSkip).toBe(true);
  });

  it("disables skip in deferred pass and keeps review available with draft label", () => {
    const selectedMarker = makeMarker({ label: null });
    const review = buildSelectedAmbiguityReviewViewModel({
      selectedMarker,
      selectedMarkerAmbiguityConflicts: [
        {
          conflictId: "conflict-2",
          type: "candidate_ambiguity",
          severity: "warning",
          label: null,
          message: "спор кандидатов",
          candidateIds: [],
          markerIds: [selectedMarker.markerId],
          relatedLabels: [],
          bboxX: null,
          bboxY: null,
          bboxWidth: null,
          bboxHeight: null
        }
      ],
      pendingCandidates: [],
      candidatesById: new Map(),
      candidateAssociationCountById: new Map(),
      draftLabel: "D7",
      showDeferredAmbiguityMarkersOnly: true
    });

    expect(review.canConfirm).toBe(true);
    expect(review.canSkip).toBe(false);
    expect(review.reviewTypeLabels).toEqual(["спор кандидатов"]);
  });

  it("builds queue view model for active and all-marker modes", () => {
    const markers = [
      makeMarker({ markerId: "marker-1" }),
      makeMarker({ markerId: "marker-2" }),
      makeMarker({ markerId: "marker-3", status: "human_confirmed" })
    ];

    const reviewQueue = buildAmbiguityQueueViewModel({
      sessionMarkers: markers,
      selectedMarkerId: "marker-2",
      showAmbiguityMarkersOnly: true,
      showDeferredAmbiguityMarkersOnly: false,
      activeAmbiguityQueueMarkers: markers.slice(0, 2),
      currentPassCount: 2,
      deferredCount: 1
    });

    expect(reviewQueue.activeQueueLabel).toBe("review");
    expect(reviewQueue.displayedMarkers.map((marker) => marker.markerId)).toEqual(["marker-1", "marker-2"]);
    expect(reviewQueue.currentPosition).toBe(2);
    expect(reviewQueue.progress).toBe(1);
    expect(reviewQueue.hasUnresolvedMarkers).toBe(true);

    const allModeQueue = buildAmbiguityQueueViewModel({
      sessionMarkers: markers,
      selectedMarkerId: "marker-3",
      showAmbiguityMarkersOnly: false,
      showDeferredAmbiguityMarkersOnly: false,
      activeAmbiguityQueueMarkers: markers.slice(0, 2),
      currentPassCount: 2,
      deferredCount: 0
    });

    expect(allModeQueue.activeQueueLabel).toBe("рабочая очередь");
    expect(allModeQueue.displayedMarkers.map((marker) => marker.markerId)).toEqual(["marker-1", "marker-2", "marker-3"]);
    expect(allModeQueue.selectedQueueHint).toContain("рабочей review-очереди");
  });

  it("derives queue actions and counts for history resume", () => {
    expect(getPrimaryHistoryQueueContext(3, 2)).toBe("review");
    expect(getPrimaryHistoryQueueContext(0, 2)).toBe("deferred");
    expect(getPrimaryHistoryQueueContext(0, 0)).toBeNull();

    expect(getQueueCountByContext("review", 3, 2)).toBe(3);
    expect(getQueueCountByContext("deferred", 3, 2)).toBe(2);
    expect(getQueueCountByContext(null, 3, 2)).toBe(0);

    expect(
      buildHistoryAlternateQueueAction({
        showDeferredAmbiguityMarkersOnly: true,
        currentPassCount: 4,
        deferredCount: 1,
        toReviewLabel: "в review",
        toDeferredLabel: "в отложенные"
      })
    ).toEqual({
      mode: "current",
      label: "в review 4"
    });

    expect(
      buildHistoryAlternateQueueAction({
        showDeferredAmbiguityMarkersOnly: false,
        currentPassCount: 3,
        deferredCount: 2,
        toReviewLabel: "в review",
        toDeferredLabel: "в отложенные"
      })
    ).toEqual({
      mode: "deferred",
      label: "в отложенные 2"
    });
  });

  it("returns next marker id in circular queue order", () => {
    const queue = [makeMarker({ markerId: "m1" }), makeMarker({ markerId: "m2" }), makeMarker({ markerId: "m3" })];

    expect(getNextMarkerIdInQueue(queue, "m1")).toBe("m2");
    expect(getNextMarkerIdInQueue(queue, "m3")).toBe("m1");
    expect(getNextMarkerIdInQueue(queue, "unknown")).toBeNull();
    expect(getNextMarkerIdInQueue([makeMarker({ markerId: "solo" })], "solo")).toBeNull();
  });

  it("builds deferred-only queue state and empty unresolved state", () => {
    const markers = [makeMarker({ markerId: "marker-deferred" })];

    const deferredQueue = buildAmbiguityQueueViewModel({
      sessionMarkers: markers,
      selectedMarkerId: "marker-deferred",
      showAmbiguityMarkersOnly: true,
      showDeferredAmbiguityMarkersOnly: true,
      activeAmbiguityQueueMarkers: markers,
      currentPassCount: 0,
      deferredCount: 1
    });

    expect(deferredQueue.activeQueueLabel).toBe("отложенные");
    expect(deferredQueue.selectedQueueTitle).toBe("Отложенный проход");
    expect(deferredQueue.selectedQueueHint).toContain("отложенных ambiguity-кейсов");
    expect(deferredQueue.hasUnresolvedMarkers).toBe(true);

    const emptyQueue = buildAmbiguityQueueViewModel({
      sessionMarkers: [],
      selectedMarkerId: null,
      showAmbiguityMarkersOnly: false,
      showDeferredAmbiguityMarkersOnly: false,
      activeAmbiguityQueueMarkers: [],
      currentPassCount: 0,
      deferredCount: 0
    });

    expect(emptyQueue.firstMarkerId).toBeNull();
    expect(emptyQueue.progress).toBe(0);
    expect(emptyQueue.hasUnresolvedMarkers).toBe(false);
  });

  it("does not create alternate history action when there is no other queue", () => {
    expect(
      buildHistoryAlternateQueueAction({
        showDeferredAmbiguityMarkersOnly: false,
        currentPassCount: 1,
        deferredCount: 0,
        toReviewLabel: "в review",
        toDeferredLabel: "в отложенные"
      })
    ).toBeNull();

    expect(
      buildHistoryAlternateQueueAction({
        showDeferredAmbiguityMarkersOnly: true,
        currentPassCount: 0,
        deferredCount: 2,
        toReviewLabel: "в review",
        toDeferredLabel: "в отложенные"
      })
    ).toBeNull();
  });
});
