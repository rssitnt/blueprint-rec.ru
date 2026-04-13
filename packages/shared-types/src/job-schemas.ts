import type { JsonSchema } from "./schemas";

export const drawingJobStatusSchema: JsonSchema = {
  $id: "DrawingJobStatus",
  type: "string",
  enum: ["queued", "running", "completed", "failed"],
};

export const drawingResultRowStatusSchema: JsonSchema = {
  $id: "DrawingResultRowStatus",
  type: "string",
  enum: ["found", "not_found", "uncertain"],
};

export const resultPointSchema: JsonSchema = {
  $id: "ResultPoint",
  type: "object",
  required: ["x", "y"],
  properties: {
    x: { type: "number", minimum: 0 },
    y: { type: "number", minimum: 0 },
  },
  additionalProperties: false,
};

export const resultBoundingBoxSchema: JsonSchema = {
  $id: "ResultBoundingBox",
  type: "object",
  required: ["x", "y", "w", "h"],
  properties: {
    x: { type: "number", minimum: 0 },
    y: { type: "number", minimum: 0 },
    w: { type: "number", exclusiveMinimum: 0 },
    h: { type: "number", exclusiveMinimum: 0 },
  },
  additionalProperties: false,
};

export const drawingJobInputSchema: JsonSchema = {
  $id: "DrawingJobInput",
  type: "object",
  required: ["drawingName", "drawingUrl", "labelsName", "labelsUrl", "hasLabels"],
  properties: {
    drawingName: { type: "string" },
    drawingUrl: { type: "string" },
    labelsName: { type: ["string", "null"] },
    labelsUrl: { type: ["string", "null"] },
    hasLabels: { type: "boolean" },
  },
  additionalProperties: false,
};

export const drawingResultPageSchema: JsonSchema = {
  $id: "DrawingResultPage",
  type: "object",
  required: ["pageIndex", "width", "height", "overlayUrl", "rowCount", "heldBackCount"],
  properties: {
    pageIndex: { type: "integer", minimum: 0 },
    width: { type: "integer", minimum: 1 },
    height: { type: "integer", minimum: 1 },
    overlayUrl: { type: ["string", "null"] },
    rowCount: { type: "integer", minimum: 0 },
    heldBackCount: { type: "integer", minimum: 0 },
  },
  additionalProperties: false,
};

export const drawingResultRowSchema: JsonSchema = {
  $id: "DrawingResultRow",
  type: "object",
  required: ["row", "label", "pageIndex", "center", "topLeft", "bbox", "finalScore", "status", "note", "sourceKind"],
  properties: {
    row: { type: "integer", minimum: 1 },
    label: { type: "string" },
    pageIndex: { type: "integer", minimum: 0 },
    center: { anyOf: [{ $ref: "ResultPoint" }, { type: "null" }] },
    topLeft: { anyOf: [{ $ref: "ResultPoint" }, { type: "null" }] },
    bbox: { anyOf: [{ $ref: "ResultBoundingBox" }, { type: "null" }] },
    finalScore: { type: ["number", "null"], minimum: 0, maximum: 1 },
    status: { $ref: "DrawingResultRowStatus" },
    note: { type: ["string", "null"] },
    sourceKind: { type: ["string", "null"] },
  },
  additionalProperties: false,
};

export const drawingJobArtifactsSchema: JsonSchema = {
  $id: "DrawingJobArtifacts",
  type: "object",
  required: [
    "overlayUrl",
    "csvUrl",
    "xlsxUrl",
    "zipUrl",
    "reviewCsvUrl",
    "reviewXlsxUrl",
    "reviewZipUrl",
    "nearTieCsvUrl",
    "nearTieJsonUrl",
    "sourceJsonUrl",
    "resultJsonUrl",
  ],
  properties: {
    overlayUrl: { type: ["string", "null"] },
    csvUrl: { type: ["string", "null"] },
    xlsxUrl: { type: ["string", "null"] },
    zipUrl: { type: ["string", "null"] },
    reviewCsvUrl: { type: ["string", "null"] },
    reviewXlsxUrl: { type: ["string", "null"] },
    reviewZipUrl: { type: ["string", "null"] },
    nearTieCsvUrl: { type: ["string", "null"] },
    nearTieJsonUrl: { type: ["string", "null"] },
    sourceJsonUrl: { type: ["string", "null"] },
    resultJsonUrl: { type: ["string", "null"] },
  },
  additionalProperties: false,
};

export const drawingJobSummarySchema: JsonSchema = {
  $id: "DrawingJobSummary",
  type: "object",
  required: [
    "totalRows",
    "foundCount",
    "missingCount",
    "uncertainCount",
    "heldBackCount",
    "nearTieAmbiguityCount",
    "discardedCount",
    "documentConfidence",
    "degradedRecognition",
    "degradedReason",
    "selectedOcrEngine",
    "fallbackUsed",
    "fallbackAttempted",
    "fallbackFailureCount",
    "emergencyFallbackUsed",
    "emergencyFallbackReason",
    "reviewRecommended",
    "reviewReasons",
    "statusText",
    "failureMessage",
  ],
  properties: {
    totalRows: { type: "integer", minimum: 0 },
    foundCount: { type: "integer", minimum: 0 },
    missingCount: { type: "integer", minimum: 0 },
    uncertainCount: { type: "integer", minimum: 0 },
    heldBackCount: { type: "integer", minimum: 0 },
    nearTieAmbiguityCount: { type: "integer", minimum: 0 },
    discardedCount: { type: "integer", minimum: 0 },
    documentConfidence: { type: ["number", "null"], minimum: 0, maximum: 1 },
    degradedRecognition: { type: "boolean" },
    degradedReason: { type: ["string", "null"] },
    selectedOcrEngine: { type: ["string", "null"] },
    fallbackUsed: { type: "boolean" },
    fallbackAttempted: { type: "boolean" },
    fallbackFailureCount: { type: "integer", minimum: 0 },
    emergencyFallbackUsed: { type: "boolean" },
    emergencyFallbackReason: { type: ["string", "null"] },
    reviewRecommended: { type: "boolean" },
    reviewReasons: { type: "array", items: { type: "string" } },
    statusText: { type: "string" },
    failureMessage: { type: ["string", "null"] },
  },
  additionalProperties: false,
};

export const drawingJobResultSchema: JsonSchema = {
  $id: "DrawingJobResult",
  type: "object",
  required: [
    "sourceFile",
    "sourceLabelsFile",
    "pages",
    "rows",
    "heldBackRows",
    "missingLabels",
    "extraDetectedLabels",
    "summary",
    "artifacts",
  ],
  properties: {
    sourceFile: { type: "string" },
    sourceLabelsFile: { type: ["string", "null"] },
    pages: { type: "array", items: { $ref: "DrawingResultPage" } },
    rows: { type: "array", items: { $ref: "DrawingResultRow" } },
    heldBackRows: { type: "array", items: { $ref: "DrawingResultRow" } },
    missingLabels: { type: "array", items: { type: "string" } },
    extraDetectedLabels: { type: "array", items: { type: "string" } },
    summary: { $ref: "DrawingJobSummary" },
    artifacts: { $ref: "DrawingJobArtifacts" },
  },
  additionalProperties: false,
};

export const drawingJobSchema: JsonSchema = {
  $id: "DrawingJob",
  type: "object",
  required: ["jobId", "title", "status", "input", "errorMessage", "result", "createdAt", "updatedAt"],
  properties: {
    jobId: { type: "string" },
    title: { type: "string" },
    status: { $ref: "DrawingJobStatus" },
    input: { $ref: "DrawingJobInput" },
    errorMessage: { type: ["string", "null"] },
    result: { anyOf: [{ $ref: "DrawingJobResult" }, { type: "null" }] },
    createdAt: { type: "string", format: "date-time" },
    updatedAt: { type: "string", format: "date-time" },
  },
  additionalProperties: false,
};

export const jobListItemSchema: JsonSchema = {
  $id: "JobListItem",
  type: "object",
  required: [
    "jobId",
    "title",
    "status",
    "drawingName",
    "labelsName",
    "documentConfidence",
    "degradedRecognition",
    "degradedReason",
    "emergencyFallbackUsed",
    "errorMessage",
    "createdAt",
    "updatedAt",
  ],
  properties: {
    jobId: { type: "string" },
    title: { type: "string" },
    status: { $ref: "DrawingJobStatus" },
    drawingName: { type: "string" },
    labelsName: { type: ["string", "null"] },
    documentConfidence: { type: ["number", "null"], minimum: 0, maximum: 1 },
    degradedRecognition: { type: "boolean" },
    degradedReason: { type: ["string", "null"] },
    emergencyFallbackUsed: { type: "boolean" },
    errorMessage: { type: ["string", "null"] },
    createdAt: { type: "string", format: "date-time" },
    updatedAt: { type: "string", format: "date-time" },
  },
  additionalProperties: false,
};

export const jobContracts = {
  CreateJobResponse: {
    $id: "CreateJobResponse",
    type: "object",
    required: ["job"],
    properties: {
      job: { $ref: "DrawingJob" },
    },
    additionalProperties: false,
  },
  JobListResponse: {
    $id: "JobListResponse",
    type: "object",
    required: ["jobs"],
    properties: {
      jobs: {
        type: "array",
        items: { $ref: "JobListItem" },
      },
    },
    additionalProperties: false,
  },
  BatchJobWarning: {
    $id: "BatchJobWarning",
    type: "object",
    required: ["code", "message", "fileName", "baseName"],
    properties: {
      code: { type: "string" },
      message: { type: "string" },
      fileName: { type: ["string", "null"] },
      baseName: { type: ["string", "null"] },
    },
    additionalProperties: false,
  },
  CreateBatchJobsResponse: {
    $id: "CreateBatchJobsResponse",
    type: "object",
    required: ["batchId", "jobs", "warnings"],
    properties: {
      batchId: { type: ["string", "null"] },
      jobs: {
        type: "array",
        items: { $ref: "DrawingJob" },
      },
      warnings: {
        type: "array",
        items: { $ref: "BatchJobWarning" },
      },
    },
    additionalProperties: false,
  },
  BatchSummary: {
    $id: "BatchSummary",
    type: "object",
    required: ["totalJobs", "queuedJobs", "runningJobs", "completedJobs", "failedJobs", "degradedJobs", "rescuedJobs", "finished"],
    properties: {
      totalJobs: { type: "integer", minimum: 0 },
      queuedJobs: { type: "integer", minimum: 0 },
      runningJobs: { type: "integer", minimum: 0 },
      completedJobs: { type: "integer", minimum: 0 },
      failedJobs: { type: "integer", minimum: 0 },
      degradedJobs: { type: "integer", minimum: 0 },
      rescuedJobs: { type: "integer", minimum: 0 },
      finished: { type: "boolean" },
    },
    additionalProperties: false,
  },
  DrawingJobBatch: {
    $id: "DrawingJobBatch",
    type: "object",
    required: ["batchId", "title", "archiveName", "titlePrefix", "jobIds", "warnings", "createdAt", "updatedAt", "summary"],
    properties: {
      batchId: { type: "string" },
      title: { type: "string" },
      archiveName: { type: "string" },
      titlePrefix: { type: ["string", "null"] },
      jobIds: { type: "array", items: { type: "string" } },
      warnings: { type: "array", items: { $ref: "BatchJobWarning" } },
      createdAt: { type: "string", format: "date-time" },
      updatedAt: { type: "string", format: "date-time" },
      summary: { $ref: "BatchSummary" },
    },
    additionalProperties: false,
  },
  BatchListItem: {
    $id: "BatchListItem",
    type: "object",
    required: ["batchId", "title", "archiveName", "jobCount", "warningCount", "createdAt", "updatedAt", "summary"],
    properties: {
      batchId: { type: "string" },
      title: { type: "string" },
      archiveName: { type: "string" },
      jobCount: { type: "integer", minimum: 0 },
      warningCount: { type: "integer", minimum: 0 },
      createdAt: { type: "string", format: "date-time" },
      updatedAt: { type: "string", format: "date-time" },
      summary: { $ref: "BatchSummary" },
    },
    additionalProperties: false,
  },
  BatchListResponse: {
    $id: "BatchListResponse",
    type: "object",
    required: ["batches"],
    properties: {
      batches: {
        type: "array",
        items: { $ref: "BatchListItem" },
      },
    },
    additionalProperties: false,
  },
  BatchResponse: {
    $id: "BatchResponse",
    type: "object",
    required: ["batch", "jobs"],
    properties: {
      batch: { $ref: "DrawingJobBatch" },
      jobs: {
        type: "array",
        items: { $ref: "JobListItem" },
      },
    },
    additionalProperties: false,
  },
  PreviewSessionResponse: {
    $id: "PreviewSessionResponse",
    type: "object",
    required: ["sessionId"],
    properties: {
      sessionId: { type: "string" },
    },
    additionalProperties: false,
  },
};
