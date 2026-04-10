export type TimestampIso = string;

export type SessionState = "draft" | "ready";

export type Actor = "human" | "ai" | "system";

export type MarkerStatus =
  | "human_draft"
  | "ai_detected"
  | "ai_review"
  | "human_confirmed"
  | "human_corrected"
  | "rejected";

export type MarkerPointType = "center" | "top_left";

export type CandidateKind = "circle" | "box" | "text";

export type CandidateReviewStatus = "pending" | "accepted" | "rejected";

export type PipelineConflictType =
  | "candidate_ambiguity"
  | "association_ambiguity"
  | "duplicate_label_nearby"
  | "missing_vocab_label";

export type PipelineConflictSeverity = "warning" | "error";

export type SessionCommandType =
  | "set_viewport"
  | "pan_viewport"
  | "zoom_to_region"
  | "place_marker"
  | "move_marker"
  | "update_marker"
  | "confirm_marker"
  | "reject_marker"
  | "delete_marker"
  | "clear_markers"
  | "reject_candidate";

export type ActionType =
  | "session_created"
  | "document_uploaded"
  | "candidates_detected"
  | "auto_annotation_completed"
  | "viewport_set"
  | "viewport_panned"
  | "viewport_zoomed"
  | "candidate_rejected"
  | "marker_created"
  | "marker_moved"
  | "marker_updated"
  | "marker_confirmed"
  | "marker_rejected"
  | "marker_deleted"
  | "markers_cleared";

export interface DocumentAsset {
  documentId: string;
  fileName: string;
  contentType: string;
  sizeBytes: number;
  width: number;
  height: number;
  storageUrl: string;
  uploadedAt: TimestampIso;
}

export interface Viewport {
  centerX: number;
  centerY: number;
  zoom: number;
}

export interface Marker {
  markerId: string;
  label: string | null;
  x: number;
  y: number;
  pointType: MarkerPointType;
  status: MarkerStatus;
  confidence: number | null;
  createdBy: Actor;
  updatedBy: Actor;
  createdAt: TimestampIso;
  updatedAt: TimestampIso;
}

export interface CalloutCandidate {
  candidateId: string;
  kind: CandidateKind;
  centerX: number;
  centerY: number;
  bboxX: number;
  bboxY: number;
  bboxWidth: number;
  bboxHeight: number;
  score: number;
  cropUrl: string | null;
  suggestedLabel: string | null;
  suggestedConfidence: number | null;
  suggestedSource: string | null;
  topologyScore: number | null;
  topologySource: string | null;
  leaderAnchorX: number | null;
  leaderAnchorY: number | null;
  reviewStatus: CandidateReviewStatus;
  conflictGroup: string | null;
  conflictCount: number;
  createdAt: TimestampIso;
  updatedAt: TimestampIso;
}

export interface CandidateAssociation {
  associationId: string;
  shapeCandidateId: string;
  textCandidateId: string;
  shapeKind: CandidateKind;
  label: string;
  score: number;
  geometryScore: number;
  topologyScore: number | null;
  source: string;
  leaderAnchorX: number | null;
  leaderAnchorY: number | null;
  bboxX: number | null;
  bboxY: number | null;
  bboxWidth: number | null;
  bboxHeight: number | null;
}

export interface PageVocabularyEntry {
  label: string;
  normalizedLabel: string;
  occurrences: number;
  maxConfidence: number | null;
  sources: string[];
  bboxX: number | null;
  bboxY: number | null;
  bboxWidth: number | null;
  bboxHeight: number | null;
}

export interface PipelineConflict {
  conflictId: string;
  type: PipelineConflictType;
  severity: PipelineConflictSeverity;
  label: string | null;
  message: string;
  candidateIds: string[];
  markerIds: string[];
  relatedLabels: string[];
  bboxX: number | null;
  bboxY: number | null;
  bboxWidth: number | null;
  bboxHeight: number | null;
}

export interface ActionLogEntry {
  actionId: string;
  actor: Actor;
  type: ActionType;
  createdAt: TimestampIso;
  payload: Record<string, unknown>;
}

export interface SessionSummary {
  totalMarkers: number;
  aiDetected: number;
  aiReview: number;
  humanConfirmed: number;
  humanCorrected: number;
  rejected: number;
}

export interface AnnotationSession {
  sessionId: string;
  title: string;
  state: SessionState;
  document: DocumentAsset | null;
  viewport: Viewport;
  candidates: CalloutCandidate[];
  candidateAssociations: CandidateAssociation[];
  pageVocabulary: PageVocabularyEntry[];
  missingLabels: string[];
  pipelineConflicts: PipelineConflict[];
  markers: Marker[];
  actionLog: ActionLogEntry[];
  summary: SessionSummary;
  createdAt: TimestampIso;
  updatedAt: TimestampIso;
}

export interface SessionListItem {
  sessionId: string;
  title: string;
  state: SessionState;
  documentName: string | null;
  markerCount: number;
  updatedAt: TimestampIso;
}

export interface CreateSessionRequest {
  title?: string;
}

export interface CreateSessionResponse {
  session: AnnotationSession;
}

export interface SessionListResponse {
  sessions: SessionListItem[];
}

export interface UploadDocumentResponse {
  session: AnnotationSession;
}

export interface SessionCommandRequest {
  type: SessionCommandType;
  actor?: Actor;
  markerId?: string;
  candidateId?: string;
  label?: string | null;
  pointType?: MarkerPointType;
  status?: MarkerStatus;
  confidence?: number | null;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  centerX?: number;
  centerY?: number;
  zoom?: number;
  deltaX?: number;
  deltaY?: number;
}

export interface SessionCommandResponse {
  session: AnnotationSession;
}
