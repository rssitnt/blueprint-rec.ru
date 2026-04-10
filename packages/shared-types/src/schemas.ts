export type JsonSchema = Record<string, unknown>;

export const markerStatusSchema: JsonSchema = {
  $id: "MarkerStatus",
  type: "string",
  enum: ["human_draft", "ai_detected", "ai_review", "human_confirmed", "human_corrected", "rejected"],
};

export const markerPointTypeSchema: JsonSchema = {
  $id: "MarkerPointType",
  type: "string",
  enum: ["center", "top_left"],
};

export const candidateKindSchema: JsonSchema = {
  $id: "CandidateKind",
  type: "string",
  enum: ["circle", "box", "text"],
};

export const candidateReviewStatusSchema: JsonSchema = {
  $id: "CandidateReviewStatus",
  type: "string",
  enum: ["pending", "accepted", "rejected"],
};

export const pipelineConflictTypeSchema: JsonSchema = {
  $id: "PipelineConflictType",
  type: "string",
  enum: ["candidate_ambiguity", "association_ambiguity", "duplicate_label_nearby", "missing_vocab_label"],
};

export const pipelineConflictSeveritySchema: JsonSchema = {
  $id: "PipelineConflictSeverity",
  type: "string",
  enum: ["warning", "error"],
};

export const sessionCommandTypeSchema: JsonSchema = {
  $id: "SessionCommandType",
  type: "string",
  enum: [
    "set_viewport",
    "pan_viewport",
    "zoom_to_region",
    "place_marker",
    "move_marker",
    "update_marker",
    "confirm_marker",
    "reject_marker",
    "delete_marker",
    "clear_markers",
    "reject_candidate",
  ],
};

export const actorSchema: JsonSchema = {
  $id: "Actor",
  type: "string",
  enum: ["human", "ai", "system"],
};

export const viewportSchema: JsonSchema = {
  $id: "Viewport",
  type: "object",
  required: ["centerX", "centerY", "zoom"],
  properties: {
    centerX: { type: "number" },
    centerY: { type: "number" },
    zoom: { type: "number", minimum: 0.1 },
  },
  additionalProperties: false,
};

export const documentAssetSchema: JsonSchema = {
  $id: "DocumentAsset",
  type: "object",
  required: ["documentId", "fileName", "contentType", "sizeBytes", "width", "height", "storageUrl", "uploadedAt"],
  properties: {
    documentId: { type: "string" },
    fileName: { type: "string" },
    contentType: { type: "string" },
    sizeBytes: { type: "integer", minimum: 1 },
    width: { type: "integer", minimum: 1 },
    height: { type: "integer", minimum: 1 },
    storageUrl: { type: "string" },
    uploadedAt: { type: "string", format: "date-time" },
  },
  additionalProperties: false,
};

export const markerSchema: JsonSchema = {
  $id: "Marker",
  type: "object",
  required: [
    "markerId",
    "label",
    "x",
    "y",
    "pointType",
    "status",
    "confidence",
    "createdBy",
    "updatedBy",
    "createdAt",
    "updatedAt",
  ],
  properties: {
    markerId: { type: "string" },
    label: { type: ["string", "null"] },
    x: { type: "number", minimum: 0 },
    y: { type: "number", minimum: 0 },
    pointType: { $ref: "MarkerPointType" },
    status: { $ref: "MarkerStatus" },
    confidence: { type: ["number", "null"], minimum: 0, maximum: 1 },
    createdBy: { $ref: "Actor" },
    updatedBy: { $ref: "Actor" },
    createdAt: { type: "string", format: "date-time" },
    updatedAt: { type: "string", format: "date-time" },
  },
  additionalProperties: false,
};

export const calloutCandidateSchema: JsonSchema = {
  $id: "CalloutCandidate",
  type: "object",
  required: [
    "candidateId",
    "kind",
    "centerX",
    "centerY",
    "bboxX",
    "bboxY",
    "bboxWidth",
    "bboxHeight",
    "score",
    "cropUrl",
    "suggestedLabel",
    "suggestedConfidence",
    "suggestedSource",
    "topologyScore",
    "topologySource",
    "leaderAnchorX",
    "leaderAnchorY",
    "reviewStatus",
    "conflictGroup",
    "conflictCount",
    "createdAt",
    "updatedAt",
  ],
  properties: {
    candidateId: { type: "string" },
    kind: { $ref: "CandidateKind" },
    centerX: { type: "number", minimum: 0 },
    centerY: { type: "number", minimum: 0 },
    bboxX: { type: "number", minimum: 0 },
    bboxY: { type: "number", minimum: 0 },
    bboxWidth: { type: "number", exclusiveMinimum: 0 },
    bboxHeight: { type: "number", exclusiveMinimum: 0 },
    score: { type: "number", minimum: 0 },
    cropUrl: { type: ["string", "null"] },
    suggestedLabel: { type: ["string", "null"] },
    suggestedConfidence: { type: ["number", "null"], minimum: 0, maximum: 1 },
    suggestedSource: { type: ["string", "null"] },
    topologyScore: { type: ["number", "null"], minimum: 0, maximum: 1 },
    topologySource: { type: ["string", "null"] },
    leaderAnchorX: { type: ["number", "null"], minimum: 0 },
    leaderAnchorY: { type: ["number", "null"], minimum: 0 },
    reviewStatus: { $ref: "CandidateReviewStatus" },
    conflictGroup: { type: ["string", "null"] },
    conflictCount: { type: "integer", minimum: 0 },
    createdAt: { type: "string", format: "date-time" },
    updatedAt: { type: "string", format: "date-time" },
  },
  additionalProperties: false,
};

export const candidateAssociationSchema: JsonSchema = {
  $id: "CandidateAssociation",
  type: "object",
  required: [
    "associationId",
    "shapeCandidateId",
    "textCandidateId",
    "shapeKind",
    "label",
    "score",
    "geometryScore",
    "topologyScore",
    "source",
    "leaderAnchorX",
    "leaderAnchorY",
    "bboxX",
    "bboxY",
    "bboxWidth",
    "bboxHeight",
  ],
  properties: {
    associationId: { type: "string" },
    shapeCandidateId: { type: "string" },
    textCandidateId: { type: "string" },
    shapeKind: { $ref: "CandidateKind" },
    label: { type: "string" },
    score: { type: "number", minimum: 0, maximum: 1 },
    geometryScore: { type: "number", minimum: 0, maximum: 1 },
    topologyScore: { type: ["number", "null"], minimum: 0, maximum: 1 },
    source: { type: "string" },
    leaderAnchorX: { type: ["number", "null"], minimum: 0 },
    leaderAnchorY: { type: ["number", "null"], minimum: 0 },
    bboxX: { type: ["number", "null"], minimum: 0 },
    bboxY: { type: ["number", "null"], minimum: 0 },
    bboxWidth: { type: ["number", "null"], minimum: 0 },
    bboxHeight: { type: ["number", "null"], minimum: 0 },
  },
  additionalProperties: false,
};

export const pageVocabularyEntrySchema: JsonSchema = {
  $id: "PageVocabularyEntry",
  type: "object",
  required: [
    "label",
    "normalizedLabel",
    "occurrences",
    "maxConfidence",
    "sources",
    "bboxX",
    "bboxY",
    "bboxWidth",
    "bboxHeight",
  ],
  properties: {
    label: { type: "string" },
    normalizedLabel: { type: "string" },
    occurrences: { type: "integer", minimum: 0 },
    maxConfidence: { type: ["number", "null"], minimum: 0, maximum: 1 },
    sources: { type: "array", items: { type: "string" } },
    bboxX: { type: ["number", "null"], minimum: 0 },
    bboxY: { type: ["number", "null"], minimum: 0 },
    bboxWidth: { type: ["number", "null"], minimum: 0 },
    bboxHeight: { type: ["number", "null"], minimum: 0 },
  },
  additionalProperties: false,
};

export const pipelineConflictSchema: JsonSchema = {
  $id: "PipelineConflict",
  type: "object",
  required: [
    "conflictId",
    "type",
    "severity",
    "label",
    "message",
    "candidateIds",
    "markerIds",
    "relatedLabels",
    "bboxX",
    "bboxY",
    "bboxWidth",
    "bboxHeight",
  ],
  properties: {
    conflictId: { type: "string" },
    type: { $ref: "PipelineConflictType" },
    severity: { $ref: "PipelineConflictSeverity" },
    label: { type: ["string", "null"] },
    message: { type: "string" },
    candidateIds: { type: "array", items: { type: "string" } },
    markerIds: { type: "array", items: { type: "string" } },
    relatedLabels: { type: "array", items: { type: "string" } },
    bboxX: { type: ["number", "null"], minimum: 0 },
    bboxY: { type: ["number", "null"], minimum: 0 },
    bboxWidth: { type: ["number", "null"], minimum: 0 },
    bboxHeight: { type: ["number", "null"], minimum: 0 },
  },
  additionalProperties: false,
};

export const actionLogEntrySchema: JsonSchema = {
  $id: "ActionLogEntry",
  type: "object",
  required: ["actionId", "actor", "type", "createdAt", "payload"],
  properties: {
    actionId: { type: "string" },
    actor: { $ref: "Actor" },
    type: { type: "string" },
    createdAt: { type: "string", format: "date-time" },
    payload: { type: "object" },
  },
  additionalProperties: false,
};

export const sessionSummarySchema: JsonSchema = {
  $id: "SessionSummary",
  type: "object",
  required: ["totalMarkers", "aiDetected", "aiReview", "humanConfirmed", "humanCorrected", "rejected"],
  properties: {
    totalMarkers: { type: "integer", minimum: 0 },
    aiDetected: { type: "integer", minimum: 0 },
    aiReview: { type: "integer", minimum: 0 },
    humanConfirmed: { type: "integer", minimum: 0 },
    humanCorrected: { type: "integer", minimum: 0 },
    rejected: { type: "integer", minimum: 0 },
  },
  additionalProperties: false,
};

export const annotationSessionSchema: JsonSchema = {
  $id: "AnnotationSession",
  type: "object",
  required: [
    "sessionId",
    "title",
    "state",
    "document",
    "viewport",
    "candidates",
    "candidateAssociations",
    "pageVocabulary",
    "missingLabels",
    "pipelineConflicts",
    "markers",
    "actionLog",
    "summary",
    "createdAt",
    "updatedAt",
  ],
  properties: {
    sessionId: { type: "string" },
    title: { type: "string" },
    state: { type: "string", enum: ["draft", "ready"] },
    document: { anyOf: [{ $ref: "DocumentAsset" }, { type: "null" }] },
    viewport: { $ref: "Viewport" },
    candidates: { type: "array", items: { $ref: "CalloutCandidate" } },
    candidateAssociations: { type: "array", items: { $ref: "CandidateAssociation" } },
    pageVocabulary: { type: "array", items: { $ref: "PageVocabularyEntry" } },
    missingLabels: { type: "array", items: { type: "string" } },
    pipelineConflicts: { type: "array", items: { $ref: "PipelineConflict" } },
    markers: { type: "array", items: { $ref: "Marker" } },
    actionLog: { type: "array", items: { $ref: "ActionLogEntry" } },
    summary: { $ref: "SessionSummary" },
    createdAt: { type: "string", format: "date-time" },
    updatedAt: { type: "string", format: "date-time" },
  },
  additionalProperties: false,
};

export const apiContracts = {
  CreateSessionResponse: {
    $id: "CreateSessionResponse",
    type: "object",
    required: ["session"],
    properties: {
      session: { $ref: "AnnotationSession" },
    },
    additionalProperties: false,
  },
  SessionListResponse: {
    $id: "SessionListResponse",
    type: "object",
    required: ["sessions"],
    properties: {
      sessions: {
        type: "array",
        items: {
          type: "object",
          required: ["sessionId", "title", "state", "documentName", "markerCount", "updatedAt"],
          properties: {
            sessionId: { type: "string" },
            title: { type: "string" },
            state: { type: "string", enum: ["draft", "ready"] },
            documentName: { type: ["string", "null"] },
            markerCount: { type: "integer", minimum: 0 },
            updatedAt: { type: "string", format: "date-time" },
          },
          additionalProperties: false,
        },
      },
    },
    additionalProperties: false,
  },
  UploadDocumentResponse: {
    $id: "UploadDocumentResponse",
    type: "object",
    required: ["session"],
    properties: {
      session: { $ref: "AnnotationSession" },
    },
    additionalProperties: false,
  },
  SessionCommandResponse: {
    $id: "SessionCommandResponse",
    type: "object",
    required: ["session"],
    properties: {
      session: { $ref: "AnnotationSession" },
    },
    additionalProperties: false,
  },
};
