import type { TimestampIso } from "./types";

export type DrawingJobStatus = "queued" | "running" | "completed" | "failed";

export type DrawingResultRowStatus = "found" | "not_found" | "uncertain";

export interface ResultPoint {
  x: number;
  y: number;
}

export interface ResultBoundingBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DrawingJobInput {
  drawingName: string;
  drawingUrl: string;
  labelsName: string | null;
  labelsUrl: string | null;
  hasLabels: boolean;
}

export interface DrawingResultPage {
  pageIndex: number;
  width: number;
  height: number;
  overlayUrl: string | null;
  rowCount: number;
  heldBackCount: number;
}

export interface DrawingResultRow {
  row: number;
  label: string;
  pageIndex: number;
  center: ResultPoint | null;
  topLeft: ResultPoint | null;
  bbox: ResultBoundingBox | null;
  finalScore: number | null;
  status: DrawingResultRowStatus;
  note: string | null;
  sourceKind: string | null;
}

export interface DrawingJobArtifacts {
  overlayUrl: string | null;
  csvUrl: string | null;
  xlsxUrl: string | null;
  zipUrl: string | null;
  reviewCsvUrl: string | null;
  reviewXlsxUrl: string | null;
  reviewZipUrl: string | null;
  nearTieCsvUrl: string | null;
  nearTieJsonUrl: string | null;
  sourceJsonUrl: string | null;
  resultJsonUrl: string | null;
}

export interface DrawingJobSummary {
  totalRows: number;
  foundCount: number;
  missingCount: number;
  uncertainCount: number;
  heldBackCount: number;
  nearTieAmbiguityCount: number;
  discardedCount: number;
  documentConfidence: number | null;
  degradedRecognition: boolean;
  degradedReason: string | null;
  selectedOcrEngine: string | null;
  fallbackUsed: boolean;
  fallbackAttempted: boolean;
  fallbackFailureCount: number;
  emergencyFallbackUsed: boolean;
  emergencyFallbackReason: string | null;
  reviewRecommended: boolean;
  reviewReasons: string[];
  statusText: string;
  failureMessage: string | null;
}

export interface DrawingJobResult {
  sourceFile: string;
  sourceLabelsFile: string | null;
  pages: DrawingResultPage[];
  rows: DrawingResultRow[];
  heldBackRows: DrawingResultRow[];
  missingLabels: string[];
  extraDetectedLabels: string[];
  summary: DrawingJobSummary;
  artifacts: DrawingJobArtifacts;
}

export interface DrawingJob {
  jobId: string;
  title: string;
  status: DrawingJobStatus;
  input: DrawingJobInput;
  errorMessage: string | null;
  result: DrawingJobResult | null;
  createdAt: TimestampIso;
  updatedAt: TimestampIso;
}

export interface JobListItem {
  jobId: string;
  title: string;
  status: DrawingJobStatus;
  drawingName: string;
  labelsName: string | null;
  documentConfidence: number | null;
  degradedRecognition: boolean;
  degradedReason: string | null;
  emergencyFallbackUsed: boolean;
  errorMessage: string | null;
  createdAt: TimestampIso;
  updatedAt: TimestampIso;
}

export interface CreateJobResponse {
  job: DrawingJob;
}

export interface JobListResponse {
  jobs: JobListItem[];
}

export interface BatchJobWarning {
  code: string;
  message: string;
  fileName: string | null;
  baseName: string | null;
}

export interface CreateBatchJobsResponse {
  batchId: string | null;
  jobs: DrawingJob[];
  warnings: BatchJobWarning[];
}

export interface PreviewSessionResponse {
  sessionId: string;
}

export interface BatchSummary {
  totalJobs: number;
  queuedJobs: number;
  runningJobs: number;
  completedJobs: number;
  failedJobs: number;
  degradedJobs: number;
  rescuedJobs: number;
  finished: boolean;
}

export interface DrawingJobBatch {
  batchId: string;
  title: string;
  archiveName: string;
  titlePrefix: string | null;
  jobIds: string[];
  warnings: BatchJobWarning[];
  createdAt: TimestampIso;
  updatedAt: TimestampIso;
  summary: BatchSummary;
}

export interface BatchListItem {
  batchId: string;
  title: string;
  archiveName: string;
  jobCount: number;
  warningCount: number;
  createdAt: TimestampIso;
  updatedAt: TimestampIso;
  summary: BatchSummary;
}

export interface BatchListResponse {
  batches: BatchListItem[];
}

export interface BatchResponse {
  batch: DrawingJobBatch;
  jobs: JobListItem[];
}
