from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class SessionState(str, Enum):
    DRAFT = "draft"
    READY = "ready"


class Actor(str, Enum):
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"


class MarkerStatus(str, Enum):
    HUMAN_DRAFT = "human_draft"
    AI_DETECTED = "ai_detected"
    AI_REVIEW = "ai_review"
    HUMAN_CONFIRMED = "human_confirmed"
    HUMAN_CORRECTED = "human_corrected"
    REJECTED = "rejected"


class MarkerPointType(str, Enum):
    CENTER = "center"
    TOP_LEFT = "top_left"


class CandidateKind(str, Enum):
    CIRCLE = "circle"
    BOX = "box"
    TEXT = "text"


class CandidateReviewStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PipelineConflictType(str, Enum):
    CANDIDATE_AMBIGUITY = "candidate_ambiguity"
    ASSOCIATION_AMBIGUITY = "association_ambiguity"
    DUPLICATE_LABEL_NEARBY = "duplicate_label_nearby"
    MISSING_VOCAB_LABEL = "missing_vocab_label"


class PipelineConflictSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class ActionType(str, Enum):
    SESSION_CREATED = "session_created"
    DOCUMENT_UPLOADED = "document_uploaded"
    CANDIDATES_DETECTED = "candidates_detected"
    AUTO_ANNOTATION_COMPLETED = "auto_annotation_completed"
    VIEWPORT_SET = "viewport_set"
    VIEWPORT_PANNED = "viewport_panned"
    VIEWPORT_ZOOMED = "viewport_zoomed"
    CANDIDATE_REJECTED = "candidate_rejected"
    MARKER_CREATED = "marker_created"
    MARKER_MOVED = "marker_moved"
    MARKER_UPDATED = "marker_updated"
    MARKER_CONFIRMED = "marker_confirmed"
    MARKER_REJECTED = "marker_rejected"
    MARKER_DELETED = "marker_deleted"
    MARKERS_CLEARED = "markers_cleared"


class SessionCommandType(str, Enum):
    SET_VIEWPORT = "set_viewport"
    PAN_VIEWPORT = "pan_viewport"
    ZOOM_TO_REGION = "zoom_to_region"
    PLACE_MARKER = "place_marker"
    MOVE_MARKER = "move_marker"
    UPDATE_MARKER = "update_marker"
    CONFIRM_MARKER = "confirm_marker"
    REJECT_MARKER = "reject_marker"
    DELETE_MARKER = "delete_marker"
    CLEAR_MARKERS = "clear_markers"
    REJECT_CANDIDATE = "reject_candidate"


class Viewport(ApiModel):
    center_x: float = 0
    center_y: float = 0
    zoom: float = Field(default=1, gt=0)


class DocumentAsset(ApiModel):
    document_id: str = Field(default_factory=lambda: str(uuid4()))
    file_name: str
    content_type: str
    size_bytes: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    storage_url: str
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


class Marker(ApiModel):
    marker_id: str = Field(default_factory=lambda: str(uuid4()))
    label: Optional[str] = None
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    point_type: MarkerPointType = MarkerPointType.CENTER
    status: MarkerStatus
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    created_by: Actor
    updated_by: Actor
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CalloutCandidate(ApiModel):
    candidate_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: CandidateKind
    center_x: float = Field(ge=0)
    center_y: float = Field(ge=0)
    bbox_x: float = Field(ge=0)
    bbox_y: float = Field(ge=0)
    bbox_width: float = Field(gt=0)
    bbox_height: float = Field(gt=0)
    score: float = Field(ge=0)
    crop_url: Optional[str] = None
    suggested_label: Optional[str] = None
    suggested_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    suggested_source: Optional[str] = None
    topology_score: Optional[float] = Field(default=None, ge=0, le=1)
    topology_source: Optional[str] = None
    leader_anchor_x: Optional[float] = Field(default=None, ge=0)
    leader_anchor_y: Optional[float] = Field(default=None, ge=0)
    review_status: CandidateReviewStatus = CandidateReviewStatus.PENDING
    conflict_group: Optional[str] = None
    conflict_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CandidateAssociation(ApiModel):
    association_id: str = Field(default_factory=lambda: str(uuid4()))
    shape_candidate_id: str
    text_candidate_id: str
    shape_kind: CandidateKind
    label: str
    score: float = Field(ge=0, le=1)
    geometry_score: float = Field(ge=0, le=1)
    topology_score: Optional[float] = Field(default=None, ge=0, le=1)
    source: str
    leader_anchor_x: Optional[float] = Field(default=None, ge=0)
    leader_anchor_y: Optional[float] = Field(default=None, ge=0)
    bbox_x: Optional[float] = Field(default=None, ge=0)
    bbox_y: Optional[float] = Field(default=None, ge=0)
    bbox_width: Optional[float] = Field(default=None, ge=0)
    bbox_height: Optional[float] = Field(default=None, ge=0)


class PageVocabularyEntry(ApiModel):
    label: str
    normalized_label: str
    occurrences: int = Field(default=0, ge=0)
    max_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    sources: list[str] = Field(default_factory=list)
    bbox_x: Optional[float] = Field(default=None, ge=0)
    bbox_y: Optional[float] = Field(default=None, ge=0)
    bbox_width: Optional[float] = Field(default=None, ge=0)
    bbox_height: Optional[float] = Field(default=None, ge=0)


class PipelineConflict(ApiModel):
    conflict_id: str = Field(default_factory=lambda: str(uuid4()))
    type: PipelineConflictType
    severity: PipelineConflictSeverity
    label: Optional[str] = None
    message: str
    candidate_ids: list[str] = Field(default_factory=list)
    marker_ids: list[str] = Field(default_factory=list)
    related_labels: list[str] = Field(default_factory=list)
    bbox_x: Optional[float] = Field(default=None, ge=0)
    bbox_y: Optional[float] = Field(default=None, ge=0)
    bbox_width: Optional[float] = Field(default=None, ge=0)
    bbox_height: Optional[float] = Field(default=None, ge=0)


class ActionLogEntry(ApiModel):
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    actor: Actor
    type: ActionType
    created_at: datetime = Field(default_factory=datetime.utcnow)
    payload: Dict[str, Any] = Field(default_factory=dict)


class SessionSummary(ApiModel):
    total_markers: int = 0
    ai_detected: int = 0
    ai_review: int = 0
    human_confirmed: int = 0
    human_corrected: int = 0
    rejected: int = 0


class AnnotationSession(ApiModel):
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = "Untitled session"
    state: SessionState = SessionState.DRAFT
    document: Optional[DocumentAsset] = None
    viewport: Viewport = Field(default_factory=Viewport)
    candidates: list[CalloutCandidate] = Field(default_factory=list)
    candidate_associations: list[CandidateAssociation] = Field(default_factory=list)
    page_vocabulary: list[PageVocabularyEntry] = Field(default_factory=list)
    missing_labels: list[str] = Field(default_factory=list)
    pipeline_conflicts: list[PipelineConflict] = Field(default_factory=list)
    markers: list[Marker] = Field(default_factory=list)
    action_log: list[ActionLogEntry] = Field(default_factory=list)
    summary: SessionSummary = Field(default_factory=SessionSummary)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SessionListItem(ApiModel):
    session_id: str
    title: str
    state: SessionState
    document_name: Optional[str] = None
    marker_count: int = 0
    updated_at: datetime


class CreateSessionRequest(ApiModel):
    title: Optional[str] = None


class CreateSessionResponse(ApiModel):
    session: AnnotationSession


class SessionListResponse(ApiModel):
    sessions: list[SessionListItem]


class UploadDocumentResponse(ApiModel):
    session: AnnotationSession


class SessionCommandRequest(ApiModel):
    type: SessionCommandType
    actor: Actor = Actor.HUMAN
    marker_id: Optional[str] = None
    candidate_id: Optional[str] = None
    label: Optional[str] = None
    point_type: Optional[MarkerPointType] = None
    status: Optional[MarkerStatus] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    center_x: Optional[float] = None
    center_y: Optional[float] = None
    zoom: Optional[float] = Field(default=None, gt=0)
    delta_x: Optional[float] = None
    delta_y: Optional[float] = None


class SessionCommandResponse(ApiModel):
    session: AnnotationSession
