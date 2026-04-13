from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import Field

from .schemas import ApiModel


class DrawingJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DrawingResultRowStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    UNCERTAIN = "uncertain"


class ResultPoint(ApiModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)


class ResultBoundingBox(ApiModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    w: float = Field(gt=0)
    h: float = Field(gt=0)


class DrawingJobInput(ApiModel):
    drawing_name: str
    drawing_url: str
    labels_name: Optional[str] = None
    labels_url: Optional[str] = None
    has_labels: bool = False


class DrawingResultPage(ApiModel):
    page_index: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    overlay_url: Optional[str] = None
    row_count: int = Field(default=0, ge=0)
    held_back_count: int = Field(default=0, ge=0)


class DrawingResultRow(ApiModel):
    row: int = Field(ge=1)
    label: str
    page_index: int = Field(default=0, ge=0)
    center: Optional[ResultPoint] = None
    top_left: Optional[ResultPoint] = None
    bbox: Optional[ResultBoundingBox] = None
    final_score: Optional[float] = Field(default=None, ge=0, le=1)
    status: DrawingResultRowStatus
    note: Optional[str] = None
    source_kind: Optional[str] = None


class DrawingJobArtifacts(ApiModel):
    overlay_url: Optional[str] = None
    csv_url: Optional[str] = None
    xlsx_url: Optional[str] = None
    zip_url: Optional[str] = None
    review_csv_url: Optional[str] = None
    review_xlsx_url: Optional[str] = None
    review_zip_url: Optional[str] = None
    near_tie_csv_url: Optional[str] = None
    near_tie_json_url: Optional[str] = None
    source_json_url: Optional[str] = None
    result_json_url: Optional[str] = None


class DrawingJobSummary(ApiModel):
    total_rows: int = Field(default=0, ge=0)
    found_count: int = Field(default=0, ge=0)
    missing_count: int = Field(default=0, ge=0)
    uncertain_count: int = Field(default=0, ge=0)
    held_back_count: int = Field(default=0, ge=0)
    near_tie_ambiguity_count: int = Field(default=0, ge=0)
    discarded_count: int = Field(default=0, ge=0)
    document_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    degraded_recognition: bool = False
    degraded_reason: Optional[str] = None
    selected_ocr_engine: Optional[str] = None
    fallback_used: bool = False
    fallback_attempted: bool = False
    fallback_failure_count: int = Field(default=0, ge=0)
    emergency_fallback_used: bool = False
    emergency_fallback_reason: Optional[str] = None
    review_recommended: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    status_text: str = ""
    failure_message: Optional[str] = None


class DrawingJobResult(ApiModel):
    source_file: str
    source_labels_file: Optional[str] = None
    pages: list[DrawingResultPage] = Field(default_factory=list)
    rows: list[DrawingResultRow] = Field(default_factory=list)
    held_back_rows: list[DrawingResultRow] = Field(default_factory=list)
    missing_labels: list[str] = Field(default_factory=list)
    extra_detected_labels: list[str] = Field(default_factory=list)
    summary: DrawingJobSummary = Field(default_factory=DrawingJobSummary)
    artifacts: DrawingJobArtifacts = Field(default_factory=DrawingJobArtifacts)


class DrawingJob(ApiModel):
    job_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = "Untitled job"
    status: DrawingJobStatus = DrawingJobStatus.QUEUED
    input: DrawingJobInput
    error_message: Optional[str] = None
    result: Optional[DrawingJobResult] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class JobListItem(ApiModel):
    job_id: str
    title: str
    status: DrawingJobStatus
    drawing_name: str
    labels_name: Optional[str] = None
    document_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    degraded_recognition: bool = False
    degraded_reason: Optional[str] = None
    emergency_fallback_used: bool = False
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CreateJobResponse(ApiModel):
    job: DrawingJob


class JobListResponse(ApiModel):
    jobs: list[JobListItem]


class BatchJobWarning(ApiModel):
    code: str
    message: str
    file_name: Optional[str] = None
    base_name: Optional[str] = None


class CreateBatchJobsResponse(ApiModel):
    batch_id: Optional[str] = None
    jobs: list[DrawingJob] = Field(default_factory=list)
    warnings: list[BatchJobWarning] = Field(default_factory=list)


class PreviewSessionResponse(ApiModel):
    session_id: str


class BatchSummary(ApiModel):
    total_jobs: int = Field(default=0, ge=0)
    queued_jobs: int = Field(default=0, ge=0)
    running_jobs: int = Field(default=0, ge=0)
    completed_jobs: int = Field(default=0, ge=0)
    failed_jobs: int = Field(default=0, ge=0)
    degraded_jobs: int = Field(default=0, ge=0)
    rescued_jobs: int = Field(default=0, ge=0)
    finished: bool = False


class DrawingJobBatch(ApiModel):
    batch_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = "Untitled batch"
    archive_name: str
    title_prefix: Optional[str] = None
    job_ids: list[str] = Field(default_factory=list)
    warnings: list[BatchJobWarning] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    summary: BatchSummary = Field(default_factory=BatchSummary)


class BatchListItem(ApiModel):
    batch_id: str
    title: str
    archive_name: str
    job_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    summary: BatchSummary = Field(default_factory=BatchSummary)


class BatchListResponse(ApiModel):
    batches: list[BatchListItem] = Field(default_factory=list)


class BatchResponse(ApiModel):
    batch: DrawingJobBatch
    jobs: list[JobListItem] = Field(default_factory=list)
