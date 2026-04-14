from __future__ import annotations

import asyncio
import csv
import re
import shutil
import string
import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO, StringIO
from math import hypot
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from ..core.config import settings
from ..models.job_schemas import DrawingResultRow, DrawingResultRowStatus
from ..models.schemas import (
    ActionLogEntry,
    ActionType,
    Actor,
    AnnotationSession,
    CalloutCandidate,
    CandidateAssociation,
    CandidateReviewStatus,
    CreateSessionRequest,
    CreateSessionResponse,
    CandidateKind,
    DocumentAsset,
    Marker,
    MarkerPointType,
    MarkerStatus,
    PageVocabularyEntry,
    PipelineConflict,
    PipelineConflictType,
    PipelineConflictSeverity,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCommandType,
    SessionListItem,
    SessionListResponse,
    SessionState,
    SessionSummary,
    UploadDocumentResponse,
    Viewport,
)
from .candidate_association import AssociationBuildConfig, CandidateAssociationBuilder
from .candidate_detector import DrawingCandidateDetector
from .candidate_recognizer import DrawingCandidateRecognizer
from .candidate_vlm_recognizer import VisionLLMCandidateRecognizer
from .conflict_engine import PipelineConflictEngine
from .leader_topology import LeaderTopologyAnalyzer
from .page_vocabulary import PageVocabularyBuilder


@dataclass
class StoredUpload:
    file_name: str
    content_type: str
    size_bytes: int
    storage_path: Path


def normalize_label(value: str | None) -> str:
    return (value or "").strip().lower()


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, AnnotationSession] = {}
        self._lock = asyncio.Lock()
        self._candidate_detector = DrawingCandidateDetector()
        self._candidate_recognizer = DrawingCandidateRecognizer()
        self._candidate_vlm_recognizer = VisionLLMCandidateRecognizer()
        self._candidate_association_builder = CandidateAssociationBuilder()
        self._page_vocabulary_builder = PageVocabularyBuilder()
        self._pipeline_conflict_engine = PipelineConflictEngine()
        self._leader_topology = LeaderTopologyAnalyzer()

    @property
    def storage_root(self) -> Path:
        root = Path(settings.storage_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _session_dir(self, session_id: str) -> Path:
        path = self.storage_root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def list_sessions(self) -> SessionListResponse:
        async with self._lock:
            sessions = sorted(self._sessions.values(), key=lambda item: item.updated_at, reverse=True)
            return SessionListResponse(
                sessions=[
                    SessionListItem(
                        session_id=item.session_id,
                        title=item.title,
                        state=item.state,
                        document_name=item.document.file_name if item.document else None,
                        marker_count=len(item.markers),
                        updated_at=item.updated_at,
                    )
                    for item in sessions
                ]
            )

    async def create_session(self, payload: CreateSessionRequest) -> CreateSessionResponse:
        async with self._lock:
            session = AnnotationSession(title=(payload.title or "Untitled session").strip() or "Untitled session")
            self._record_action(session, Actor.SYSTEM, ActionType.SESSION_CREATED, {"title": session.title})
            self._sessions[session.session_id] = session
            self._session_dir(session.session_id)
            return CreateSessionResponse(session=session.model_copy(deep=True))

    async def delete_session(self, session_id: str) -> None:
        async with self._lock:
            self._get_session(session_id)
            self._sessions.pop(session_id, None)
            shutil.rmtree(self.storage_root / session_id, ignore_errors=True)

    async def get_session(self, session_id: str) -> AnnotationSession:
        async with self._lock:
            return self._get_session(session_id).model_copy(deep=True)

    async def delete_session(self, session_id: str) -> None:
        async with self._lock:
            self._get_session(session_id)
            self._sessions.pop(session_id, None)

            session_dir = self.storage_root / session_id
            if session_dir.is_dir():
                shutil.rmtree(session_dir)

    async def create_session_from_job_result(
        self,
        *,
        title: str,
        source_file_name: str,
        raster_path: Path,
        rows: list[DrawingResultRow],
        held_back_rows: list[DrawingResultRow],
        missing_labels: list[str],
        document_confidence: float | None,
        source_job_id: str,
        near_tie_items: list[dict] | None = None,
    ) -> CreateSessionResponse:
        if not raster_path.is_file():
            raise ValueError(f"Не найден подготовленный raster для preview: {raster_path}")

        created = await self.create_session(CreateSessionRequest(title=title))
        session_id = created.session.session_id
        raster_file_name = f"{Path(source_file_name).stem or raster_path.stem}-preview.png"

        try:
            with raster_path.open("rb") as source_stream:
                upload = self.prepare_upload(
                    session_id=session_id,
                    file_name=raster_file_name,
                    content_type="image/png",
                    source_stream=source_stream,
                    size_bytes=raster_path.stat().st_size,
                )
            await self.upload_document(session_id, upload)

            async with self._lock:
                session = self._get_session(session_id)
                merged_missing_labels = self._merge_job_missing_labels(missing_labels, rows)
                session.markers = []
                session.candidates = []
                session.candidate_associations = []
                session.page_vocabulary = self._build_page_vocabulary_from_job_rows(rows + held_back_rows)
                session.missing_labels = merged_missing_labels
                session.pipeline_conflicts = []
                near_tie_index = self._build_near_tie_index(near_tie_items or [])

                missing_conflict_labels = {normalize_label(label) for label in merged_missing_labels}

                for row in rows:
                    bbox_kwargs = self._job_row_bbox_kwargs(row)
                    marker = self._build_marker_from_job_row(
                        row,
                        status=MarkerStatus.AI_REVIEW if row.status == DrawingResultRowStatus.UNCERTAIN else MarkerStatus.AI_DETECTED,
                    )

                    if marker is not None:
                        session.markers.append(marker)

                    if row.status == DrawingResultRowStatus.NOT_FOUND:
                        session.pipeline_conflicts.append(
                            PipelineConflict(
                                type=PipelineConflictType.MISSING_VOCAB_LABEL,
                                severity=PipelineConflictSeverity.ERROR,
                                label=row.label,
                                message=row.note or "Система не смогла уверенно найти номер на чертеже.",
                                related_labels=[row.label],
                                **bbox_kwargs,
                            )
                        )
                        continue

                    if row.status == DrawingResultRowStatus.UNCERTAIN:
                        session.pipeline_conflicts.append(
                            PipelineConflict(
                                type=PipelineConflictType.CANDIDATE_AMBIGUITY,
                                severity=PipelineConflictSeverity.WARNING,
                                label=row.label,
                                message=row.note or "Нужна ручная проверка результата job pipeline.",
                                marker_ids=[marker.marker_id] if marker is not None else [],
                                related_labels=[row.label],
                                **bbox_kwargs,
                            )
                        )
                        continue

                    if marker is None:
                        normalized_label = normalize_label(row.label)
                        if normalized_label and normalized_label not in missing_conflict_labels:
                            session.missing_labels.append(row.label)
                            missing_conflict_labels.add(normalized_label)
                        session.pipeline_conflicts.append(
                            PipelineConflict(
                                type=PipelineConflictType.MISSING_VOCAB_LABEL,
                                severity=PipelineConflictSeverity.ERROR,
                                label=row.label,
                                message=row.note or "Для найденного номера не удалось собрать координаты preview.",
                                related_labels=[row.label],
                                **bbox_kwargs,
                            )
                        )

                for row in held_back_rows:
                    near_tie_item = near_tie_index.get((row.row, row.page_index, normalize_label(row.label)))
                    if near_tie_item is not None and row.note and "OCR near-tie ambiguity" in row.note:
                        markers = self._build_near_tie_markers_from_job_row(row, near_tie_item)
                        session.markers.extend(markers)
                        session.pipeline_conflicts.append(
                            PipelineConflict(
                                type=PipelineConflictType.CANDIDATE_AMBIGUITY,
                                severity=PipelineConflictSeverity.WARNING,
                                label=row.label,
                                message=(
                                    near_tie_item.get("note")
                                    or row.note
                                    or "Внутри одного bbox почти равный OCR-спор между двумя цифрами. Нужна ручная проверка."
                                ),
                                marker_ids=[marker.marker_id for marker in markers],
                                related_labels=[marker.label for marker in markers if marker.label],
                                **self._job_row_bbox_kwargs(row),
                            )
                        )
                        continue
                    bbox_kwargs = self._job_row_bbox_kwargs(row)
                    marker = self._build_marker_from_job_row(row, status=MarkerStatus.AI_REVIEW)
                    if marker is not None:
                        session.markers.append(marker)
                    session.pipeline_conflicts.append(
                        PipelineConflict(
                            type=PipelineConflictType.CANDIDATE_AMBIGUITY,
                            severity=PipelineConflictSeverity.WARNING,
                            label=row.label,
                            message=row.note or "Кандидат удержан из итогового CSV и вынесен в ручную проверку.",
                            marker_ids=[marker.marker_id] if marker is not None else [],
                            related_labels=[row.label],
                            **bbox_kwargs,
                        )
                    )

                self._refresh_summary(session)
                self._record_action(
                    session,
                    Actor.AI,
                    ActionType.AUTO_ANNOTATION_COMPLETED,
                    {
                        "source_job_id": source_job_id,
                        "imported_marker_count": len(session.markers),
                        "held_back_marker_count": len(held_back_rows),
                        "missing_label_count": len(session.missing_labels),
                        "pipeline_conflict_count": len(session.pipeline_conflicts),
                        "document_confidence": document_confidence,
                    },
                )
                return CreateSessionResponse(session=session.model_copy(deep=True))
        except Exception:
            try:
                await self.delete_session(session_id)
            except KeyError:
                pass
            raise

    async def upload_document(self, session_id: str, upload: StoredUpload) -> UploadDocumentResponse:
        async with self._lock:
            session = self._get_session(session_id)
            try:
                with Image.open(upload.storage_path) as image:
                    width, height = image.size
            except (UnidentifiedImageError, OSError) as exc:
                upload.storage_path.unlink(missing_ok=True)
                raise ValueError("Only raster image files are supported for now.") from exc

            document = DocumentAsset(
                file_name=upload.file_name,
                content_type=upload.content_type,
                size_bytes=upload.size_bytes,
                width=width,
                height=height,
                storage_url=f"{settings.storage_mount_path}/{session_id}/{upload.storage_path.name}",
            )
            session.document = document
            session.viewport = Viewport(center_x=width / 2, center_y=height / 2, zoom=1)
            session.candidates = []
            session.candidate_associations = []
            session.page_vocabulary = []
            session.missing_labels = []
            session.pipeline_conflicts = []
            session.markers = []
            self._refresh_summary(session)
            session.state = SessionState.READY
            session.updated_at = datetime.utcnow()
            self._record_action(
                session,
                Actor.HUMAN,
                ActionType.DOCUMENT_UPLOADED,
                {
                    "file_name": document.file_name,
                    "width": document.width,
                    "height": document.height,
                    "storage_url": document.storage_url,
                },
            )
            return UploadDocumentResponse(session=session.model_copy(deep=True))

    async def export_session_archive(self, session_id: str) -> tuple[str, bytes]:
        async with self._lock:
            live_session = self._get_session(session_id)
            self._refresh_pipeline_state(live_session, include_missing_labels=True)
            session = live_session.model_copy(deep=True)

        blocking_export_issues = self._collect_blocking_export_issues(session)
        if blocking_export_issues:
            issues_text = "; ".join(blocking_export_issues[:4])
            if len(blocking_export_issues) > 4:
                issues_text = f"{issues_text}; +{len(blocking_export_issues) - 4} more"
            raise ValueError(f"Export blocked until pipeline conflicts are resolved: {issues_text}")

        document = self._require_document(session)
        document_path = self._resolve_document_path(session.session_id, document.storage_url)
        if not document_path.is_file():
            raise ValueError("Document file is missing and cannot be exported.")

        archive_name = f"{self._safe_export_name(session.title)}-{session.session_id[:8]}-export.zip"
        archive_body = self._build_export_archive(session, document_path)
        return archive_name, archive_body

    async def detect_candidates(self, session_id: str) -> CreateSessionResponse:
        async with self._lock:
            session = self._get_session(session_id)
            document = self._require_document(session)
            document_path = self._resolve_document_path(session.session_id, document.storage_url)
            if not document_path.is_file():
                raise ValueError("Document file is missing and candidates cannot be detected.")

            session.candidates, session.candidate_associations, explicit_vocabulary = self._build_candidates(session, document_path)
            self._refresh_pipeline_state(
                session,
                explicit_vocabulary=explicit_vocabulary,
                include_missing_labels=False,
            )
            self._record_action(
                session,
                Actor.SYSTEM,
                ActionType.CANDIDATES_DETECTED,
                {"count": len(session.candidates)},
            )
            return CreateSessionResponse(session=session.model_copy(deep=True))

    async def auto_annotate(self, session_id: str) -> CreateSessionResponse:
        async with self._lock:
            session = self._get_session(session_id)
            document = self._require_document(session)
            document_path = self._resolve_document_path(session.session_id, document.storage_url)
            if not document_path.is_file():
                raise ValueError("Document file is missing and auto-annotation cannot run.")

            session.markers = [marker for marker in session.markers if marker.created_by != Actor.AI]
            session.candidates, session.candidate_associations, explicit_vocabulary = self._build_candidates(session, document_path)
            self._refresh_pipeline_state(
                session,
                explicit_vocabulary=explicit_vocabulary,
                include_missing_labels=False,
            )
            auto_markers, accepted_count, review_count, pending_count = self._build_auto_markers(session)
            session.markers.extend(auto_markers)
            self._refresh_pipeline_state(
                session,
                explicit_vocabulary=explicit_vocabulary,
                include_missing_labels=True,
            )
            self._refresh_summary(session)
            self._record_action(
                session,
                Actor.AI,
                ActionType.AUTO_ANNOTATION_COMPLETED,
                {
                    "candidateCount": len(session.candidates),
                    "autoAccepted": accepted_count,
                    "autoReview": review_count,
                    "pendingCandidates": pending_count,
                },
            )
            return CreateSessionResponse(session=session.model_copy(deep=True))

    async def reject_candidate(self, session_id: str, candidate_id: str, actor: Actor = Actor.HUMAN) -> CreateSessionResponse:
        async with self._lock:
            session = self._get_session(session_id)
            candidate = self._find_candidate(session, candidate_id)
            candidate.review_status = CandidateReviewStatus.REJECTED
            candidate.updated_at = datetime.utcnow()
            self._record_action(
                session,
                actor,
                ActionType.CANDIDATE_REJECTED,
                {"candidateId": candidate_id},
            )
            return CreateSessionResponse(session=session.model_copy(deep=True))

    async def apply_command(self, session_id: str, command: SessionCommandRequest) -> SessionCommandResponse:
        async with self._lock:
            session = self._get_session(session_id)
            action_type = self._apply(session, command)
            self._refresh_summary(session)

            if self._should_record_action(action_type):
                self._record_action(session, command.actor, action_type, self._command_payload(command))
            else:
                session.updated_at = datetime.utcnow()
            self._refresh_pipeline_state(session)
            return SessionCommandResponse(session=session.model_copy(deep=True))

    def prepare_upload(self, session_id: str, file_name: str, content_type: str, source_stream, size_bytes: int) -> StoredUpload:
        session_dir = self._session_dir(session_id)
        safe_name = Path(file_name).name or "drawing.png"
        storage_path = session_dir / f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{safe_name}"
        with storage_path.open("wb") as out_file:
            shutil.copyfileobj(source_stream, out_file)
        return StoredUpload(
            file_name=safe_name,
            content_type=content_type or "application/octet-stream",
            size_bytes=max(size_bytes, 1),
            storage_path=storage_path,
        )

    def _build_candidates(
        self,
        session: AnnotationSession,
        document_path: Path,
    ) -> tuple[list[CalloutCandidate], list[CandidateAssociation], set[str]]:
        raw_candidates = self._candidate_detector.detect(document_path)
        candidates_dir = self._session_dir(session.session_id) / "candidates"
        if candidates_dir.is_dir():
            shutil.rmtree(candidates_dir)
        candidates_dir.mkdir(parents=True, exist_ok=True)
        label_vocabulary: set[str] = set()

        with Image.open(document_path) as image:
            preview_image = image.convert("RGB")
            width, height = preview_image.size
            accepted_markers = session.markers
            base_candidates: list[CalloutCandidate] = []

            for raw in raw_candidates:
                bbox_x = max(0, min(raw.bbox_x, width - 1))
                bbox_y = max(0, min(raw.bbox_y, height - 1))
                bbox_width = min(raw.bbox_width, width - bbox_x)
                bbox_height = min(raw.bbox_height, height - bbox_y)
                if bbox_width <= 4 or bbox_height <= 4:
                    continue

                review_status = CandidateReviewStatus.PENDING
                acceptance_threshold = max(10.0, min(bbox_width, bbox_height) * 0.45)
                for marker in accepted_markers:
                    marker_distance = ((marker.x - raw.center_x) ** 2 + (marker.y - raw.center_y) ** 2) ** 0.5
                    if marker_distance <= acceptance_threshold:
                        review_status = CandidateReviewStatus.ACCEPTED
                        break

                candidate = CalloutCandidate(
                    kind=CandidateKind(raw.kind),
                    center_x=raw.center_x,
                    center_y=raw.center_y,
                    bbox_x=bbox_x,
                    bbox_y=bbox_y,
                    bbox_width=bbox_width,
                    bbox_height=bbox_height,
                    score=max(0.0, round(raw.score, 2)),
                    review_status=review_status,
                )
                crop = self._build_candidate_crop(preview_image, bbox_x, bbox_y, bbox_width, bbox_height)
                candidate.crop_url = self._write_candidate_crop(session.session_id, candidate.candidate_id, crop)
                candidate.created_at = datetime.utcnow()
                candidate.updated_at = candidate.created_at
                base_candidates.append(candidate)

            for region in self._candidate_recognizer.detect_document_text(preview_image):
                review_status = CandidateReviewStatus.PENDING
                acceptance_threshold = max(10.0, min(region.bbox_width, region.bbox_height) * 0.45)
                center_x = region.bbox_x + region.bbox_width / 2
                center_y = region.bbox_y + region.bbox_height / 2
                for marker in accepted_markers:
                    marker_distance = hypot(marker.x - center_x, marker.y - center_y)
                    if marker_distance <= acceptance_threshold:
                        review_status = CandidateReviewStatus.ACCEPTED
                        break

                candidate = CalloutCandidate(
                    kind=CandidateKind.TEXT,
                    center_x=center_x,
                    center_y=center_y,
                    bbox_x=region.bbox_x,
                    bbox_y=region.bbox_y,
                    bbox_width=region.bbox_width,
                    bbox_height=region.bbox_height,
                    score=round(140 + region.confidence * 120 + min(region.bbox_width, 80) * 0.35, 2),
                    crop_url="",
                    suggested_label=region.label,
                    suggested_confidence=region.confidence,
                    suggested_source=region.source,
                    review_status=review_status,
                )
                crop = self._build_candidate_crop(
                    preview_image,
                    candidate.bbox_x,
                    candidate.bbox_y,
                    candidate.bbox_width,
                    candidate.bbox_height,
                )
                candidate.crop_url = self._write_candidate_crop(session.session_id, candidate.candidate_id, crop)
                candidate.created_at = datetime.utcnow()
                candidate.updated_at = candidate.created_at
                base_candidates.append(candidate)

            min_side = min(width, height)
            low_res_circle_mode = min_side <= 1100 and len(raw_candidates) >= 180
            heavy_vlm_sheet = min_side <= 1600
            enable_vlm_vocabulary = low_res_circle_mode or heavy_vlm_sheet
            normalized_label_vocabulary: set[str] | None = None
            if enable_vlm_vocabulary:
                label_vocabulary = self._candidate_vlm_recognizer.extract_label_vocabulary(
                    preview_image,
                    max_tiles=settings.openai_vision_vocab_max_tiles,
                    heavy_sheet=heavy_vlm_sheet,
                )
                if label_vocabulary:
                    normalized_label_vocabulary = {normalize_label(label) for label in label_vocabulary}
            candidates, candidate_associations = self._compose_candidates(session.session_id, preview_image, base_candidates)
            self._refine_final_candidates_with_ocr(
                preview_image,
                candidates,
                low_res_circle_mode,
                allowed_labels=normalized_label_vocabulary,
            )
            if low_res_circle_mode:
                low_res_circle_pool_size = sum(
                    1
                    for candidate in base_candidates
                    if candidate.kind == CandidateKind.CIRCLE
                    and 14 <= min(candidate.bbox_width, candidate.bbox_height) <= 60
                )
                low_res_recovery_target = max(16, min(48, max(1, low_res_circle_pool_size // 2)))
                if label_vocabulary:
                    low_res_recovery_target = max(low_res_recovery_target, min(48, max(12, len(label_vocabulary) // 2)))

                allow_expensive_low_res_tile_passes = (
                    not normalized_label_vocabulary
                    and low_res_circle_pool_size <= 220
                    and len(candidates) < max(8, low_res_recovery_target // 2)
                )

                if allow_expensive_low_res_tile_passes:
                    context_tile_candidates, reviewed_context_tile_candidates = self._build_low_res_context_tile_candidates(
                        preview_image,
                        base_candidates,
                        candidates,
                        allowed_labels=normalized_label_vocabulary,
                    )
                    if context_tile_candidates:
                        candidates = self._prune_low_res_candidates_after_tile_review(
                            candidates,
                            context_tile_candidates,
                            reviewed_context_tile_candidates,
                        )
                        candidates = self._dedupe_composed_candidates([*candidates, *context_tile_candidates])
                        candidates = self._prune_low_res_oversized_circle_candidates(candidates)

                if allow_expensive_low_res_tile_passes and len(candidates) < low_res_recovery_target:
                    sequence_tile_candidates, reviewed_sequence_tile_candidates = self._build_low_res_sequence_tile_candidates(
                        preview_image,
                        base_candidates,
                        candidates,
                        allowed_labels=normalized_label_vocabulary,
                    )
                    if sequence_tile_candidates:
                        candidates = self._prune_low_res_candidates_after_tile_review(
                            candidates,
                            sequence_tile_candidates,
                            reviewed_sequence_tile_candidates,
                        )
                        candidates = self._dedupe_composed_candidates([*candidates, *sequence_tile_candidates])
                        candidates = self._prune_low_res_oversized_circle_candidates(candidates)

                if len(candidates) < low_res_recovery_target:
                    circle_only = self._build_low_res_circle_only_candidates(
                        preview_image,
                        base_candidates,
                        candidates,
                        allowed_labels=normalized_label_vocabulary,
                    )
                    if circle_only:
                        candidates = self._dedupe_composed_candidates([*candidates, *circle_only])
                    candidates = self._prune_low_res_oversized_circle_candidates(candidates)

                if len(candidates) < low_res_recovery_target:
                    fallback_shapes = self._build_low_res_shape_fallback_candidates(
                        preview_image,
                        base_candidates,
                        candidates,
                        allowed_labels=normalized_label_vocabulary,
                    )
                    if fallback_shapes:
                        candidates = self._dedupe_composed_candidates([*candidates, *fallback_shapes])
                    candidates = self._prune_low_res_oversized_circle_candidates(candidates)

                if len(candidates) < low_res_recovery_target:
                    tile_candidates, reviewed_tile_candidates = self._build_low_res_letter_tile_candidates(
                        preview_image,
                        base_candidates,
                        candidates,
                        allowed_labels=normalized_label_vocabulary,
                    )
                    if tile_candidates:
                        candidates = self._prune_low_res_candidates_after_tile_review(
                            candidates,
                            tile_candidates,
                            reviewed_tile_candidates,
                        )
                        candidates = self._dedupe_composed_candidates([*candidates, *tile_candidates])
                        candidates = self._prune_low_res_oversized_circle_candidates(candidates)
            self._refine_final_candidates_with_vlm(
                preview_image,
                candidates,
                low_res_circle_mode,
                allowed_labels=normalized_label_vocabulary,
            )
            if enable_vlm_vocabulary and label_vocabulary:
                self._apply_label_vocabulary(candidates, label_vocabulary)
                candidates = self._recover_missing_low_res_vocabulary_labels(
                    preview_image,
                    base_candidates,
                    candidates,
                    label_vocabulary,
                )
                self._apply_label_vocabulary(candidates, label_vocabulary)
            self._mark_candidates_against_existing_markers(candidates, accepted_markers)

        self._assign_candidate_conflicts(candidates)
        return candidates, candidate_associations, label_vocabulary

    def _refresh_pipeline_state(
        self,
        session: AnnotationSession,
        *,
        explicit_vocabulary: set[str] | None = None,
        include_missing_labels: bool | None = None,
    ) -> None:
        page_vocabulary = self._page_vocabulary_builder.build(session.candidates, explicit_vocabulary)
        session.page_vocabulary = page_vocabulary
        if include_missing_labels is None:
            include_missing_labels = self._should_include_missing_label_conflicts(session)
        missing_labels, pipeline_conflicts = self._pipeline_conflict_engine.build(
            session,
            page_vocabulary,
            include_missing_labels=include_missing_labels,
        )
        session.missing_labels = missing_labels
        session.pipeline_conflicts = pipeline_conflicts
        session.updated_at = datetime.utcnow()

    @staticmethod
    def _should_include_missing_label_conflicts(session: AnnotationSession) -> bool:
        return bool(session.markers) or any(
            entry.type == ActionType.AUTO_ANNOTATION_COMPLETED for entry in session.action_log
        )

    @staticmethod
    def _mark_candidates_against_existing_markers(candidates: list[CalloutCandidate], accepted_markers: list[Marker]) -> None:
        for candidate in candidates:
            acceptance_threshold = max(10.0, min(candidate.bbox_width, candidate.bbox_height) * 0.45)
            for marker in accepted_markers:
                marker_distance = hypot(marker.x - candidate.center_x, marker.y - candidate.center_y)
                if marker_distance <= acceptance_threshold:
                    candidate.review_status = CandidateReviewStatus.ACCEPTED
                    candidate.updated_at = datetime.utcnow()
                    break

    def _refine_final_candidates_with_ocr(
        self,
        preview_image: Image.Image,
        candidates: list[CalloutCandidate],
        low_res_circle_mode: bool,
        allowed_labels: set[str] | None = None,
    ) -> None:
        if not candidates:
            return

        if low_res_circle_mode:
            ocr_budget = min(18, len(candidates))
            target_pool = [
                candidate
                for candidate in candidates
                if self._eligible_for_final_candidate_ocr(candidate)
                and self._should_retry_low_res_final_candidate_ocr(candidate)
            ]
        else:
            ocr_budget = 48 if len(candidates) <= 80 else 36
            target_pool = [
                candidate
                for candidate in candidates
                if self._eligible_for_final_candidate_ocr(candidate)
            ]

        targets = sorted(target_pool, key=self._final_candidate_ocr_priority)[:ocr_budget]

        for candidate in targets:
            crop = self._build_candidate_ocr_crop(preview_image, candidate)
            suggestion = self._candidate_recognizer.recognize(crop, candidate.kind.value)
            coerced_label = self._coerce_to_allowed_label(allowed_labels, suggestion.label)
            if suggestion.label and not coerced_label:
                continue
            if coerced_label:
                suggestion.label = coerced_label
            if self._should_replace_candidate_suggestion(candidate, suggestion.label, suggestion.confidence, suggestion.source):
                candidate.suggested_label = suggestion.label
                candidate.suggested_confidence = suggestion.confidence
                candidate.suggested_source = suggestion.source

    def _refine_final_candidates_with_vlm(
        self,
        preview_image: Image.Image,
        candidates: list[CalloutCandidate],
        low_res_circle_mode: bool,
        allowed_labels: set[str] | None = None,
    ) -> None:
        if not candidates or not self._candidate_vlm_recognizer.is_enabled():
            return

        budget = settings.openai_vision_max_candidates_low_res if low_res_circle_mode else settings.openai_vision_max_candidates
        if low_res_circle_mode:
            budget = min(max(budget, 8), 12)
        target_pool = [
            candidate
            for candidate in candidates
            if self._eligible_for_final_candidate_vlm(candidate, low_res_circle_mode)
        ]
        targets = sorted(target_pool, key=self._final_candidate_vlm_priority)[:budget]

        for candidate in targets:
            crop = self._build_candidate_vlm_crop(preview_image, candidate)
            suggestion = self._candidate_vlm_recognizer.recognize(
                crop,
                candidate.kind.value,
                local_label=candidate.suggested_label,
                local_confidence=candidate.suggested_confidence,
                use_consensus=not low_res_circle_mode,
                heavy_sheet=low_res_circle_mode,
            )
            coerced_label = self._coerce_to_allowed_label(allowed_labels, suggestion.label)
            if suggestion.label and not coerced_label:
                continue
            if coerced_label:
                suggestion.label = coerced_label
            if self._should_reject_candidate_from_vlm(candidate, suggestion):
                candidate.suggested_label = None
                candidate.suggested_confidence = None
                candidate.suggested_source = suggestion.source
                candidate.review_status = CandidateReviewStatus.REJECTED
                candidate.updated_at = datetime.utcnow()
                continue
            if self._should_replace_candidate_suggestion(candidate, suggestion.label, suggestion.confidence, suggestion.source):
                candidate.suggested_label = suggestion.label
                candidate.suggested_confidence = suggestion.confidence
                candidate.suggested_source = suggestion.source
                candidate.updated_at = datetime.utcnow()

    def _build_low_res_shape_fallback_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> list[CalloutCandidate]:
        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        fallback_pool = [
            candidate
            for candidate in base_candidates
            if candidate.kind in {CandidateKind.CIRCLE, CandidateKind.BOX}
            and not candidate.suggested_label
            and not self._shape_candidate_is_covered(candidate, produced_candidates)
            and (header_cutoff is None or candidate.center_y > header_cutoff)
            and min(candidate.bbox_width, candidate.bbox_height) >= 18
            and max(candidate.bbox_width, candidate.bbox_height) <= 40
        ]
        fallback_pool = sorted(fallback_pool, key=self._low_res_fallback_shape_priority)[:48]

        accepted: list[CalloutCandidate] = []
        known_labels = {
            normalize_label(candidate.suggested_label)
            for candidate in produced_candidates
            if candidate.suggested_label
        }
        for candidate in fallback_pool:
            crop = self._build_candidate_ocr_crop(preview_image, candidate)
            suggestion = self._candidate_recognizer.recognize(crop, candidate.kind.value)
            coerced_label = self._coerce_to_allowed_label(allowed_labels, suggestion.label)
            if suggestion.label and not coerced_label:
                continue
            if coerced_label:
                suggestion.label = coerced_label
            if self._should_replace_candidate_suggestion(candidate, suggestion.label, suggestion.confidence, suggestion.source):
                candidate.suggested_label = suggestion.label
                candidate.suggested_confidence = suggestion.confidence
                candidate.suggested_source = suggestion.source

            candidate_label_key = normalize_label(candidate.suggested_label)
            if candidate_label_key and candidate_label_key in known_labels:
                continue

            if candidate.kind == CandidateKind.CIRCLE:
                if self._should_keep_fallback_circle_candidate(preview_image, candidate, header_cutoff):
                    accepted.append(candidate)
                    if candidate_label_key:
                        known_labels.add(candidate_label_key)
            elif self._should_keep_fallback_box_candidate(candidate, header_cutoff):
                accepted.append(candidate)
                if candidate_label_key:
                    known_labels.add(candidate_label_key)

        return accepted

    def _build_low_res_circle_only_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> list[CalloutCandidate]:
        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        pool = [
            candidate
            for candidate in base_candidates
            if candidate.kind == CandidateKind.CIRCLE
            and not self._shape_candidate_is_covered(candidate, produced_candidates)
            and (header_cutoff is None or candidate.center_y > header_cutoff)
            and 14 <= min(candidate.bbox_width, candidate.bbox_height) <= 60
        ]
        pool = [
            candidate
            for candidate in pool
            if self._fallback_circle_looks_like_callout(preview_image, candidate)
        ]
        if not pool:
            return []

        accepted: list[CalloutCandidate] = []
        clusters = sorted(
            self._cluster_low_res_circle_candidates(pool),
            key=lambda cluster: max(self._low_res_circle_geometry_priority(candidate) for candidate in cluster),
            reverse=True,
        )[:20]
        for cluster in clusters:
            span_x = max(member.center_x for member in cluster) - min(member.center_x for member in cluster)
            span_y = max(member.center_y for member in cluster) - min(member.center_y for member in cluster)
            cluster_span = max(span_x, span_y)
            review_limit = 1
            if len(cluster) >= 3 or cluster_span >= 18:
                review_limit = 2
            if len(cluster) >= 6 or cluster_span >= 28:
                review_limit = 3
            if len(cluster) >= 10 or cluster_span >= 42:
                review_limit = 4

            review_subset = sorted(cluster, key=self._low_res_circle_geometry_priority, reverse=True)[:review_limit]
            for candidate in review_subset:
                crop = self._build_candidate_ocr_crop(preview_image, candidate)
                suggestion = self._candidate_recognizer.recognize(crop, candidate.kind.value)
                coerced_label = self._coerce_to_allowed_label(allowed_labels, suggestion.label)
                if suggestion.label and not coerced_label:
                    continue
                if coerced_label:
                    suggestion.label = coerced_label
                if self._should_replace_candidate_suggestion(candidate, suggestion.label, suggestion.confidence, suggestion.source):
                    candidate.suggested_label = suggestion.label
                    candidate.suggested_confidence = suggestion.confidence
                    candidate.suggested_source = suggestion.source

            filtered_cluster = [
                candidate
                for candidate in review_subset
                if self._should_keep_low_res_circle_candidate(preview_image, candidate, header_cutoff)
            ]
            if filtered_cluster:
                ranked_cluster = sorted(filtered_cluster, key=self._low_res_circle_candidate_quality, reverse=True)
                selected: list[CalloutCandidate] = []
                selection_limit = 1 if cluster_span < 26 else 2
                if cluster_span >= 44:
                    selection_limit = 3
                for candidate in ranked_cluster:
                    if any(self._candidate_same_target(candidate, existing) or self._candidate_nearby(candidate, existing) for existing in selected):
                        continue
                    selected.append(candidate)
                    if len(selected) >= selection_limit:
                        break
                accepted.extend(selected)

        if not accepted:
            return []
        return accepted

    def _build_low_res_letter_tile_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> tuple[list[CalloutCandidate], list[CalloutCandidate]]:
        if not self._candidate_vlm_recognizer.is_enabled():
            return [], []

        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        pool = [
            candidate.model_copy(deep=True)
            for candidate in base_candidates
            if candidate.kind == CandidateKind.CIRCLE
            and (header_cutoff is None or candidate.center_y > header_cutoff)
            and 14 <= min(candidate.bbox_width, candidate.bbox_height) <= 60
            and self._fallback_circle_looks_like_callout(preview_image, candidate)
        ]
        if not pool:
            return [], []

        resolved: list[CalloutCandidate] = []
        reviewed_candidates: list[CalloutCandidate] = []
        has_letter_labels = bool(
            allowed_labels
            and any(any(char.isalpha() for char in label) or "-" in label for label in allowed_labels)
        )
        group_limit = 24 if (not allowed_labels or has_letter_labels) else 12
        if allowed_labels and len(allowed_labels) <= 4:
            group_limit = max(group_limit, 18)
        for group in self._build_low_res_letter_tile_groups(pool)[:group_limit]:
            if len(group) < 2:
                continue

            tile_image, index_map = self._render_low_res_letter_tile(preview_image, group)
            if tile_image is None or not index_map:
                continue

            resolved_any = False
            for tile_item in self._candidate_vlm_recognizer.resolve_indexed_tile(tile_image, heavy_sheet=True):
                if len(tile_item) >= 4:
                    index_letter, label, confidence, source = tile_item
                else:
                    index_letter, label, confidence = tile_item
                    source = "tile-vlm:consensus"
                coerced_label = self._coerce_to_allowed_label(allowed_labels, label)
                if label and not coerced_label:
                    continue
                if coerced_label:
                    label = coerced_label
                candidate = index_map.get(index_letter)
                if candidate is None:
                    continue
                if self._should_replace_candidate_suggestion(candidate, label, confidence, source):
                    candidate.suggested_label = label
                    candidate.suggested_confidence = confidence
                    candidate.suggested_source = source
                    candidate.updated_at = datetime.utcnow()
                if candidate.suggested_label:
                    local_crop = self._build_candidate_ocr_crop(preview_image, candidate)
                    local_suggestion = self._candidate_recognizer.recognize(local_crop, candidate.kind.value)
                    coerced_local = self._coerce_to_allowed_label(allowed_labels, local_suggestion.label)
                    if local_suggestion.label and not coerced_local:
                        continue
                    if coerced_local:
                        local_suggestion.label = coerced_local
                    trusted_vocab_hit = bool(
                        allowed_labels
                        and normalize_label(candidate.suggested_label) in allowed_labels
                        and (candidate.suggested_confidence or 0.0) >= 0.95
                    )
                    if not trusted_vocab_hit and not self._context_tile_label_survives_local_check(candidate, local_suggestion):
                        continue
                    if self._should_replace_candidate_suggestion(
                        candidate,
                        local_suggestion.label,
                        local_suggestion.confidence,
                        local_suggestion.source,
                    ):
                        candidate.suggested_label = local_suggestion.label
                        candidate.suggested_confidence = local_suggestion.confidence
                        candidate.suggested_source = local_suggestion.source
                        candidate.updated_at = datetime.utcnow()
                    resolved.append(candidate)
                    resolved_any = True

            if resolved_any:
                reviewed_candidates.extend(group)

        if not resolved:
            return [], reviewed_candidates

        filtered = [
            candidate
            for candidate in self._dedupe_composed_candidates(resolved)
            if self._should_keep_low_res_letter_tile_candidate(candidate, header_cutoff)
        ]
        return filtered, reviewed_candidates

    def _build_low_res_sequence_tile_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> tuple[list[CalloutCandidate], list[CalloutCandidate]]:
        if not self._candidate_vlm_recognizer.is_enabled():
            return [], []

        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        coverage_candidates = [
            candidate
            for candidate in produced_candidates
            if normalize_label(candidate.suggested_label)
            and normalize_label(candidate.suggested_label) in allowed_labels
        ]
        pool = [
            candidate.model_copy(deep=True)
            for candidate in base_candidates
            if candidate.kind == CandidateKind.CIRCLE
            and not self._candidate_is_strongly_covered(candidate, coverage_candidates)
            and (header_cutoff is None or candidate.center_y > header_cutoff)
            and 14 <= min(candidate.bbox_width, candidate.bbox_height) <= 60
            and self._fallback_circle_looks_like_callout(preview_image, candidate)
        ]
        if not pool:
            return [], []

        resolved: list[CalloutCandidate] = []
        reviewed_candidates: list[CalloutCandidate] = []
        for group in self._build_low_res_sequence_groups(pool)[:18]:
            tile_image, index_map = self._render_low_res_letter_tile(preview_image, group)
            if tile_image is None or not index_map:
                continue

            resolved_any = False
            for tile_item in self._candidate_vlm_recognizer.resolve_indexed_tile(tile_image, heavy_sheet=True):
                if len(tile_item) >= 4:
                    index_letter, label, confidence, source = tile_item
                else:
                    index_letter, label, confidence = tile_item
                    source = "tile-vlm:consensus"
                coerced_label = self._coerce_to_allowed_label(allowed_labels, label)
                if label and not coerced_label:
                    continue
                if coerced_label:
                    label = coerced_label
                candidate = index_map.get(index_letter)
                if candidate is None:
                    continue
                if self._should_replace_candidate_suggestion(candidate, label, confidence, source):
                    candidate.suggested_label = label
                    candidate.suggested_confidence = confidence
                    candidate.suggested_source = source
                    candidate.updated_at = datetime.utcnow()
                if candidate.suggested_label:
                    resolved.append(candidate)
                    resolved_any = True

            if resolved_any:
                reviewed_candidates.extend(group)

        if not resolved:
            return [], reviewed_candidates

        filtered = [
            candidate
            for candidate in self._dedupe_composed_candidates(resolved)
            if self._should_keep_low_res_letter_tile_candidate(candidate, header_cutoff)
        ]
        return filtered, reviewed_candidates

    def _build_low_res_context_tile_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> tuple[list[CalloutCandidate], list[CalloutCandidate]]:
        if not self._candidate_vlm_recognizer.is_enabled():
            return [], []

        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        pool = [
            candidate.model_copy(deep=True)
            for candidate in base_candidates
            if candidate.kind == CandidateKind.CIRCLE
            and not self._candidate_is_strongly_covered(candidate, produced_candidates)
            and (header_cutoff is None or candidate.center_y > header_cutoff)
            and 14 <= min(candidate.bbox_width, candidate.bbox_height) <= 60
            and self._fallback_circle_looks_like_callout(preview_image, candidate)
        ]
        if not pool:
            return [], []

        resolved: list[CalloutCandidate] = []
        reviewed_candidates: list[CalloutCandidate] = []
        for bounds, tile_candidates in self._build_low_res_context_tiles(preview_image, pool)[:20]:
            tile_image, index_map = self._render_low_res_letter_tile(preview_image, tile_candidates, bounds)
            if tile_image is None or not index_map:
                continue

            resolved_any = False
            for tile_item in self._candidate_vlm_recognizer.resolve_indexed_tile(tile_image, heavy_sheet=True):
                if len(tile_item) >= 4:
                    index_letter, label, confidence, source = tile_item
                else:
                    index_letter, label, confidence = tile_item
                    source = "tile-vlm:consensus"
                coerced_label = self._coerce_to_allowed_label(allowed_labels, label)
                if label and not coerced_label:
                    continue
                if coerced_label:
                    label = coerced_label
                candidate = index_map.get(index_letter)
                if candidate is None:
                    continue
                if self._should_replace_candidate_suggestion(candidate, label, confidence, source):
                    candidate.suggested_label = label
                    candidate.suggested_confidence = confidence
                    candidate.suggested_source = source
                    candidate.updated_at = datetime.utcnow()
                if candidate.suggested_label:
                    resolved.append(candidate)
                    resolved_any = True

            if resolved_any:
                reviewed_candidates.extend(tile_candidates)

        if not resolved:
            return [], reviewed_candidates

        filtered = [
            candidate
            for candidate in self._dedupe_composed_candidates(resolved)
            if self._should_keep_low_res_letter_tile_candidate(candidate, header_cutoff)
        ]
        return filtered, reviewed_candidates

    def _prune_low_res_candidates_after_tile_review(
        self,
        candidates: list[CalloutCandidate],
        tile_candidates: list[CalloutCandidate],
        reviewed_tile_candidates: list[CalloutCandidate],
    ) -> list[CalloutCandidate]:
        if not tile_candidates or not reviewed_tile_candidates:
            return candidates

        pruned: list[CalloutCandidate] = []
        for candidate in candidates:
            if any(self._candidate_same_target(candidate, tile_candidate) for tile_candidate in tile_candidates):
                continue

            touched_by_tile = any(
                self._candidate_same_target(candidate, reviewed_candidate) or self._candidate_nearby(candidate, reviewed_candidate)
                for reviewed_candidate in reviewed_tile_candidates
            )
            if not touched_by_tile:
                pruned.append(candidate)
                continue

            label = (candidate.suggested_label or "").strip()
            confidence = candidate.suggested_confidence or 0.0
            source = (candidate.suggested_source or "").lower()
            if candidate.kind == CandidateKind.TEXT and (
                ("-" in label or any(char.isalpha() for char in label))
                or confidence >= 0.88
            ):
                pruned.append(candidate)
                continue
            if candidate.kind in {CandidateKind.CIRCLE, CandidateKind.BOX}:
                if source.startswith("tile-vlm-consensus:") or source.startswith("tile-vlm:") or source.startswith("tile-openrouter-vlm:"):
                    pruned.append(candidate)
                    continue
                if label and not label.isdigit():
                    pruned.append(candidate)
                    continue
                if confidence >= 0.9 and (
                    source.startswith("openrouter-vlm:")
                    or source.startswith("openai-vlm:")
                ):
                    pruned.append(candidate)
                    continue
                continue
            pruned.append(candidate)

        return pruned

    @staticmethod
    def _candidate_has_strong_label(candidate: CalloutCandidate) -> bool:
        label = (candidate.suggested_label or "").strip()
        confidence = candidate.suggested_confidence or 0.0
        if not label or candidate.review_status == CandidateReviewStatus.REJECTED:
            return False

        source = (candidate.suggested_source or "").lower()
        if source.startswith("tile-vlm-consensus:") or source.startswith("tile-vlm:") or source.startswith("tile-openrouter-vlm:"):
            return confidence >= 0.7
        if source.startswith("openrouter-vlm:"):
            return confidence >= 0.86
        if source.startswith("openai-vlm:"):
            return confidence >= 0.88
        if "-" in label or any(char.isalpha() for char in label):
            return confidence >= 0.82
        return confidence >= 0.995

    @staticmethod
    def _apply_label_vocabulary(candidates: list[CalloutCandidate], vocabulary: set[str]) -> None:
        if not vocabulary:
            return
        normalized_vocab = {normalize_label(label) for label in vocabulary}
        for candidate in candidates:
            label = normalize_label(candidate.suggested_label)
            if not label:
                continue
            if label not in normalized_vocab:
                candidate.suggested_label = None
                candidate.suggested_confidence = None
                candidate.suggested_source = None
                candidate.updated_at = datetime.utcnow()

    @staticmethod
    def _label_allowed(allowed_labels: set[str] | None, label: str | None) -> bool:
        if not allowed_labels:
            return True
        normalized = normalize_label(label)
        if not normalized:
            return True
        return normalized in allowed_labels

    @staticmethod
    def _is_edit_distance_at_most_one(left: str, right: str) -> bool:
        if left == right:
            return True
        if abs(len(left) - len(right)) > 1:
            return False
        if len(left) == len(right):
            mismatches = sum(1 for a, b in zip(left, right) if a != b)
            return mismatches <= 1
        if len(left) > len(right):
            left, right = right, left
        i = 0
        j = 0
        mismatches = 0
        while i < len(left) and j < len(right):
            if left[i] == right[j]:
                i += 1
                j += 1
                continue
            mismatches += 1
            if mismatches > 1:
                return False
            j += 1
        return True

    def _coerce_to_allowed_label(self, allowed_labels: set[str] | None, label: str | None) -> str | None:
        if not label:
            return label
        if not allowed_labels:
            return label

        normalized = normalize_label(label)
        if not normalized:
            return label
        if normalized in allowed_labels:
            return label

        if not any(char.isalpha() for char in normalized) and "-" not in normalized:
            return None

        compatible = []
        for allowed in allowed_labels:
            if any(char.isalpha() for char in normalized) != any(char.isalpha() for char in allowed):
                continue
            if ("-" in normalized) != ("-" in allowed):
                continue
            if self._is_edit_distance_at_most_one(normalized, allowed):
                compatible.append(allowed)

        if not compatible:
            match = re.fullmatch(r"(\d+)([a-z]+)", normalized)
            if match:
                number = int(match.group(1))
                suffix = match.group(2)
                suffix_matches = []
                for allowed in allowed_labels:
                    allowed_match = re.fullmatch(r"(\d+)([a-z]+)", allowed)
                    if not allowed_match:
                        continue
                    if allowed_match.group(2) != suffix:
                        continue
                    if abs(int(allowed_match.group(1)) - number) <= 1:
                        suffix_matches.append(allowed)
                if len(suffix_matches) == 1:
                    compatible = suffix_matches

        if len(compatible) != 1:
            return None
        return compatible[0].upper()

    def _build_low_res_missing_label_ocr_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> list[CalloutCandidate]:
        if not allowed_labels:
            return []

        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        pool = [
            candidate.model_copy(deep=True)
            for candidate in base_candidates
            if candidate.kind == CandidateKind.CIRCLE
            and not self._candidate_is_strongly_covered(candidate, produced_candidates)
            and (header_cutoff is None or candidate.center_y > header_cutoff)
            and 14 <= min(candidate.bbox_width, candidate.bbox_height) <= 60
            and self._fallback_circle_looks_like_callout(preview_image, candidate)
        ]
        if not pool:
            return []

        accepted: list[CalloutCandidate] = []
        limit = 96 if len(allowed_labels) <= 20 else 160
        for candidate in sorted(pool, key=self._low_res_circle_geometry_priority, reverse=True)[:limit]:
            crop = self._build_candidate_ocr_crop(preview_image, candidate)
            suggestion = self._candidate_recognizer.recognize(crop, candidate.kind.value)
            coerced_label = self._coerce_to_allowed_label(allowed_labels, suggestion.label)
            if suggestion.label and not coerced_label:
                continue
            if coerced_label:
                suggestion.label = coerced_label
            if self._should_replace_candidate_suggestion(candidate, suggestion.label, suggestion.confidence, suggestion.source):
                candidate.suggested_label = suggestion.label
                candidate.suggested_confidence = suggestion.confidence
                candidate.suggested_source = suggestion.source
            if candidate.suggested_label and self._should_keep_low_res_circle_candidate(preview_image, candidate, header_cutoff):
                accepted.append(candidate)

        if not accepted:
            return []
        return self._dedupe_composed_candidates(accepted)

    def _build_low_res_missing_label_text_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> list[CalloutCandidate]:
        if not allowed_labels:
            return []

        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        text_candidates = [candidate for candidate in base_candidates if candidate.kind == CandidateKind.TEXT]
        pool = [
            candidate.model_copy(deep=True)
            for candidate in text_candidates
            if not self._candidate_is_strongly_covered(candidate, produced_candidates)
            and (header_cutoff is None or candidate.center_y > header_cutoff)
            and candidate.bbox_width <= 120
            and candidate.bbox_height <= 48
        ]
        if not pool:
            return []

        accepted: list[CalloutCandidate] = []
        limit = 160 if len(allowed_labels) <= 24 else 220
        allowed_upper = {label.upper() for label in allowed_labels if label}
        for candidate in sorted(pool, key=self._candidate_quality, reverse=True)[:limit]:
            neighbor_count = self._text_neighbor_count(candidate, text_candidates)
            crop = self._build_candidate_ocr_crop(preview_image, candidate)
            suggestion = self._candidate_recognizer.recognize(crop, candidate.kind.value)
            coerced_label = self._coerce_to_allowed_label(allowed_upper, suggestion.label)
            if suggestion.label and not coerced_label:
                continue
            if coerced_label:
                suggestion.label = coerced_label

            confidence = suggestion.confidence or 0.0
            compact_numeric = self._is_compact_numeric_text_candidate(candidate)
            if compact_numeric:
                min_conf = 0.6 if len(allowed_upper) <= 12 else 0.7
                if confidence < min_conf or neighbor_count > 2:
                    continue
            else:
                if confidence < 0.8 or neighbor_count > 1:
                    continue

            if self._should_replace_candidate_suggestion(candidate, suggestion.label, suggestion.confidence, suggestion.source):
                candidate.suggested_label = suggestion.label
                candidate.suggested_confidence = suggestion.confidence
                candidate.suggested_source = suggestion.source
            if candidate.suggested_label:
                accepted.append(candidate)

        if not accepted:
            return []
        return self._dedupe_composed_candidates(accepted)

    def _build_relaxed_text_candidates(self, preview_image: Image.Image) -> list[CalloutCandidate]:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return []

        rgb = preview_image.convert("RGB")
        gray = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2GRAY)
        height, width = gray.shape[:2]
        if height <= 0 or width <= 0:
            return []

        upscale = 2 if min(width, height) <= 1400 else 1
        working = (
            cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
            if upscale > 1
            else gray
        )
        blurred = cv2.GaussianBlur(working, (5, 5), 0)
        _, threshold = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(threshold, connectivity=8)
        min_side = min(working.shape[:2])
        min_char_h = max(6, int(min_side * 0.006))
        max_char_h = max(min_char_h + 8, int(min_side * 0.06))
        max_char_w = max(min_char_h + 18, int(min_side * 0.12))

        glyphs: list[dict[str, float]] = []
        for index in range(1, num_labels):
            x = int(stats[index, cv2.CC_STAT_LEFT])
            y = int(stats[index, cv2.CC_STAT_TOP])
            w = int(stats[index, cv2.CC_STAT_WIDTH])
            h = int(stats[index, cv2.CC_STAT_HEIGHT])
            area = int(stats[index, cv2.CC_STAT_AREA])
            if area < 8:
                continue
            if h < min_char_h or h > max_char_h:
                continue
            if w < 2 or w > max_char_w:
                continue
            fill_ratio = area / float(max(w * h, 1))
            if fill_ratio < 0.04 or fill_ratio > 0.92:
                continue
            cx, cy = centroids[index]
            glyphs.append(
                {
                    "x": float(x),
                    "y": float(y),
                    "w": float(w),
                    "h": float(h),
                    "area": float(area),
                    "cx": float(cx),
                    "cy": float(cy),
                }
            )

        if not glyphs:
            return []

        glyphs.sort(key=lambda item: (item["cy"], item["cx"]))
        clusters: list[list[dict[str, float]]] = []
        used = [False] * len(glyphs)

        for index, glyph in enumerate(glyphs):
            if used[index]:
                continue
            cluster = [glyph]
            used[index] = True
            changed = True
            while changed:
                changed = False
                cluster_left = min(item["x"] for item in cluster)
                cluster_top = min(item["y"] for item in cluster)
                cluster_right = max(item["x"] + item["w"] for item in cluster)
                cluster_bottom = max(item["y"] + item["h"] for item in cluster)
                cluster_height = cluster_bottom - cluster_top
                for other_index, other in enumerate(glyphs):
                    if used[other_index]:
                        continue
                    vertical_gate = max(cluster_height, other["h"]) * 0.9
                    horizontal_gap = min(
                        abs(other["x"] - cluster_right),
                        abs(cluster_left - (other["x"] + other["w"])),
                    )
                    same_row = abs(other["cy"] - (cluster_top + cluster_bottom) / 2) <= vertical_gate
                    overlaps_x = not (other["x"] + other["w"] < cluster_left or other["x"] > cluster_right)
                    close_enough = horizontal_gap <= max(24.0, cluster_height * 2.0) or overlaps_x
                    if same_row and close_enough:
                        cluster.append(other)
                        used[other_index] = True
                        changed = True

            if 1 <= len(cluster) <= 10:
                clusters.append(cluster)

        candidates: list[CalloutCandidate] = []
        max_text_width = max(60, int(min_side * 0.35))
        max_text_height = max(24, int(min_side * 0.08))
        for cluster in clusters:
            left = min(item["x"] for item in cluster)
            top = min(item["y"] for item in cluster)
            right = max(item["x"] + item["w"] for item in cluster)
            bottom = max(item["y"] + item["h"] for item in cluster)
            width_box = right - left
            height_box = bottom - top
            if width_box < 8 or height_box < min_char_h:
                continue
            if width_box > max_text_width or height_box > max_text_height:
                continue

            area_sum = sum(item["area"] for item in cluster)
            fill_ratio = area_sum / float(max(width_box * height_box, 1))
            if fill_ratio < 0.04 or fill_ratio > 0.85:
                continue

            score = float(len(cluster) * 40 + min(width_box, 140) * 0.5 + fill_ratio * 140)
            candidates.append(
                CalloutCandidate(
                    kind=CandidateKind.TEXT,
                    center_x=(left + width_box / 2) / upscale,
                    center_y=(top + height_box / 2) / upscale,
                    bbox_x=left / upscale,
                    bbox_y=top / upscale,
                    bbox_width=width_box / upscale,
                    bbox_height=height_box / upscale,
                    score=score,
                )
            )

        return candidates

    def _build_low_res_missing_label_document_text_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> list[CalloutCandidate]:
        if not allowed_labels:
            return []

        normalized_allowed = {normalize_label(label) for label in allowed_labels if normalize_label(label)}
        if not normalized_allowed:
            return []

        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        accepted: list[CalloutCandidate] = []

        min_side = min(preview_image.size)
        scales = [1.0]
        if min_side <= 1400:
            scales.append(2.0)

        for scale in scales:
            if scale == 1.0:
                scan_image = preview_image
            else:
                scan_image = preview_image.resize(
                    (int(preview_image.width * scale), int(preview_image.height * scale)),
                    Image.LANCZOS,
                )

            regions = self._candidate_recognizer.detect_document_text(scan_image, include_tiles=False)
            if not regions:
                continue

            for region in regions:
                region_label = normalize_label(region.label)
                if not region_label or region_label not in normalized_allowed:
                    continue
                if region.confidence < 0.55:
                    continue

                bbox_x = region.bbox_x / scale
                bbox_y = region.bbox_y / scale
                bbox_width = region.bbox_width / scale
                bbox_height = region.bbox_height / scale
                candidate = CalloutCandidate(
                    kind=CandidateKind.TEXT,
                    center_x=bbox_x + bbox_width / 2,
                    center_y=bbox_y + bbox_height / 2,
                    bbox_x=bbox_x,
                    bbox_y=bbox_y,
                    bbox_width=bbox_width,
                    bbox_height=bbox_height,
                    score=float(max(region.confidence, 0.0) * 100.0),
                )
                if header_cutoff is not None and candidate.center_y <= header_cutoff:
                    continue
                if self._candidate_is_strongly_covered(candidate, produced_candidates):
                    continue
                source_tag = f"document-ocr:{region.source}@{int(scale)}x"
                if self._should_replace_candidate_suggestion(candidate, region_label, region.confidence, source_tag):
                    candidate.suggested_label = region_label
                    candidate.suggested_confidence = region.confidence
                    candidate.suggested_source = source_tag
                if candidate.suggested_label:
                    accepted.append(candidate)

        if not accepted:
            return []
        return self._dedupe_composed_candidates(accepted)

    def _build_low_res_missing_label_locator_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> list[CalloutCandidate]:
        if not allowed_labels or not self._candidate_vlm_recognizer.is_enabled():
            return []

        normalized_allowed = {normalize_label(label) for label in allowed_labels if normalize_label(label)}
        if not normalized_allowed:
            return []

        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        items = self._candidate_vlm_recognizer.locate_labels(
            preview_image,
            sorted(normalized_allowed),
            heavy_sheet=True,
        )
        if not items:
            return []

        min_side = min(preview_image.size)
        box_size = min(64.0, max(22.0, min_side * 0.03))
        accepted: list[CalloutCandidate] = []
        for item in items:
            label = normalize_label(str(item.get("label") or ""))
            if not label or label not in normalized_allowed:
                continue
            try:
                x = float(item.get("x"))
                y = float(item.get("y"))
            except Exception:
                continue
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                continue
            confidence = float(item.get("confidence") or 0.0)

            center_x = x * preview_image.width
            center_y = y * preview_image.height
            candidate = CalloutCandidate(
                kind=CandidateKind.TEXT,
                center_x=center_x,
                center_y=center_y,
                bbox_x=max(0.0, center_x - box_size / 2),
                bbox_y=max(0.0, center_y - box_size / 2),
                bbox_width=box_size,
                bbox_height=box_size,
                score=float(max(confidence, 0.0) * 100.0 + 30.0),
            )
            if header_cutoff is not None and candidate.center_y <= header_cutoff:
                continue
            if self._candidate_is_strongly_covered(candidate, produced_candidates):
                continue

            local_crop = self._build_candidate_ocr_crop(preview_image, candidate)
            local_suggestion = self._candidate_recognizer.recognize(local_crop, candidate.kind.value)
            local_label = normalize_label(local_suggestion.label)
            if local_label and local_label in normalized_allowed:
                candidate.suggested_label = local_label
                candidate.suggested_confidence = local_suggestion.confidence
                candidate.suggested_source = "vlm-locate+ocr"
            elif confidence >= 0.78:
                candidate.suggested_label = label
                candidate.suggested_confidence = confidence
                candidate.suggested_source = "vlm-locate"
            else:
                continue

            accepted.append(candidate)

        if not accepted:
            return []
        return self._dedupe_composed_candidates(accepted)

    def _build_low_res_missing_label_text_vlm_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> list[CalloutCandidate]:
        if not allowed_labels or not self._candidate_vlm_recognizer.is_enabled():
            return []

        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        text_candidates = [candidate for candidate in base_candidates if candidate.kind == CandidateKind.TEXT]
        pool = [
            candidate.model_copy(deep=True)
            for candidate in text_candidates
            if not self._candidate_is_strongly_covered(candidate, produced_candidates)
            and (header_cutoff is None or candidate.center_y > header_cutoff)
            and candidate.bbox_width <= 120
            and candidate.bbox_height <= 48
        ]
        if not pool:
            return []

        accepted: list[CalloutCandidate] = []
        limit = 36 if len(allowed_labels) <= 16 else 48
        allowed_upper = sorted({label.upper() for label in allowed_labels if label})
        for candidate in sorted(pool, key=self._candidate_quality, reverse=True)[:limit]:
            local_crop = self._build_candidate_ocr_crop(preview_image, candidate)
            local_suggestion = self._candidate_recognizer.recognize(local_crop, candidate.kind.value)
            crop = self._build_candidate_vlm_crop(preview_image, candidate)
            suggestion = self._candidate_vlm_recognizer.recognize(
                crop,
                candidate.kind.value,
                local_label=local_suggestion.label,
                local_confidence=local_suggestion.confidence,
                allowed_labels=allowed_upper,
                heavy_sheet=True,
                use_consensus=False,
            )
            coerced_label = self._coerce_to_allowed_label({label.upper() for label in allowed_labels}, suggestion.label)
            if suggestion.label and not coerced_label:
                continue
            if coerced_label:
                suggestion.label = coerced_label
            confidence = suggestion.confidence or 0.0
            compact_numeric = self._is_compact_numeric_text_candidate(candidate)
            if compact_numeric and confidence < 0.6:
                continue
            if not compact_numeric and confidence < 0.75:
                continue
            if self._should_replace_candidate_suggestion(candidate, suggestion.label, suggestion.confidence, suggestion.source):
                candidate.suggested_label = suggestion.label
                candidate.suggested_confidence = suggestion.confidence
                candidate.suggested_source = suggestion.source
            if candidate.suggested_label:
                accepted.append(candidate)

        if not accepted:
            return []
        return self._dedupe_composed_candidates(accepted)

    def _build_low_res_missing_label_vlm_candidates(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        produced_candidates: list[CalloutCandidate],
        allowed_labels: set[str] | None = None,
    ) -> list[CalloutCandidate]:
        if not allowed_labels or not self._candidate_vlm_recognizer.is_enabled():
            return []
        targeted_labels = {normalize_label(label) for label in allowed_labels if normalize_label(label)}
        if not targeted_labels:
            return []

        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        coverage_candidates = [
            candidate
            for candidate in produced_candidates
            if normalize_label(candidate.suggested_label)
            and normalize_label(candidate.suggested_label) in targeted_labels
        ]
        pool = [
            candidate.model_copy(deep=True)
            for candidate in base_candidates
            if candidate.kind == CandidateKind.CIRCLE
            and not self._candidate_is_strongly_covered(candidate, coverage_candidates)
            and (header_cutoff is None or candidate.center_y > header_cutoff)
            and 14 <= min(candidate.bbox_width, candidate.bbox_height) <= 60
            and self._fallback_circle_looks_like_callout(preview_image, candidate)
        ]
        if not pool:
            return []

        accepted: list[CalloutCandidate] = []
        budget = 48 if len(targeted_labels) == 1 else min(32, max(10, len(targeted_labels) * 4))
        for candidate in sorted(pool, key=self._low_res_circle_geometry_priority, reverse=True)[:budget]:
            crop = self._build_candidate_ocr_crop(preview_image, candidate)
            local_suggestion = self._candidate_recognizer.recognize(crop, candidate.kind.value)
            vlm_suggestion = self._candidate_vlm_recognizer.recognize(
                crop,
                candidate.kind.value,
                local_label=local_suggestion.label,
                local_confidence=local_suggestion.confidence,
                allowed_labels=sorted({label.upper() for label in targeted_labels}),
                heavy_sheet=True,
            )
            coerced_label = self._coerce_to_allowed_label(targeted_labels, vlm_suggestion.label)
            if vlm_suggestion.label and not coerced_label:
                continue
            if coerced_label:
                vlm_suggestion.label = coerced_label
            if self._should_replace_candidate_suggestion(
                candidate,
                vlm_suggestion.label,
                vlm_suggestion.confidence,
                vlm_suggestion.source,
            ):
                candidate.suggested_label = vlm_suggestion.label
                candidate.suggested_confidence = vlm_suggestion.confidence
                candidate.suggested_source = vlm_suggestion.source
            if candidate.suggested_label and self._should_keep_low_res_circle_candidate(preview_image, candidate, header_cutoff):
                accepted.append(candidate)

        if not accepted:
            return []
        return self._dedupe_composed_candidates(accepted)

    def _recover_missing_low_res_vocabulary_labels(
        self,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
        candidates: list[CalloutCandidate],
        vocabulary: set[str],
    ) -> list[CalloutCandidate]:
        normalized_vocab = {normalize_label(label) for label in vocabulary if normalize_label(label)}
        if not normalized_vocab:
            return candidates

        baseline_candidates = list(candidates)
        recovered = list(candidates)
        present = {
            normalize_label(candidate.suggested_label)
            for candidate in recovered
            if normalize_label(candidate.suggested_label)
        }
        missing = normalized_vocab - present
        if not missing:
            return recovered

        doc_text = self._build_low_res_missing_label_document_text_candidates(
            preview_image,
            base_candidates,
            recovered,
            allowed_labels=missing,
        )
        if doc_text:
            recovered = self._dedupe_composed_candidates([*recovered, *doc_text])
            recovered = self._prune_low_res_oversized_circle_candidates(recovered)
            present = {
                normalize_label(candidate.suggested_label)
                for candidate in recovered
                if normalize_label(candidate.suggested_label)
            }
            missing = normalized_vocab - present
        if not missing:
            return recovered

        locator_candidates = self._build_low_res_missing_label_locator_candidates(
            preview_image,
            base_candidates,
            recovered,
            allowed_labels=missing,
        )
        if locator_candidates:
            recovered = self._dedupe_composed_candidates([*recovered, *locator_candidates])
            recovered = self._prune_low_res_oversized_circle_candidates(recovered)
            present = {
                normalize_label(candidate.suggested_label)
                for candidate in recovered
                if normalize_label(candidate.suggested_label)
            }
            missing = normalized_vocab - present
        if not missing:
            return recovered

        direct_ocr = self._build_low_res_missing_label_ocr_candidates(
            preview_image,
            base_candidates,
            recovered,
            allowed_labels=missing,
        )
        if direct_ocr:
            recovered = self._dedupe_composed_candidates([*recovered, *direct_ocr])
            recovered = self._prune_low_res_oversized_circle_candidates(recovered)
            present = {
                normalize_label(candidate.suggested_label)
                for candidate in recovered
                if normalize_label(candidate.suggested_label)
            }
            missing = normalized_vocab - present
        if not missing:
            return recovered

        relaxed_text = self._build_relaxed_text_candidates(preview_image)
        augmented_base = [*base_candidates, *relaxed_text] if relaxed_text else base_candidates

        text_ocr = self._build_low_res_missing_label_text_candidates(
            preview_image,
            augmented_base,
            recovered,
            allowed_labels=missing,
        )
        if text_ocr:
            recovered = self._dedupe_composed_candidates([*recovered, *text_ocr])
            recovered = self._prune_low_res_oversized_circle_candidates(recovered)
            present = {
                normalize_label(candidate.suggested_label)
                for candidate in recovered
                if normalize_label(candidate.suggested_label)
            }
            missing = normalized_vocab - present
            if not missing:
                return recovered

        text_vlm = self._build_low_res_missing_label_text_vlm_candidates(
            preview_image,
            augmented_base,
            recovered,
            allowed_labels=missing,
        )
        if text_vlm:
            recovered = self._dedupe_composed_candidates([*recovered, *text_vlm])
            recovered = self._prune_low_res_oversized_circle_candidates(recovered)
            present = {
                normalize_label(candidate.suggested_label)
                for candidate in recovered
                if normalize_label(candidate.suggested_label)
            }
            missing = normalized_vocab - present
            if not missing:
                return recovered

        recovery_steps = (
            ("list", self._build_low_res_missing_label_vlm_candidates),
            ("tuple", self._build_low_res_letter_tile_candidates),
            ("tuple", self._build_low_res_context_tile_candidates),
            ("tuple", self._build_low_res_sequence_tile_candidates),
        )

        for step_kind, step in recovery_steps:
            if not missing:
                break

            if step_kind == "list":
                extra = step(
                    preview_image,
                    base_candidates,
                    recovered,
                    allowed_labels=missing,
                )
                if extra:
                    recovered = self._dedupe_composed_candidates([*recovered, *extra])
            else:
                extra, reviewed = step(
                    preview_image,
                    base_candidates,
                    recovered,
                    allowed_labels=missing,
                )
                if extra:
                    recovered = self._prune_low_res_candidates_after_tile_review(recovered, extra, reviewed)
                    recovered = self._dedupe_composed_candidates([*recovered, *extra])

            recovered = self._prune_low_res_oversized_circle_candidates(recovered)
            present = {
                normalize_label(candidate.suggested_label)
                for candidate in recovered
                if normalize_label(candidate.suggested_label)
            }
            missing = normalized_vocab - present

        if 0 < len(missing) <= 12:
            for target_label in sorted(missing):
                single_target = {target_label}
                targeted_ocr = self._build_low_res_missing_label_ocr_candidates(
                    preview_image,
                    base_candidates,
                    recovered,
                    allowed_labels=single_target,
                )
                if targeted_ocr:
                    recovered = self._dedupe_composed_candidates([*recovered, *targeted_ocr])
                    recovered = self._prune_low_res_oversized_circle_candidates(recovered)
                present = {
                    normalize_label(candidate.suggested_label)
                    for candidate in recovered
                    if normalize_label(candidate.suggested_label)
                }
                if target_label in present:
                    continue

                targeted_vlm = self._build_low_res_missing_label_vlm_candidates(
                    preview_image,
                    base_candidates,
                    recovered,
                    allowed_labels=single_target,
                )
                if targeted_vlm:
                    recovered = self._dedupe_composed_candidates([*recovered, *targeted_vlm])
                    recovered = self._prune_low_res_oversized_circle_candidates(recovered)
                present = {
                    normalize_label(candidate.suggested_label)
                    for candidate in recovered
                    if normalize_label(candidate.suggested_label)
                }
                if target_label in present:
                    continue

                targeted_letter_tile, targeted_reviewed = self._build_low_res_letter_tile_candidates(
                    preview_image,
                    base_candidates,
                    recovered,
                    allowed_labels=single_target,
                )
                if targeted_letter_tile:
                    recovered = self._prune_low_res_candidates_after_tile_review(
                        recovered,
                        targeted_letter_tile,
                        targeted_reviewed,
                    )
                    recovered = self._dedupe_composed_candidates([*recovered, *targeted_letter_tile])
                    recovered = self._prune_low_res_oversized_circle_candidates(recovered)
                present = {
                    normalize_label(candidate.suggested_label)
                    for candidate in recovered
                    if normalize_label(candidate.suggested_label)
                }
                if target_label in present:
                    continue

                targeted_context_tile, targeted_context_reviewed = self._build_low_res_context_tile_candidates(
                    preview_image,
                    base_candidates,
                    recovered,
                    allowed_labels=single_target,
                )
                if targeted_context_tile:
                    recovered = self._prune_low_res_candidates_after_tile_review(
                        recovered,
                        targeted_context_tile,
                        targeted_context_reviewed,
                    )
                    recovered = self._dedupe_composed_candidates([*recovered, *targeted_context_tile])
                    recovered = self._prune_low_res_oversized_circle_candidates(recovered)

            present = {
                normalize_label(candidate.suggested_label)
                for candidate in recovered
                if normalize_label(candidate.suggested_label)
            }
            missing = normalized_vocab - present
            if not missing:
                recovered = self._dedupe_composed_candidates([*baseline_candidates, *recovered])
                return self._prune_low_res_oversized_circle_candidates(recovered)

        recovered = self._dedupe_composed_candidates([*baseline_candidates, *recovered])
        return self._prune_low_res_oversized_circle_candidates(recovered)

    @staticmethod
    def _dedupe_nearby_equal_labels(candidates: list[CalloutCandidate], max_distance: float = 48.0) -> list[CalloutCandidate]:
        kept: list[CalloutCandidate] = []
        for candidate in sorted(
            candidates,
            key=lambda item: ((item.suggested_confidence or 0.0), item.score),
            reverse=True,
        ):
            label = normalize_label(candidate.suggested_label)
            if not label:
                kept.append(candidate)
                continue
            duplicate = next(
                (
                    existing
                    for existing in kept
                    if normalize_label(existing.suggested_label) == label
                    and hypot(existing.center_x - candidate.center_x, existing.center_y - candidate.center_y) <= max_distance
                    and InMemorySessionStore._candidate_same_target(existing, candidate)
                ),
                None,
            )
            if duplicate is not None:
                continue
            kept.append(candidate)
        kept.sort(key=lambda item: (item.center_y, item.center_x))
        return kept

    @staticmethod
    def _should_allow_outside_vocab(label: str | None, confidence: float | None) -> bool:
        if not label:
            return False
        normalized = normalize_label(label)
        if not normalized:
            return False
        if confidence is None:
            return False
        if confidence < 0.9:
            return False
        if any(ch.isalpha() for ch in normalized):
            return True
        if "-" in normalized and confidence >= 0.92:
            return True
        return False

    @staticmethod
    def _maybe_expand_vocab(allowed_labels: set[str] | None, label: str | None) -> None:
        if not allowed_labels:
            return
        normalized = normalize_label(label)
        if not normalized:
            return
        allowed_labels.add(normalized)

    def _candidate_is_strongly_covered(
        self,
        source_candidate: CalloutCandidate,
        produced_candidates: list[CalloutCandidate],
    ) -> bool:
        for existing in produced_candidates:
            if self._candidate_same_target(source_candidate, existing) and self._candidate_has_strong_label(existing):
                return True
        return False

    def _build_low_res_letter_tile_groups(
        self,
        candidates: list[CalloutCandidate],
    ) -> list[list[CalloutCandidate]]:
        groups: list[list[CalloutCandidate]] = []
        used_ids: set[str] = set()
        ordered = sorted(candidates, key=self._low_res_circle_candidate_quality, reverse=True)

        for anchor in ordered:
            if anchor.candidate_id in used_ids:
                continue

            group = [anchor]
            left = anchor.bbox_x
            top = anchor.bbox_y
            right = anchor.bbox_x + anchor.bbox_width
            bottom = anchor.bbox_y + anchor.bbox_height

            neighbors = sorted(
                [candidate for candidate in ordered if candidate.candidate_id not in used_ids and candidate.candidate_id != anchor.candidate_id],
                key=lambda candidate: (
                    hypot(candidate.center_x - anchor.center_x, candidate.center_y - anchor.center_y),
                    -self._low_res_circle_candidate_quality(candidate),
                ),
            )
            for candidate in neighbors:
                if hypot(candidate.center_x - anchor.center_x, candidate.center_y - anchor.center_y) > 170:
                    continue
                next_left = min(left, candidate.bbox_x)
                next_top = min(top, candidate.bbox_y)
                next_right = max(right, candidate.bbox_x + candidate.bbox_width)
                next_bottom = max(bottom, candidate.bbox_y + candidate.bbox_height)
                if (next_right - next_left) > 180 or (next_bottom - next_top) > 180:
                    continue
                group.append(candidate)
                left = next_left
                top = next_top
                right = next_right
                bottom = next_bottom
                if len(group) >= 8:
                    break

            if len(group) >= 2:
                groups.append(sorted(group, key=lambda candidate: (candidate.center_y, candidate.center_x)))
                used_ids.update(candidate.candidate_id for candidate in group)

        return groups

    def _build_low_res_context_tiles(
        self,
        preview_image: Image.Image,
        candidates: list[CalloutCandidate],
    ) -> list[tuple[tuple[int, int, int, int], list[CalloutCandidate]]]:
        if not candidates:
            return []

        tile_width = max(240, min(360, int(preview_image.width * 0.42)))
        tile_height = max(240, min(360, int(preview_image.height * 0.36)))
        step_x = max(150, int(tile_width * 0.64))
        step_y = max(150, int(tile_height * 0.64))

        tiles: list[tuple[float, tuple[int, int, int, int], list[CalloutCandidate]]] = []
        seen_signatures: set[tuple[str, ...]] = set()
        for top in self._iter_low_res_tile_positions(preview_image.height, tile_height, step_y):
            for left in self._iter_low_res_tile_positions(preview_image.width, tile_width, step_x):
                right = min(preview_image.width, left + tile_width)
                bottom = min(preview_image.height, top + tile_height)
                members = [
                    candidate
                    for candidate in candidates
                    if (left - 10) <= candidate.center_x <= (right + 10)
                    and (top - 10) <= candidate.center_y <= (bottom + 10)
                ]
                if not members:
                    continue

                ranked_members = sorted(members, key=self._low_res_circle_geometry_priority, reverse=True)
                selected: list[CalloutCandidate] = []
                for candidate in ranked_members:
                    if any(
                        self._candidate_same_target(candidate, existing)
                        or self._candidate_nearby(candidate, existing)
                        for existing in selected
                    ):
                        continue
                    selected.append(candidate)
                    if len(selected) >= 10:
                        break

                if not selected:
                    continue

                signature = tuple(sorted(candidate.candidate_id for candidate in selected))
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                tile_score = len(selected) * 100 + sum(
                    max(0.0, self._low_res_circle_candidate_quality(candidate))
                    for candidate in selected
                )
                tiles.append((tile_score, (left, top, right, bottom), selected))

        tiles.sort(key=lambda item: item[0], reverse=True)
        return [(bounds, selected) for _, bounds, selected in tiles]

    @staticmethod
    def _iter_low_res_tile_positions(limit: int, tile_size: int, step: int) -> list[int]:
        if limit <= tile_size:
            return [0]

        positions: list[int] = []
        current = 0
        while True:
            positions.append(current)
            if current + tile_size >= limit:
                break
            next_value = min(limit - tile_size, current + step)
            if next_value == current:
                break
            current = next_value
        return positions

    def _build_low_res_sequence_groups(
        self,
        candidates: list[CalloutCandidate],
    ) -> list[list[CalloutCandidate]]:
        signatures: set[tuple[str, ...]] = set()
        groups: list[tuple[float, list[CalloutCandidate]]] = []
        ordered = sorted(candidates, key=self._low_res_circle_geometry_priority, reverse=True)

        def maybe_add_group(members: list[CalloutCandidate]) -> None:
            if len(members) < 3:
                return
            members = sorted(members, key=lambda candidate: (candidate.center_y, candidate.center_x))
            signature = tuple(candidate.candidate_id for candidate in members)
            if signature in signatures:
                return
            signatures.add(signature)
            span_x = max(candidate.center_x for candidate in members) - min(candidate.center_x for candidate in members)
            span_y = max(candidate.center_y for candidate in members) - min(candidate.center_y for candidate in members)
            aligned = max(span_x, span_y) / max(1.0, min(span_x, span_y) + 1.0)
            score = len(members) * 100 + aligned * 10 + sum(
                max(0.0, self._low_res_circle_candidate_quality(candidate))
                for candidate in members
            )
            groups.append((score, members))

        for anchor in ordered:
            diameter = max(14.0, min(anchor.bbox_width, anchor.bbox_height))

            vertical_gate = max(18.0, diameter * 1.35)
            vertical = [
                candidate
                for candidate in candidates
                if abs(candidate.center_x - anchor.center_x) <= vertical_gate
                and abs(candidate.center_y - anchor.center_y) <= 240
            ]
            vertical = sorted(vertical, key=lambda candidate: candidate.center_y)
            vertical = [
                candidate
                for index, candidate in enumerate(vertical)
                if index == 0
                or (candidate.center_y - vertical[index - 1].center_y) <= 120
            ]
            if len(vertical) >= 3:
                span_x = max(candidate.center_x for candidate in vertical) - min(candidate.center_x for candidate in vertical)
                span_y = max(candidate.center_y for candidate in vertical) - min(candidate.center_y for candidate in vertical)
                if span_y >= max(48.0, span_x * 1.6):
                    maybe_add_group(vertical[:8])

            horizontal_gate = max(18.0, diameter * 1.35)
            horizontal = [
                candidate
                for candidate in candidates
                if abs(candidate.center_y - anchor.center_y) <= horizontal_gate
                and abs(candidate.center_x - anchor.center_x) <= 260
            ]
            horizontal = sorted(horizontal, key=lambda candidate: candidate.center_x)
            horizontal = [
                candidate
                for index, candidate in enumerate(horizontal)
                if index == 0
                or (candidate.center_x - horizontal[index - 1].center_x) <= 130
            ]
            if len(horizontal) >= 3:
                span_x = max(candidate.center_x for candidate in horizontal) - min(candidate.center_x for candidate in horizontal)
                span_y = max(candidate.center_y for candidate in horizontal) - min(candidate.center_y for candidate in horizontal)
                if span_x >= max(48.0, span_y * 1.6):
                    maybe_add_group(horizontal[:8])

        groups.sort(key=lambda item: item[0], reverse=True)
        return [members for _, members in groups]

    @staticmethod
    def _context_tile_label_survives_local_check(
        candidate: CalloutCandidate,
        local_suggestion: CandidateSuggestion,
    ) -> bool:
        label = normalize_label(candidate.suggested_label)
        confidence = candidate.suggested_confidence or 0.0
        if not label:
            return False

        local_label = normalize_label(local_suggestion.label)
        local_confidence = local_suggestion.confidence or 0.0
        if local_label:
            if local_label == label:
                return True
            if local_confidence >= 0.72:
                return False
            if label.isdigit() and len(label) == 1:
                return confidence >= 0.96
            return False

        if label.isdigit():
            if len(label) == 1:
                return confidence >= 0.82
            return confidence >= 0.9
        return confidence >= 0.86

    def _render_low_res_letter_tile(
        self,
        preview_image: Image.Image,
        candidates: list[CalloutCandidate],
        bounds: tuple[int, int, int, int] | None = None,
    ) -> tuple[Image.Image | None, dict[str, CalloutCandidate]]:
        if not candidates:
            return None, {}

        if bounds is None:
            pad = 24
            left = max(0, int(min(candidate.bbox_x for candidate in candidates) - pad))
            top = max(0, int(min(candidate.bbox_y for candidate in candidates) - pad))
            right = min(preview_image.width, int(max(candidate.bbox_x + candidate.bbox_width for candidate in candidates) + pad))
            bottom = min(preview_image.height, int(max(candidate.bbox_y + candidate.bbox_height for candidate in candidates) + pad))
        else:
            left, top, right, bottom = bounds
        if right - left < 18 or bottom - top < 18:
            return None, {}

        crop = ImageOps.autocontrast(preview_image.crop((left, top, right, bottom)).convert("RGB"), cutoff=1)
        scale = 1
        max_side = max(crop.size)
        if max_side < 640:
            scale = min(8, max(2, int(round(640 / max(max_side, 1)))))
        enlarged = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
        overlay = Image.new("RGBA", enlarged.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = self._load_annotation_font(max(16, min(28, int(18 * scale * 0.8))))
        index_map: dict[str, CalloutCandidate] = {}

        for index, candidate in enumerate(candidates[:12]):
            letter = string.ascii_uppercase[index]
            index_map[letter] = candidate
            center_x = int(round((candidate.center_x - left) * scale))
            center_y = int(round((candidate.center_y - top) * scale))
            radius = max(12, int(min(candidate.bbox_width, candidate.bbox_height) * scale * 0.42))
            stroke_width = max(2, int(scale * 0.8))
            draw.ellipse(
                (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
                outline=(255, 82, 82, 255),
                width=stroke_width,
            )

            badge_radius = max(12, int(radius * 0.62))
            badge_x = min(enlarged.width - badge_radius - 4, center_x + radius + badge_radius - 2)
            badge_y = max(badge_radius + 4, center_y - radius - badge_radius + 2)
            draw.ellipse(
                (badge_x - badge_radius, badge_y - badge_radius, badge_x + badge_radius, badge_y + badge_radius),
                fill=(17, 24, 39, 230),
                outline=(255, 255, 255, 240),
                width=max(1, stroke_width - 1),
            )
            text_bbox = draw.textbbox((0, 0), letter, font=font, stroke_width=1)
            text_x = badge_x - (text_bbox[2] - text_bbox[0]) / 2
            text_y = badge_y - (text_bbox[3] - text_bbox[1]) / 2 - 1
            draw.text(
                (text_x, text_y),
                letter,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=1,
                stroke_fill=(17, 24, 39, 255),
            )

        return Image.alpha_composite(enlarged.convert("RGBA"), overlay).convert("RGB"), index_map

    @staticmethod
    def _should_keep_low_res_letter_tile_candidate(
        candidate: CalloutCandidate,
        header_cutoff: float | None,
    ) -> bool:
        label = (candidate.suggested_label or "").strip()
        confidence = candidate.suggested_confidence or 0.0
        source = (candidate.suggested_source or "").lower()
        if not (
            source.startswith("tile-vlm-consensus:")
            or source.startswith("tile-vlm:")
            or source.startswith("tile-openrouter-vlm:")
        ):
            return False
        if header_cutoff is not None and candidate.center_y <= header_cutoff:
            return False
        if not label:
            return False
        if "-" in label or any(char.isalpha() for char in label):
            return confidence >= 0.64
        if label.isdigit():
            if len(label) == 1:
                return confidence >= 0.58
            if len(label) <= 3:
                return confidence >= 0.62
        return False

    @staticmethod
    def _prune_low_res_oversized_circle_candidates(candidates: list[CalloutCandidate]) -> list[CalloutCandidate]:
        pruned: list[CalloutCandidate] = []
        for candidate in candidates:
            if candidate.kind != CandidateKind.CIRCLE:
                pruned.append(candidate)
                continue

            label = (candidate.suggested_label or "").strip()
            confidence = candidate.suggested_confidence or 0.0
            source = (candidate.suggested_source or "").lower()
            diameter = min(candidate.bbox_width, candidate.bbox_height)
            if label.isdigit() and len(label) <= 2 and diameter > 54:
                continue
            if label.isdigit() and len(label) == 2 and diameter >= 40 and confidence < 0.9 and not source.startswith("easy-circle"):
                continue
            pruned.append(candidate)
        return pruned

    @staticmethod
    def _shape_candidate_is_covered(candidate: CalloutCandidate, produced_candidates: list[CalloutCandidate]) -> bool:
        for existing in produced_candidates:
            if InMemorySessionStore._candidate_same_target(candidate, existing):
                return True
        return False

    @staticmethod
    def _should_retry_low_res_final_candidate_ocr(candidate: CalloutCandidate) -> bool:
        label = (candidate.suggested_label or "").strip()
        confidence = candidate.suggested_confidence or 0.0

        if not label:
            return True
        if "-" in label or any(char.isalpha() for char in label):
            return True
        if confidence < 0.84:
            return True
        if label.isdigit() and len(label) == 2 and any(char in {"0", "8"} for char in label):
            return True
        return False

    def _cluster_low_res_circle_candidates(
        self,
        candidates: list[CalloutCandidate],
    ) -> list[list[CalloutCandidate]]:
        clusters: list[list[CalloutCandidate]] = []
        for candidate in sorted(candidates, key=self._low_res_circle_candidate_quality, reverse=True):
            cluster = next(
                (
                    existing
                    for existing in clusters
                    if any(self._low_res_circle_candidates_overlap(candidate, member) for member in existing)
                ),
                None,
            )
            if cluster is None:
                clusters.append([candidate])
            else:
                cluster.append(candidate)
        return clusters

    @staticmethod
    def _low_res_circle_candidates_overlap(left: CalloutCandidate, right: CalloutCandidate) -> bool:
        if InMemorySessionStore._candidate_same_target(left, right):
            return True
        distance = hypot(left.center_x - right.center_x, left.center_y - right.center_y)
        gate = max(
            12.0,
            max(min(left.bbox_width, left.bbox_height), min(right.bbox_width, right.bbox_height)) * 0.72,
        )
        return distance <= gate

    @staticmethod
    def _low_res_circle_candidate_quality(candidate: CalloutCandidate) -> float:
        label = candidate.suggested_label or ""
        confidence = candidate.suggested_confidence or 0.0
        source = (candidate.suggested_source or "").lower()
        diameter = min(candidate.bbox_width, candidate.bbox_height)
        detector_score = min(candidate.score / 240.0, 1.0)
        score = confidence + detector_score * 0.28
        if source.startswith("easy-circle"):
            score += 0.14
        elif source.startswith("tile-vlm-consensus:") or source.startswith("tile-vlm:") or source.startswith("tile-openrouter-vlm:"):
            score += 0.34
        elif source.startswith("circle"):
            score += 0.06
        elif source.startswith("gemini-vlm:"):
            score += 0.2
        elif source.startswith("openai-vlm:"):
            score += 0.24
        elif source.startswith("openrouter-vlm:"):
            score += 0.26
        if label.isdigit() and len(label) == 1:
            score += 0.04
        elif label.isdigit() and len(label) == 2:
            score += 0.02
        if diameter > 52:
            score -= 0.22
        elif diameter > 44:
            score -= 0.08
        return score

    @staticmethod
    def _low_res_circle_geometry_priority(candidate: CalloutCandidate) -> tuple[float, float]:
        diameter = min(candidate.bbox_width, candidate.bbox_height)
        size_penalty = abs(diameter - 24.0)
        return (candidate.score, -size_penalty)

    def _should_keep_low_res_circle_candidate(
        self,
        preview_image: Image.Image,
        candidate: CalloutCandidate,
        header_cutoff: float | None,
    ) -> bool:
        label = candidate.suggested_label or ""
        confidence = candidate.suggested_confidence or 0.0
        diameter = min(candidate.bbox_width, candidate.bbox_height)
        source = (candidate.suggested_source or "").lower()

        if not label.isdigit() or len(label) > 2:
            return False
        if header_cutoff is not None and candidate.center_y <= header_cutoff:
            return False
        if not self._fallback_circle_looks_like_callout(preview_image, candidate):
            return False

        if diameter <= 24:
            return confidence >= 0.54
        if diameter <= 44:
            if source.startswith("easy-circle"):
                return confidence >= 0.62
            return confidence >= 0.74
        if diameter <= 60:
            return label.isdigit() and len(label) <= 2 and confidence >= 0.64
        return False

    @staticmethod
    def _low_res_fallback_shape_priority(candidate: CalloutCandidate) -> tuple[float, float]:
        return (-candidate.score, candidate.bbox_width * candidate.bbox_height)

    def _should_keep_fallback_circle_candidate(
        self,
        preview_image: Image.Image,
        candidate: CalloutCandidate,
        header_cutoff: float | None,
    ) -> bool:
        label = candidate.suggested_label or ""
        if not label:
            return False
        if header_cutoff is not None and candidate.center_y <= header_cutoff:
            return False

        if not self._fallback_circle_looks_like_callout(preview_image, candidate):
            return False

        confidence = candidate.suggested_confidence or 0.0
        if label.isdigit():
            if len(label) == 1:
                return confidence >= 0.97
            if len(label) <= 2:
                return confidence >= 0.9
            return False
        return ("-" in label or any(char.isalpha() for char in label)) and confidence >= 0.92

    @staticmethod
    def _fallback_circle_looks_like_callout(preview_image: Image.Image, candidate: CalloutCandidate) -> bool:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return True

        left = max(0, int(candidate.bbox_x))
        top = max(0, int(candidate.bbox_y))
        right = min(preview_image.width, int(candidate.bbox_x + candidate.bbox_width))
        bottom = min(preview_image.height, int(candidate.bbox_y + candidate.bbox_height))
        crop = preview_image.crop((left, top, right, bottom)).convert("L")
        if min(crop.size) < 12:
            return False

        inset = max(1, int(min(crop.size) * 0.16))
        inner = crop.crop((inset, inset, max(inset + 2, crop.width - inset), max(inset + 2, crop.height - inset)))
        if min(inner.size) < 6:
            return False

        enlarged = inner.resize((inner.width * 8, inner.height * 8), Image.Resampling.LANCZOS)
        array = np.array(enlarged)
        _, bw = cv2.threshold(array, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        fill_ratio = float(bw.mean()) / 255.0
        if fill_ratio < 0.16 or fill_ratio > 0.48:
            return False

        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
        component_areas = [
            int(stats[index, cv2.CC_STAT_AREA])
            for index in range(1, num_labels)
            if int(stats[index, cv2.CC_STAT_AREA]) >= 20
        ]
        if not component_areas or len(component_areas) > 5:
            return False

        inner_area = max(bw.shape[0] * bw.shape[1], 1)
        largest_ratio = max(component_areas) / float(inner_area)
        if len(component_areas) == 1 and largest_ratio > 0.38:
            return False

        return True

    @staticmethod
    def _should_keep_fallback_box_candidate(candidate: CalloutCandidate, header_cutoff: float | None) -> bool:
        label = candidate.suggested_label or ""
        if not label:
            return False
        if header_cutoff is not None and candidate.center_y <= header_cutoff:
            return False
        confidence = candidate.suggested_confidence or 0.0
        if "-" in label or any(char.isalpha() for char in label):
            return confidence >= 0.84
        if label.isdigit():
            return confidence >= 0.9 and len(label) <= 2
        return False

    @staticmethod
    def _eligible_for_final_candidate_ocr(candidate: CalloutCandidate) -> bool:
        label = candidate.suggested_label or ""
        confidence = candidate.suggested_confidence or 0.0

        if candidate.kind == CandidateKind.TEXT:
            if not label:
                return True
            if confidence < 0.9:
                return True
            return "-" in label or any(char.isalpha() for char in label)

        if not label:
            return True
        if confidence < 0.95:
            return True
        if label.isdigit() and len(label) == 1:
            return True
        return "-" in label or any(char.isalpha() for char in label)

    @staticmethod
    def _final_candidate_ocr_priority(candidate: CalloutCandidate) -> tuple[int, float, float]:
        label = candidate.suggested_label or ""
        kind_rank = 0 if candidate.kind in {CandidateKind.CIRCLE, CandidateKind.BOX} else 1
        extension_rank = 0 if label.isdigit() and len(label) == 1 else 1 if not label else 2
        return (
            kind_rank + extension_rank * 2,
            candidate.suggested_confidence or 0.0,
            candidate.bbox_width * candidate.bbox_height,
        )

    def _compose_candidates(
        self,
        session_id: str,
        preview_image: Image.Image,
        base_candidates: list[CalloutCandidate],
    ) -> tuple[list[CalloutCandidate], list[CandidateAssociation]]:
        circles = [candidate for candidate in base_candidates if candidate.kind == CandidateKind.CIRCLE]
        boxes = [candidate for candidate in base_candidates if candidate.kind == CandidateKind.BOX]
        texts = [candidate for candidate in base_candidates if candidate.kind == CandidateKind.TEXT]
        low_res_circle_mode = min(preview_image.width, preview_image.height) <= 1100 and len(circles) >= 180
        text_neighbor_counts = {
            candidate.candidate_id: self._text_neighbor_count(candidate, texts)
            for candidate in texts
        }
        header_cutoff = self._infer_header_cutoff(base_candidates, preview_image.width, preview_image.height)
        topology_observations = self._leader_topology.analyze(preview_image, [*circles, *boxes], header_cutoff)
        self._apply_topology_observations([*circles, *boxes], topology_observations)
        candidate_associations = self._build_candidate_associations(
            texts,
            circles,
            boxes,
            header_cutoff,
            low_res_circle_mode=low_res_circle_mode,
        )
        associations_by_text = self._group_candidate_associations_by_text(candidate_associations)
        shape_candidates_by_id = {candidate.candidate_id: candidate for candidate in [*circles, *boxes]}

        produced: list[CalloutCandidate] = []
        used_shape_ids: set[str] = set()
        used_text_ids: set[str] = set()

        for text_candidate in sorted(texts, key=self._candidate_quality, reverse=True):
            if not text_candidate.suggested_label:
                continue
            neighbor_count = text_neighbor_counts.get(text_candidate.candidate_id, 0)
            if self._is_probable_footer_text_candidate(
                text_candidate,
                neighbor_count,
                preview_image.width,
                preview_image.height,
            ):
                continue

            matched_association = self._select_candidate_association(
                text_candidate.candidate_id,
                associations_by_text,
                used_shape_ids,
            )
            if matched_association is not None:
                matched_shape = shape_candidates_by_id.get(matched_association.shape_candidate_id)
                if matched_shape is not None:
                    produced.append(
                        self._compose_pair_candidate(
                            session_id=session_id,
                            preview_image=preview_image,
                            shape_candidate=matched_shape,
                            text_candidate=text_candidate,
                            output_kind=matched_association.shape_kind,
                            association=matched_association,
                        )
                    )
                    used_shape_ids.add(matched_shape.candidate_id)
                    used_text_ids.add(text_candidate.candidate_id)
                    continue

            matched_circle = self._find_best_circle_for_text(
                text_candidate,
                circles,
                used_shape_ids,
                header_cutoff,
                low_res_circle_mode=low_res_circle_mode,
                preview_image=preview_image,
            )
            if matched_circle:
                produced.append(
                    self._compose_pair_candidate(
                        session_id=session_id,
                        preview_image=preview_image,
                        shape_candidate=matched_circle,
                        text_candidate=text_candidate,
                        output_kind=CandidateKind.CIRCLE,
                    )
                )
                used_shape_ids.add(matched_circle.candidate_id)
                used_text_ids.add(text_candidate.candidate_id)
                continue

            matched_box = self._find_best_box_for_text(text_candidate, boxes, used_shape_ids, header_cutoff)
            if matched_box:
                produced.append(
                    self._compose_pair_candidate(
                        session_id=session_id,
                        preview_image=preview_image,
                        shape_candidate=matched_box,
                        text_candidate=text_candidate,
                        output_kind=CandidateKind.BOX,
                    )
                )
                used_shape_ids.add(matched_box.candidate_id)
                used_text_ids.add(text_candidate.candidate_id)
                continue

            if self._should_keep_text_candidate(
                text_candidate,
                neighbor_count,
                header_cutoff,
                low_res_circle_mode=low_res_circle_mode,
            ):
                produced.append(text_candidate)
                used_text_ids.add(text_candidate.candidate_id)

        for box_candidate in sorted(boxes, key=self._candidate_quality, reverse=True):
            if box_candidate.candidate_id in used_shape_ids:
                continue
            if self._should_keep_box_candidate(box_candidate, header_cutoff):
                produced.append(box_candidate)

        for circle_candidate in sorted(circles, key=self._candidate_quality, reverse=True):
            if circle_candidate.candidate_id in used_shape_ids:
                continue
            if low_res_circle_mode:
                continue
            if self._should_keep_circle_candidate(circle_candidate, header_cutoff):
                produced.append(circle_candidate)

        return self._dedupe_composed_candidates(produced), candidate_associations

    def _build_candidate_associations(
        self,
        texts: list[CalloutCandidate],
        circles: list[CalloutCandidate],
        boxes: list[CalloutCandidate],
        header_cutoff: float | None,
        *,
        low_res_circle_mode: bool,
    ) -> list[CandidateAssociation]:
        circle_config = AssociationBuildConfig(
            shape_kind=CandidateKind.CIRCLE,
            source="shape-text:circle",
            min_score=0.72,
            topology_weight=0.08,
        )
        box_config = AssociationBuildConfig(
            shape_kind=CandidateKind.BOX,
            source="shape-text:box",
            min_score=0.72,
            topology_weight=0.06,
        )

        circle_associations = self._candidate_association_builder.build(
            texts,
            circles,
            config=circle_config,
            score_fn=self._circle_pair_score,
            header_cutoff=header_cutoff,
            relaxed_match_fn=self._relaxed_circle_association_match if low_res_circle_mode else None,
        )
        box_associations = self._candidate_association_builder.build(
            texts,
            boxes,
            config=box_config,
            score_fn=self._box_pair_score,
            header_cutoff=header_cutoff,
        )
        return [*circle_associations, *box_associations]

    @staticmethod
    def _group_candidate_associations_by_text(
        associations: list[CandidateAssociation],
    ) -> dict[str, list[CandidateAssociation]]:
        grouped: dict[str, list[CandidateAssociation]] = {}
        for association in sorted(
            associations,
            key=lambda item: (item.score, item.geometry_score, item.topology_score or 0.0),
            reverse=True,
        ):
            grouped.setdefault(association.text_candidate_id, []).append(association)
        return grouped

    @staticmethod
    def _select_candidate_association(
        text_candidate_id: str,
        associations_by_text: dict[str, list[CandidateAssociation]],
        used_shape_ids: set[str],
    ) -> CandidateAssociation | None:
        for association in associations_by_text.get(text_candidate_id, []):
            if association.shape_candidate_id in used_shape_ids:
                continue
            return association
        return None

    def _relaxed_circle_association_match(
        self,
        text_candidate: CalloutCandidate,
        shape_candidate: CalloutCandidate,
        score: float,
    ) -> bool:
        return (
            (text_candidate.suggested_confidence or 0.0) >= 0.9
            and score >= 0.5
            and self._candidate_quality(text_candidate) >= self._candidate_quality(shape_candidate) + 0.08
        )

    def _find_best_circle_for_text(
        self,
        text_candidate: CalloutCandidate,
        circles: list[CalloutCandidate],
        used_shape_ids: set[str],
        header_cutoff: float | None,
        low_res_circle_mode: bool = False,
        preview_image: Image.Image | None = None,
    ) -> CalloutCandidate | None:
        if header_cutoff is not None and max(text_candidate.center_y, 0.0) <= header_cutoff:
            return None
        best_match: CalloutCandidate | None = None
        best_score = 0.0
        for circle_candidate in circles:
            if circle_candidate.candidate_id in used_shape_ids:
                continue
            if header_cutoff is not None and circle_candidate.center_y <= header_cutoff:
                continue
            pair_score = self._circle_pair_score(text_candidate, circle_candidate)
            pair_score += min(circle_candidate.topology_score or 0.0, 1.0) * 0.08
            if pair_score > best_score:
                best_score = pair_score
                best_match = circle_candidate
        if best_score >= 0.72:
            return best_match

        if (
            low_res_circle_mode
            and best_match is not None
            and (text_candidate.suggested_confidence or 0.0) >= 0.9
            and best_score >= 0.5
            and self._candidate_quality(text_candidate) >= self._candidate_quality(best_match) + 0.08
        ):
            return best_match

        if low_res_circle_mode and preview_image is not None:
            inferred_circle = self._infer_local_circle_for_text(preview_image, text_candidate, header_cutoff)
            if inferred_circle is not None:
                inferred_score = self._circle_pair_score(text_candidate, inferred_circle)
                if inferred_score >= 0.62:
                    return inferred_circle
                if (text_candidate.suggested_confidence or 0.0) >= 0.9 and inferred_score >= 0.52:
                    return inferred_circle

        return None

    def _find_best_box_for_text(
        self,
        text_candidate: CalloutCandidate,
        boxes: list[CalloutCandidate],
        used_shape_ids: set[str],
        header_cutoff: float | None,
    ) -> CalloutCandidate | None:
        if header_cutoff is not None and max(text_candidate.center_y, 0.0) <= header_cutoff and "-" not in (text_candidate.suggested_label or ""):
            return None
        best_match: CalloutCandidate | None = None
        best_score = 0.0
        for box_candidate in boxes:
            if box_candidate.candidate_id in used_shape_ids:
                continue
            if header_cutoff is not None and box_candidate.center_y <= header_cutoff:
                continue
            pair_score = self._box_pair_score(text_candidate, box_candidate)
            pair_score += min(box_candidate.topology_score or 0.0, 1.0) * 0.06
            if pair_score > best_score:
                best_score = pair_score
                best_match = box_candidate
        return best_match if best_score >= 0.72 else None

    @staticmethod
    def _circle_pair_score(text_candidate: CalloutCandidate, circle_candidate: CalloutCandidate) -> float:
        circle_diameter = min(circle_candidate.bbox_width, circle_candidate.bbox_height)
        radius = circle_diameter / 2
        if radius <= 0:
            return 0.0

        distance = hypot(text_candidate.center_x - circle_candidate.center_x, text_candidate.center_y - circle_candidate.center_y)
        if distance > radius * 0.82:
            return 0.0

        text_max_dim = max(text_candidate.bbox_width, text_candidate.bbox_height)
        if text_max_dim > circle_diameter * 0.98:
            return 0.0

        centered_score = max(0.0, 1.0 - (distance / max(radius, 1)))
        size_target = max(circle_diameter * 0.84, 1.0)
        size_score = max(0.0, 1.0 - abs((text_max_dim / size_target) - 1.0))
        confidence_score = text_candidate.suggested_confidence or 0.0
        return centered_score * 0.55 + size_score * 0.15 + confidence_score * 0.3

    @staticmethod
    def _box_pair_score(text_candidate: CalloutCandidate, box_candidate: CalloutCandidate) -> float:
        margin_x = max(3.0, box_candidate.bbox_width * 0.12)
        margin_y = max(3.0, box_candidate.bbox_height * 0.12)
        inside = (
            text_candidate.bbox_x >= box_candidate.bbox_x - margin_x
            and text_candidate.bbox_y >= box_candidate.bbox_y - margin_y
            and text_candidate.bbox_x + text_candidate.bbox_width <= box_candidate.bbox_x + box_candidate.bbox_width + margin_x
            and text_candidate.bbox_y + text_candidate.bbox_height <= box_candidate.bbox_y + box_candidate.bbox_height + margin_y
        )
        if not inside:
            return 0.0

        width_ratio = text_candidate.bbox_width / max(box_candidate.bbox_width, 1)
        height_ratio = text_candidate.bbox_height / max(box_candidate.bbox_height, 1)
        if width_ratio >= 0.92 or height_ratio >= 0.92:
            return 0.0

        confidence_score = text_candidate.suggested_confidence or 0.0
        fit_score = max(0.0, 1.0 - abs(width_ratio - 0.5)) * 0.6 + max(0.0, 1.0 - abs(height_ratio - 0.45)) * 0.4
        return fit_score * 0.55 + confidence_score * 0.45

    def _infer_local_circle_for_text(
        self,
        preview_image: Image.Image,
        text_candidate: CalloutCandidate,
        header_cutoff: float | None,
    ) -> CalloutCandidate | None:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return None

        if header_cutoff is not None and text_candidate.center_y <= header_cutoff:
            return None

        max_dim = max(text_candidate.bbox_width, text_candidate.bbox_height)
        if max_dim <= 0 or max_dim > 34:
            return None

        pad = max(18, int(max_dim * 2.8))
        center_x = int(round(text_candidate.center_x))
        center_y = int(round(text_candidate.center_y))
        left = max(0, center_x - pad)
        top = max(0, center_y - pad)
        right = min(preview_image.width, center_x + pad)
        bottom = min(preview_image.height, center_y + pad)
        if right - left < 18 or bottom - top < 18:
            return None

        gray = np.array(preview_image.crop((left, top, right, bottom)).convert("L"))
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        min_radius = max(7, int(max_dim * 0.75))
        max_radius = max(min_radius + 4, int(max_dim * 2.8))
        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=1.1,
            minDist=max(12, int(max_dim * 1.1)),
            param1=90,
            param2=13,
            minRadius=min_radius,
            maxRadius=max_radius,
        )
        if circles is None:
            return None

        best_candidate: CalloutCandidate | None = None
        best_score = 0.0
        for local_circle in np.round(circles[0]).astype("int"):
            x, y, radius = int(local_circle[0]), int(local_circle[1]), int(local_circle[2])
            abs_x = left + x
            abs_y = top + y
            bbox_x = max(0, abs_x - radius)
            bbox_y = max(0, abs_y - radius)
            bbox_w = min(preview_image.width - bbox_x, radius * 2)
            bbox_h = min(preview_image.height - bbox_y, radius * 2)
            if bbox_w < 12 or bbox_h < 12:
                continue

            synthetic_circle = CalloutCandidate(
                kind=CandidateKind.CIRCLE,
                center_x=float(abs_x),
                center_y=float(abs_y),
                bbox_x=float(bbox_x),
                bbox_y=float(bbox_y),
                bbox_width=float(bbox_w),
                bbox_height=float(bbox_h),
                score=float(radius * 8 + 160),
                review_status=CandidateReviewStatus.PENDING,
            )
            if not self._fallback_circle_looks_like_callout(preview_image, synthetic_circle):
                continue

            pair_score = self._circle_pair_score(text_candidate, synthetic_circle)
            if pair_score > best_score:
                best_score = pair_score
                best_candidate = synthetic_circle

        return best_candidate

    def _compose_pair_candidate(
        self,
        session_id: str,
        preview_image: Image.Image,
        shape_candidate: CalloutCandidate,
        text_candidate: CalloutCandidate,
        output_kind: CandidateKind,
        association: CandidateAssociation | None = None,
    ) -> CalloutCandidate:
        left = min(shape_candidate.bbox_x, text_candidate.bbox_x)
        top = min(shape_candidate.bbox_y, text_candidate.bbox_y)
        right = max(shape_candidate.bbox_x + shape_candidate.bbox_width, text_candidate.bbox_x + text_candidate.bbox_width)
        bottom = max(shape_candidate.bbox_y + shape_candidate.bbox_height, text_candidate.bbox_y + text_candidate.bbox_height)
        composed = CalloutCandidate(
            kind=output_kind,
            center_x=shape_candidate.center_x,
            center_y=shape_candidate.center_y,
            bbox_x=left,
            bbox_y=top,
            bbox_width=right - left,
            bbox_height=bottom - top,
            score=round(shape_candidate.score + text_candidate.score * 0.75, 2),
            suggested_label=text_candidate.suggested_label,
            suggested_confidence=max(
                text_candidate.suggested_confidence or 0.0,
                min(0.99, (text_candidate.suggested_confidence or 0.0) * 0.82 + self._candidate_quality(shape_candidate) * 0.18),
            ),
            suggested_source=(
                f"{text_candidate.suggested_source or 'ocr'}+{output_kind.value}"
                if association is None
                else f"{text_candidate.suggested_source or 'ocr'}+{output_kind.value}+assoc"
            ),
            topology_score=shape_candidate.topology_score,
            topology_source=shape_candidate.topology_source,
            leader_anchor_x=association.leader_anchor_x if association is not None else shape_candidate.leader_anchor_x,
            leader_anchor_y=association.leader_anchor_y if association is not None else shape_candidate.leader_anchor_y,
            review_status=CandidateReviewStatus.PENDING,
        )
        crop = self._build_candidate_crop(
            preview_image,
            composed.bbox_x,
            composed.bbox_y,
            composed.bbox_width,
            composed.bbox_height,
        )
        composed.crop_url = self._write_candidate_crop(session_id, composed.candidate_id, crop)
        return composed

    @staticmethod
    def _text_neighbor_count(candidate: CalloutCandidate, text_candidates: list[CalloutCandidate]) -> int:
        count = 0
        row_gate = max(candidate.bbox_height, 12) * 1.8
        x_gate = max(candidate.bbox_width, 20) * 8.0
        for other in text_candidates:
            if other.candidate_id == candidate.candidate_id:
                continue
            if abs(other.center_y - candidate.center_y) <= row_gate and abs(other.center_x - candidate.center_x) <= x_gate:
                count += 1
        return count

    @staticmethod
    def _should_keep_text_candidate(
        candidate: CalloutCandidate,
        neighbor_count: int,
        header_cutoff: float | None,
        low_res_circle_mode: bool = False,
    ) -> bool:
        if not candidate.suggested_label:
            return False
        confidence = candidate.suggested_confidence or 0.0
        has_dash = "-" in candidate.suggested_label
        has_alpha = any(char.isalpha() for char in candidate.suggested_label)
        compact_numeric = InMemorySessionStore._is_compact_numeric_text_candidate(candidate)
        if header_cutoff is not None and candidate.center_y <= header_cutoff and not has_dash:
            return False
        if has_dash:
            return confidence >= 0.22
        if has_alpha:
            return confidence >= 0.58 and neighbor_count <= 1
        if low_res_circle_mode:
            if neighbor_count >= 2 and not compact_numeric:
                return False
            if compact_numeric:
                return confidence >= 0.9 and neighbor_count <= 1
            return confidence >= 0.96 and neighbor_count == 0
        if neighbor_count >= 2 and not has_dash and not compact_numeric:
            return False
        if neighbor_count >= 5 and not compact_numeric:
            return False
        if compact_numeric:
            return confidence >= 0.5
        return confidence >= 0.42

    @staticmethod
    def _is_compact_numeric_text_candidate(candidate: CalloutCandidate) -> bool:
        label = candidate.suggested_label or ""
        if not label.isdigit() or len(label) > 2:
            return False
        max_width = max(26.0, candidate.bbox_height * 1.55)
        max_height = 34.0
        return candidate.bbox_width <= max_width and candidate.bbox_height <= max_height

    @staticmethod
    def _is_probable_footer_text_candidate(
        candidate: CalloutCandidate,
        neighbor_count: int,
        image_width: int,
        image_height: int,
    ) -> bool:
        label = candidate.suggested_label or ""
        if not label.isdigit() or len(label) > 2:
            return False
        if candidate.center_y < image_height * 0.93:
            return False
        if neighbor_count > 1:
            return False

        near_left_edge = candidate.center_x <= image_width * 0.18
        near_center = abs(candidate.center_x - image_width / 2) <= image_width * 0.12
        near_right_edge = candidate.center_x >= image_width * 0.82
        if not (near_left_edge or near_center or near_right_edge):
            return False

        return candidate.bbox_height <= 24 and candidate.bbox_width <= max(22.0, candidate.bbox_height * 1.4)

    @staticmethod
    def _should_keep_box_candidate(candidate: CalloutCandidate, header_cutoff: float | None) -> bool:
        if header_cutoff is not None and candidate.center_y <= header_cutoff:
            return False
        label = candidate.suggested_label or ""
        confidence = candidate.suggested_confidence or 0.0
        topology = candidate.topology_score or 0.0
        if not label:
            return False
        if "-" in label or any(char.isalpha() for char in label):
            return confidence >= 0.34 or (topology >= 0.46 and confidence >= 0.26)
        if label.isdigit() and len(label) == 1:
            return confidence >= 0.78 or (topology >= 0.54 and confidence >= 0.68)
        return confidence >= 0.64 or (topology >= 0.52 and confidence >= 0.56)

    @staticmethod
    def _should_keep_circle_candidate(candidate: CalloutCandidate, header_cutoff: float | None) -> bool:
        label = candidate.suggested_label or ""
        if not label.isdigit():
            return False
        if header_cutoff is not None and candidate.center_y <= header_cutoff:
            return False
        if min(candidate.bbox_width, candidate.bbox_height) < 18:
            return False
        confidence = candidate.suggested_confidence or 0.0
        topology = candidate.topology_score or 0.0
        if len(label) > 2:
            return False
        return bool(confidence >= 0.88 or (topology >= 0.58 and confidence >= 0.8))

    @staticmethod
    def _infer_header_cutoff(
        candidates: list[CalloutCandidate],
        image_width: int,
        image_height: int,
    ) -> float | None:
        top_limit = image_height * 0.35
        shapes = [
            candidate
            for candidate in candidates
            if candidate.kind in {CandidateKind.CIRCLE, CandidateKind.BOX}
            and candidate.center_y <= top_limit
        ]
        top_candidates = [
            candidate
            for candidate in candidates
            if candidate.suggested_label
            and candidate.kind == CandidateKind.TEXT
            and candidate.center_y <= top_limit
            and not InMemorySessionStore._text_candidate_near_shape(candidate, shapes)
        ]
        if len(top_candidates) < 4:
            return None

        rows: list[dict[str, float]] = []
        for candidate in sorted(top_candidates, key=lambda item: item.center_y):
            placed = False
            for row in rows:
                row_gate = max(candidate.bbox_height, row["max_h"]) * 1.35
                if abs(candidate.center_y - row["center_y"]) <= row_gate:
                    row["count"] += 1
                    row["left"] = min(row["left"], candidate.bbox_x)
                    row["right"] = max(row["right"], candidate.bbox_x + candidate.bbox_width)
                    row["bottom"] = max(row["bottom"], candidate.bbox_y + candidate.bbox_height)
                    row["center_y"] = (row["center_y"] * (row["count"] - 1) + candidate.center_y) / row["count"]
                    row["max_h"] = max(row["max_h"], candidate.bbox_height)
                    placed = True
                    break
            if not placed:
                rows.append(
                    {
                        "count": 1.0,
                        "left": candidate.bbox_x,
                        "right": candidate.bbox_x + candidate.bbox_width,
                        "bottom": candidate.bbox_y + candidate.bbox_height,
                        "center_y": candidate.center_y,
                        "max_h": candidate.bbox_height,
                    }
                )

        dense_rows = [
            row
            for row in rows
            if row["count"] >= 3 and (row["right"] - row["left"]) >= image_width * 0.24
        ]
        if len(dense_rows) < 2:
            return None

        dense_bottom = max(row["bottom"] for row in dense_rows)
        next_candidate_top = min(
            (
                candidate.bbox_y
                for candidate in candidates
                if candidate.suggested_label
                and candidate.bbox_y > dense_bottom + 8
                and any(char.isdigit() for char in candidate.suggested_label)
            ),
            default=None,
        )

        if next_candidate_top is not None:
            gap = next_candidate_top - dense_bottom
            if gap >= max(22.0, image_height * 0.012):
                cutoff = dense_bottom + gap * 0.45
                return min(image_height * 0.34, cutoff)

        cutoff = dense_bottom + max(12.0, image_height * 0.012)
        return min(image_height * 0.3, cutoff)

    @staticmethod
    def _text_candidate_near_shape(text_candidate: CalloutCandidate, shapes: list[CalloutCandidate]) -> bool:
        for shape in shapes:
            if shape.kind == CandidateKind.CIRCLE:
                radius = min(shape.bbox_width, shape.bbox_height) / 2
                if radius <= 0:
                    continue
                distance = hypot(text_candidate.center_x - shape.center_x, text_candidate.center_y - shape.center_y)
                if distance <= radius * 0.92:
                    return True
                continue

            margin_x = max(3.0, shape.bbox_width * 0.15)
            margin_y = max(3.0, shape.bbox_height * 0.15)
            if (
                text_candidate.bbox_x >= shape.bbox_x - margin_x
                and text_candidate.bbox_y >= shape.bbox_y - margin_y
                and text_candidate.bbox_x + text_candidate.bbox_width <= shape.bbox_x + shape.bbox_width + margin_x
                and text_candidate.bbox_y + text_candidate.bbox_height <= shape.bbox_y + shape.bbox_height + margin_y
            ):
                return True
        return False

    def _dedupe_composed_candidates(self, candidates: list[CalloutCandidate]) -> list[CalloutCandidate]:
        deduped: list[CalloutCandidate] = []
        for candidate in sorted(candidates, key=self._candidate_quality, reverse=True):
            duplicate_index = next(
                (
                    index
                    for index, existing in enumerate(deduped)
                    if normalize_label(existing.suggested_label) == normalize_label(candidate.suggested_label)
                    and self._candidate_nearby(existing, candidate)
                    and self._candidate_overlap(existing, candidate)
                ),
                None,
            )
            if duplicate_index is None:
                duplicate_index = next(
                    (
                        index
                        for index, existing in enumerate(deduped)
                        if self._candidate_same_target(existing, candidate)
                    ),
                    None,
                )
            if duplicate_index is None:
                deduped.append(candidate)
                continue
            if self._candidate_quality(candidate) > self._candidate_quality(deduped[duplicate_index]):
                deduped[duplicate_index] = candidate
        deduped.sort(key=lambda item: (item.center_y, item.center_x))
        return deduped

    @staticmethod
    def _candidate_quality(candidate: CalloutCandidate) -> float:
        confidence = candidate.suggested_confidence or 0.0
        topology = candidate.topology_score or 0.0
        score_component = min(candidate.score / 260.0, 1.0)
        kind_bonus = {
            CandidateKind.CIRCLE: 0.18,
            CandidateKind.BOX: 0.14,
            CandidateKind.TEXT: 0.08,
        }.get(candidate.kind, 0.0)
        label_bonus = 0.05 if candidate.suggested_label and ("-" in candidate.suggested_label or any(char.isalpha() for char in candidate.suggested_label)) else 0.0
        source_bonus = 0.08 if candidate.suggested_source and ("+circle" in candidate.suggested_source or "+box" in candidate.suggested_source) else 0.0
        if candidate.suggested_source and candidate.suggested_source.startswith("openai-vlm:"):
            source_bonus += 0.14
        if candidate.suggested_source and (
            candidate.suggested_source.startswith("tile-vlm-consensus:")
            or candidate.suggested_source.startswith("tile-vlm:")
            or candidate.suggested_source.startswith("tile-openrouter-vlm:")
        ):
            source_bonus += 0.34
        if candidate.suggested_source and candidate.suggested_source.startswith("openrouter-vlm:"):
            source_bonus += 0.18
        topology_bonus = topology * 0.18 if candidate.kind in {CandidateKind.CIRCLE, CandidateKind.BOX} else 0.0
        return confidence * 0.58 + score_component * 0.19 + kind_bonus + label_bonus + source_bonus + topology_bonus

    @staticmethod
    def _apply_topology_observations(candidates: list[CalloutCandidate], observations: dict[str, object]) -> None:
        for candidate in candidates:
            observation = observations.get(candidate.candidate_id)
            if observation is None:
                continue
            candidate.topology_score = getattr(observation, "topology_score", None)
            candidate.topology_source = getattr(observation, "topology_source", None)
            candidate.leader_anchor_x = getattr(observation, "leader_anchor_x", None)
            candidate.leader_anchor_y = getattr(observation, "leader_anchor_y", None)

    def _build_auto_markers(self, session: AnnotationSession) -> tuple[list[Marker], int, int, int]:
        marker_groups: dict[str, list[CalloutCandidate]] = {}
        for candidate in session.candidates:
            if candidate.review_status != CandidateReviewStatus.PENDING or not candidate.suggested_label:
                continue
            group_key = candidate.conflict_group or candidate.candidate_id
            marker_groups.setdefault(group_key, []).append(candidate)

        auto_markers: list[Marker] = []
        auto_accepted = 0
        auto_review = 0

        for grouped_candidates in marker_groups.values():
            ranked = sorted(grouped_candidates, key=self._candidate_quality, reverse=True)
            top_candidate = ranked[0]
            top_quality = self._candidate_quality(top_candidate)
            second_quality = self._candidate_quality(ranked[1]) if len(ranked) > 1 else 0.0
            has_candidate_ambiguity = self._candidate_has_pipeline_conflict(
                top_candidate,
                session.pipeline_conflicts,
                PipelineConflictType.CANDIDATE_AMBIGUITY,
            )
            has_association_ambiguity = self._candidate_has_association_ambiguity(
                top_candidate,
                session.pipeline_conflicts,
            )

            if top_quality < 0.34:
                continue

            resolved_conflict = len(ranked) == 1 or top_quality >= second_quality + 0.08
            marker_status = (
                MarkerStatus.AI_DETECTED
                if resolved_conflict and top_quality >= 0.7 and not has_candidate_ambiguity and not has_association_ambiguity
                else MarkerStatus.AI_REVIEW
            )
            auto_markers.append(self._marker_from_candidate(top_candidate, marker_status))
            candidate_resolved_for_pipeline = marker_status == MarkerStatus.AI_DETECTED
            if candidate_resolved_for_pipeline:
                top_candidate.review_status = CandidateReviewStatus.ACCEPTED
                top_candidate.updated_at = datetime.utcnow()

            if marker_status == MarkerStatus.AI_DETECTED:
                auto_accepted += 1
            else:
                auto_review += 1

            if resolved_conflict and candidate_resolved_for_pipeline:
                for other_candidate in ranked[1:]:
                    if (
                        normalize_label(other_candidate.suggested_label) == normalize_label(top_candidate.suggested_label)
                        and self._candidate_same_target(top_candidate, other_candidate)
                    ):
                        other_candidate.review_status = CandidateReviewStatus.REJECTED
                        other_candidate.updated_at = datetime.utcnow()

        pending_count = sum(1 for candidate in session.candidates if candidate.review_status == CandidateReviewStatus.PENDING)
        return auto_markers, auto_accepted, auto_review, pending_count

    @staticmethod
    def _candidate_has_pipeline_conflict(
        candidate: CalloutCandidate,
        pipeline_conflicts: list,
        conflict_type: PipelineConflictType,
    ) -> bool:
        candidate_label = normalize_label(candidate.suggested_label)

        for conflict in pipeline_conflicts:
            if conflict.type != conflict_type:
                continue

            if candidate.candidate_id in (conflict.candidate_ids or []):
                return True

            conflict_labels = {normalize_label(label) for label in conflict.related_labels if normalize_label(label)}
            if conflict_labels and candidate_label not in conflict_labels:
                continue

            if (
                conflict.bbox_x is not None
                and conflict.bbox_y is not None
                and conflict.bbox_width is not None
                and conflict.bbox_height is not None
            ):
                right = conflict.bbox_x + conflict.bbox_width
                bottom = conflict.bbox_y + conflict.bbox_height
                if conflict.bbox_x <= candidate.center_x <= right and conflict.bbox_y <= candidate.center_y <= bottom:
                    return True

        return False

    @staticmethod
    def _candidate_has_association_ambiguity(
        candidate: CalloutCandidate,
        pipeline_conflicts: list,
    ) -> bool:
        return InMemorySessionStore._candidate_has_pipeline_conflict(
            candidate,
            pipeline_conflicts,
            PipelineConflictType.ASSOCIATION_AMBIGUITY,
        )

    @staticmethod
    def _marker_from_candidate(candidate: CalloutCandidate, status: MarkerStatus) -> Marker:
        point_type = MarkerPointType.CENTER
        x = candidate.center_x
        y = candidate.center_y
        return Marker(
            label=candidate.suggested_label,
            x=x,
            y=y,
            point_type=point_type,
            status=status,
            confidence=candidate.suggested_confidence,
            created_by=Actor.AI,
            updated_by=Actor.AI,
        )

    def _build_candidate_crop(
        self,
        image: Image.Image,
        bbox_x: float,
        bbox_y: float,
        bbox_width: float,
        bbox_height: float,
    ) -> Image.Image:
        margin = max(8, int(max(bbox_width, bbox_height) * 0.5))
        left = max(0, int(bbox_x - margin))
        top = max(0, int(bbox_y - margin))
        right = min(image.width, int(bbox_x + bbox_width + margin))
        bottom = min(image.height, int(bbox_y + bbox_height + margin))
        return image.crop((left, top, right, bottom))

    def _build_candidate_ocr_crop(
        self,
        image: Image.Image,
        candidate: CalloutCandidate,
    ) -> Image.Image:
        max_dim = max(candidate.bbox_width, candidate.bbox_height)
        if candidate.kind == CandidateKind.TEXT:
            margin = max(8, int(max_dim * 0.45))
        elif candidate.kind == CandidateKind.BOX:
            margin = max(2, int(max_dim * 0.08))
        else:
            margin = max(1, int(max_dim * 0.04))

        left = max(0, int(candidate.bbox_x - margin))
        top = max(0, int(candidate.bbox_y - margin))
        right = min(image.width, int(candidate.bbox_x + candidate.bbox_width + margin))
        bottom = min(image.height, int(candidate.bbox_y + candidate.bbox_height + margin))
        return image.crop((left, top, right, bottom))

    def _build_candidate_vlm_crop(
        self,
        image: Image.Image,
        candidate: CalloutCandidate,
    ) -> Image.Image:
        max_dim = max(candidate.bbox_width, candidate.bbox_height)
        if candidate.kind == CandidateKind.TEXT:
            margin = max(18, int(max_dim * 1.2))
            return self._build_highlighted_candidate_crop(image, candidate, margin)

        zoom_margin = max(22, int(max_dim * 1.35))
        zoom_crop = self._build_highlighted_candidate_crop(image, candidate, zoom_margin)
        if candidate.kind == CandidateKind.CIRCLE and min(image.width, image.height) <= 1100:
            context_margin = max(72, int(max_dim * 3.6))
            context_crop = self._build_highlighted_candidate_crop(image, candidate, context_margin)
            return self._compose_candidate_vlm_multiview(zoom_crop, context_crop)
        return zoom_crop

    def _build_highlighted_candidate_crop(
        self,
        image: Image.Image,
        candidate: CalloutCandidate,
        margin: int,
    ) -> Image.Image:
        left = max(0, int(candidate.bbox_x - margin))
        top = max(0, int(candidate.bbox_y - margin))
        right = min(image.width, int(candidate.bbox_x + candidate.bbox_width + margin))
        bottom = min(image.height, int(candidate.bbox_y + candidate.bbox_height + margin))
        crop = image.crop((left, top, right, bottom)).convert("RGBA")

        target_left = candidate.bbox_x - left
        target_top = candidate.bbox_y - top
        target_right = target_left + candidate.bbox_width
        target_bottom = target_top + candidate.bbox_height
        center_x = candidate.center_x - left
        center_y = candidate.center_y - top

        overlay = Image.new("RGBA", crop.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        outline_width = max(2, int(round(min(crop.size) * 0.02)))
        highlight_color = (88, 255, 188, 255)
        fill_color = (88, 255, 188, 32)
        radius = max(8, int(round(min(candidate.bbox_width, candidate.bbox_height) * 0.35)))
        draw.rounded_rectangle(
            (target_left, target_top, target_right, target_bottom),
            radius=radius,
            outline=highlight_color,
            fill=fill_color,
            width=outline_width,
        )
        cross = max(8, int(round(min(crop.size) * 0.08)))
        draw.line((center_x - cross, center_y, center_x + cross, center_y), fill=highlight_color, width=outline_width)
        draw.line((center_x, center_y - cross, center_x, center_y + cross), fill=highlight_color, width=outline_width)
        return Image.alpha_composite(crop, overlay).convert("RGB")

    @staticmethod
    def _compose_candidate_vlm_multiview(zoom_crop: Image.Image, context_crop: Image.Image) -> Image.Image:
        target_height = max(320, min(560, max(zoom_crop.height, context_crop.height)))

        def _fit(image: Image.Image) -> Image.Image:
            scale = target_height / max(image.height, 1)
            width = max(1, int(round(image.width * scale)))
            return image.resize((width, target_height), Image.Resampling.LANCZOS)

        left_view = _fit(zoom_crop)
        right_view = _fit(context_crop)
        gap = 18
        padding = 18
        canvas = Image.new(
            "RGB",
            (left_view.width + right_view.width + gap + padding * 2, target_height + padding * 2),
            (255, 255, 255),
        )
        canvas.paste(left_view, (padding, padding))
        canvas.paste(right_view, (padding + left_view.width + gap, padding))

        draw = ImageDraw.Draw(canvas)
        divider_x = padding + left_view.width + gap // 2
        draw.line((divider_x, padding, divider_x, padding + target_height), fill=(190, 190, 190), width=2)
        return canvas

    @staticmethod
    def _suggestion_quality(label: str | None, confidence: float | None, source: str | None) -> float:
        if not label:
            return 0.0
        score = confidence or 0.0
        if source:
            if source.startswith("openai-vlm:"):
                score += 0.28
            elif source.startswith("tile-vlm-consensus:") or source.startswith("tile-vlm:") or source.startswith("tile-openrouter-vlm:"):
                score += 0.48
            elif source.startswith("openrouter-vlm:"):
                score += 0.3
            elif source.startswith("gemini-vlm:"):
                score += 0.24
            if source.startswith("tile-"):
                score += 0.18
            elif source.startswith("page-"):
                score += 0.12
            elif source in {"sharp2x", "bw3x", "inner3x", "circle3x", "circle-inner5x", "circle-inner-bw6x", "base"}:
                score += 0.04
        if "-" in label or any(char.isalpha() for char in label):
            score += 0.04
        if label.isdigit() and len(label) == 1:
            score -= 0.03
        return score

    @staticmethod
    def _eligible_for_final_candidate_vlm(candidate: CalloutCandidate, low_res_circle_mode: bool) -> bool:
        label = (candidate.suggested_label or "").strip()
        confidence = candidate.suggested_confidence or 0.0
        diameter = min(candidate.bbox_width, candidate.bbox_height)

        if candidate.kind in {CandidateKind.CIRCLE, CandidateKind.BOX}:
            if not label:
                return True
            if candidate.conflict_group:
                return True
            if low_res_circle_mode and label.isdigit() and len(label) == 1 and diameter <= 34:
                return True
            if low_res_circle_mode and diameter <= 28:
                if confidence < 0.86:
                    return True
                return label.isdigit() and len(label) == 2 and any(char in {"0", "8"} for char in label)
            if confidence < 0.92:
                return True
            if label.isdigit() and len(label) == 2 and any(char in {"0", "8"} for char in label):
                return True
            return False

        if candidate.kind == CandidateKind.TEXT:
            if not label:
                return True
            if confidence < 0.86:
                return True
            return "-" in label or any(char.isalpha() for char in label)

        return False

    @staticmethod
    def _final_candidate_vlm_priority(candidate: CalloutCandidate) -> tuple[int, float, float]:
        label = (candidate.suggested_label or "").strip()
        confidence = candidate.suggested_confidence or 0.0
        missing_rank = 0 if not label else 1
        kind_rank = 0 if candidate.kind in {CandidateKind.CIRCLE, CandidateKind.BOX} else 1
        suspicious_single_digit_rank = 0 if candidate.kind in {CandidateKind.CIRCLE, CandidateKind.BOX} and label.isdigit() and len(label) == 1 else 1
        return (
            missing_rank,
            kind_rank,
            suspicious_single_digit_rank,
            confidence,
        )

    @staticmethod
    def _should_reject_candidate_from_vlm(candidate: CalloutCandidate, suggestion: CandidateSuggestion) -> bool:
        if not suggestion.source or not suggestion.source.endswith(":no-callout"):
            return False
        if candidate.kind not in {CandidateKind.CIRCLE, CandidateKind.BOX}:
            return False
        label = (candidate.suggested_label or "").strip()
        confidence = candidate.suggested_confidence or 0.0
        diameter = min(candidate.bbox_width, candidate.bbox_height)
        if not label:
            return True
        if "-" in label or any(char.isalpha() for char in label):
            return False
        if len(label) > 2:
            return False
        if diameter <= 28 and confidence >= 0.86:
            return False
        return True

    def _should_replace_candidate_suggestion(
        self,
        candidate: CalloutCandidate,
        new_label: str | None,
        new_confidence: float | None,
        new_source: str | None,
    ) -> bool:
        current_label = (candidate.suggested_label or "").strip().upper()
        proposed_label = (new_label or "").strip().upper()
        proposed_confidence = new_confidence or 0.0

        if candidate.kind in {CandidateKind.CIRCLE, CandidateKind.BOX} and current_label and proposed_label:
            diameter = min(candidate.bbox_width, candidate.bbox_height)
            if (
                proposed_label.startswith(current_label)
                and len(proposed_label) > len(current_label)
                and proposed_confidence >= 0.68
            ):
                return True
            if (
                current_label.isdigit()
                and proposed_label.isdigit()
                and len(current_label) == 2
                and len(proposed_label) == 1
                and current_label.startswith(proposed_label)
                and current_label[1] in {"0", "8"}
                and (
                    proposed_confidence >= 0.82
                    or (
                        diameter <= 20
                        and (new_source or "").startswith("easy-circle")
                        and proposed_confidence >= 0.5
                    )
                )
            ):
                return True

        current_quality = self._suggestion_quality(
            candidate.suggested_label,
            candidate.suggested_confidence,
            candidate.suggested_source,
        )
        new_quality = self._suggestion_quality(new_label, new_confidence, new_source)
        return new_quality >= current_quality + 0.05

    def _write_candidate_crop(
        self,
        session_id: str,
        candidate_id: str,
        crop: Image.Image,
    ) -> str:
        candidate_dir = self._session_dir(session_id) / "candidates"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{candidate_id}.png"
        crop.save(candidate_dir / file_name, format="PNG")
        return f"{settings.storage_mount_path}/{session_id}/candidates/{file_name}"

    def _assign_candidate_conflicts(self, candidates: list[CalloutCandidate]) -> None:
        next_conflict = 1
        for index, candidate in enumerate(candidates):
            overlapping: list[CalloutCandidate] = []
            for other in candidates[index + 1 :]:
                same_suggested_label = (
                    candidate.suggested_label
                    and other.suggested_label
                    and candidate.suggested_label == other.suggested_label
                )
                if self._candidate_overlap(candidate, other) or (
                    same_suggested_label and self._candidate_nearby(candidate, other)
                ):
                    overlapping.append(other)

            if not overlapping:
                continue

            group_members = [candidate, *overlapping]
            existing_group = next((member.conflict_group for member in group_members if member.conflict_group), None)
            group_key = existing_group or f"candidate-conflict-{next_conflict}"
            if not existing_group:
                next_conflict += 1

            for member in group_members:
                member.conflict_group = group_key
                member.conflict_count = len(group_members)

    @staticmethod
    def _eligible_for_candidate_ocr(candidate: CalloutCandidate, low_res_circle_mode: bool = False) -> bool:
        max_dim = max(candidate.bbox_width, candidate.bbox_height)
        min_dim = min(candidate.bbox_width, candidate.bbox_height)
        if candidate.kind == CandidateKind.TEXT:
            if low_res_circle_mode and candidate.suggested_label and (candidate.suggested_confidence or 0.0) >= 0.72:
                return False
            return True
        if candidate.kind == CandidateKind.BOX:
            return True
        if min_dim < 10:
            return False
        if max_dim > 72:
            return False
        return True

    @staticmethod
    def _candidate_ocr_priority(candidate: CalloutCandidate, low_res_circle_mode: bool = False) -> tuple[int, float, float]:
        area = candidate.bbox_width * candidate.bbox_height
        if low_res_circle_mode:
            kind_rank = 0 if candidate.kind == CandidateKind.CIRCLE else 1 if candidate.kind == CandidateKind.BOX else 2
        else:
            kind_rank = 0 if candidate.kind == CandidateKind.TEXT else 1 if candidate.kind == CandidateKind.BOX else 2
        return (kind_rank, area, -candidate.score)

    @staticmethod
    def _candidate_overlap(left: CalloutCandidate, right: CalloutCandidate) -> bool:
        left_x2 = left.bbox_x + left.bbox_width
        left_y2 = left.bbox_y + left.bbox_height
        right_x2 = right.bbox_x + right.bbox_width
        right_y2 = right.bbox_y + right.bbox_height
        inter_x1 = max(left.bbox_x, right.bbox_x)
        inter_y1 = max(left.bbox_y, right.bbox_y)
        inter_x2 = min(left_x2, right_x2)
        inter_y2 = min(left_y2, right_y2)
        if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
            return False
        intersection = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
        union = left.bbox_width * left.bbox_height + right.bbox_width * right.bbox_height - intersection
        if union <= 0:
            return False
        return (intersection / union) >= 0.18

    @staticmethod
    def _candidate_nearby(left: CalloutCandidate, right: CalloutCandidate) -> bool:
        distance = ((left.center_x - right.center_x) ** 2 + (left.center_y - right.center_y) ** 2) ** 0.5
        gate = max(12.0, min(left.bbox_width, left.bbox_height, right.bbox_width, right.bbox_height) * 0.6)
        return distance <= gate

    @classmethod
    def _candidate_same_target(cls, left: CalloutCandidate, right: CalloutCandidate) -> bool:
        if not cls._candidate_overlap(left, right):
            return False

        distance = ((left.center_x - right.center_x) ** 2 + (left.center_y - right.center_y) ** 2) ** 0.5
        avg_width = (left.bbox_width + right.bbox_width) / 2
        avg_height = (left.bbox_height + right.bbox_height) / 2
        width_ratio = min(left.bbox_width, right.bbox_width) / max(left.bbox_width, right.bbox_width, 1.0)
        height_ratio = min(left.bbox_height, right.bbox_height) / max(left.bbox_height, right.bbox_height, 1.0)

        if width_ratio < 0.62 or height_ratio < 0.62:
            return False
        if distance > max(6.0, min(avg_width, avg_height) * 0.38):
            return False
        return True

    def _build_export_archive(self, session: AnnotationSession, document_path: Path) -> bytes:
        archive_buffer = BytesIO()
        session_export_name = self._safe_export_name(session.title)
        markers_csv = self._build_markers_csv(session)
        markers_xlsx = self._build_markers_xlsx(session)

        with Image.open(document_path) as source_image:
            annotated_image = self._render_annotated_image(source_image, session.markers)
            annotated_buffer = BytesIO()
            annotated_image.save(annotated_buffer, format="PNG")

        with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{session_export_name}/markers.csv", markers_csv)
            archive.writestr(f"{session_export_name}/markers.xlsx", markers_xlsx)
            archive.writestr(f"{session_export_name}/annotated.png", annotated_buffer.getvalue())

        return archive_buffer.getvalue()

    @staticmethod
    def _collect_blocking_export_issues(session: AnnotationSession) -> list[str]:
        issues: list[str] = []
        for conflict in session.pipeline_conflicts:
            if conflict.severity != PipelineConflictSeverity.ERROR:
                continue
            label = (conflict.label or "").strip()
            if label:
                issues.append(f"{conflict.type.value}:{label}")
            else:
                issues.append(conflict.type.value)
        return issues

    def _build_markers_xlsx(self, session: AnnotationSession) -> bytes:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Markers"
        headers = ["Цифра", "X", "Y"]
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True)

        for marker in session.markers:
            sheet.append(
                [
                    marker.label or "",
                    marker.x,
                    marker.y,
                ]
            )

        sheet.freeze_panes = "A2"
        widths = {
            "A": 16,
            "B": 12,
            "C": 12,
        }
        for column, width in widths.items():
            sheet.column_dimensions[column].width = width

        payload = BytesIO()
        workbook.save(payload)
        return payload.getvalue()

    def _build_markers_csv(self, session: AnnotationSession) -> str:
        rows = self._build_export_rows(session)
        buffer = StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "label",
                "center_x",
                "center_y",
                "top_left_x",
                "top_left_y",
                "statuses",
                "confidence",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return buffer.getvalue()

    def _build_export_rows(self, session: AnnotationSession) -> list[dict[str, object]]:
        grouped: dict[str, dict[str, object]] = {}
        order: list[str] = []

        for marker in session.markers:
            export_key = (marker.label or "").strip() or f"unlabeled:{marker.marker_id}"
            if export_key not in grouped:
                grouped[export_key] = {
                    "label": (marker.label or "").strip(),
                    "center_x": None,
                    "center_y": None,
                    "top_left_x": None,
                    "top_left_y": None,
                    "statuses": [],
                    "confidence": None,
                }
                order.append(export_key)

            row = grouped[export_key]
            statuses = row["statuses"]
            if isinstance(statuses, list) and marker.status.value not in statuses:
                statuses.append(marker.status.value)

            if marker.confidence is not None:
                current_confidence = row["confidence"]
                if current_confidence is None or marker.confidence > current_confidence:
                    row["confidence"] = marker.confidence

            if marker.point_type == MarkerPointType.CENTER:
                row["center_x"] = round(marker.x, 4)
                row["center_y"] = round(marker.y, 4)
            else:
                row["top_left_x"] = round(marker.x, 4)
                row["top_left_y"] = round(marker.y, 4)

        export_rows: list[dict[str, object]] = []
        for key in order:
            row = grouped[key]
            statuses = row["statuses"]
            export_rows.append(
                {
                    "label": row["label"],
                    "center_x": row["center_x"] if row["center_x"] is not None else "",
                    "center_y": row["center_y"] if row["center_y"] is not None else "",
                    "top_left_x": row["top_left_x"] if row["top_left_x"] is not None else "",
                    "top_left_y": row["top_left_y"] if row["top_left_y"] is not None else "",
                    "statuses": "|".join(statuses) if isinstance(statuses, list) else "",
                    "confidence": row["confidence"] if row["confidence"] is not None else "",
                }
            )
        return export_rows

    @staticmethod
    def _safe_export_name(title: str) -> str:
        compact = re.sub(r"[^A-Za-z0-9._-]+", "-", (title or "session").strip()).strip("-._")
        return compact or "session"

    def _resolve_document_path(self, session_id: str, storage_url: str) -> Path:
        return self.storage_root / session_id / Path(storage_url).name

    def _render_annotated_image(self, source_image: Image.Image, markers: list[Marker]) -> Image.Image:
        image = source_image.convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        min_side = min(image.width, image.height)
        is_low_res = min_side <= 1100
        if min_side <= 260:
            font_size = 7
            center_radius = 3
            top_left_size = 6
        elif is_low_res:
            font_size = max(8, min(14, int(min_side * 0.014)))
            center_radius = max(3, min(5, int(min_side * 0.005)))
            top_left_size = max(6, min(9, center_radius * 2))
        else:
            font_size = max(10, min(18, int(min_side * 0.017)))
            center_radius = max(4, min(6, int(min_side * 0.0065)))
            top_left_size = max(8, min(11, center_radius * 2))
        outline_width = 1
        label_gap = max(2, center_radius - 1 if is_low_res else center_radius)
        text_stroke = 1
        font = self._load_annotation_font(font_size)
        point_alpha = 204
        text_alpha = 204
        outline_alpha = 220

        for marker in markers:
            point_fill = (22, 163, 74, point_alpha) if marker.point_type == MarkerPointType.TOP_LEFT else (217, 45, 32, point_alpha)
            outline_fill = (255, 255, 255, outline_alpha)
            x = marker.x
            y = marker.y
            if marker.point_type == MarkerPointType.CENTER:
                point_box = (x - center_radius, y - center_radius, x + center_radius, y + center_radius)
                draw.ellipse(point_box, fill=point_fill, outline=outline_fill, width=outline_width)
            else:
                outer_box = (
                    x - outline_width,
                    y - outline_width,
                    x + top_left_size + outline_width,
                    y + top_left_size + outline_width,
                )
                point_box = (x, y, x + top_left_size, y + top_left_size)
                corner_radius = max(2, top_left_size // 3)
                draw.rounded_rectangle(outer_box, radius=corner_radius, fill=None, outline=outline_fill, width=outline_width + 1)
                draw.rounded_rectangle(point_box, radius=corner_radius, fill=None, outline=point_fill, width=outline_width)

            if marker.label:
                point_right = point_box[2]
                point_top = point_box[1]
                text_x = point_right + label_gap
                text_y = point_top - max(1, int(font_size * 0.55))
                text_bbox = draw.textbbox((text_x, text_y), marker.label, font=font, stroke_width=text_stroke)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]

                if text_bbox[2] > image.width - 2:
                    text_x = max(2, int(point_box[0] - label_gap - text_width))
                if text_y < 2:
                    text_y = min(image.height - text_height - 2, int(point_box[3] + label_gap - 1))

                draw.text(
                    (text_x, text_y),
                    marker.label,
                    fill=(*point_fill[:3], text_alpha),
                    font=font,
                    stroke_width=text_stroke,
                    stroke_fill=outline_fill,
                )

        return Image.alpha_composite(image, overlay)

    @staticmethod
    def _load_annotation_font(font_size: int) -> ImageFont.ImageFont:
        for candidate in ("arialbd.ttf", "arial.ttf", "seguisb.ttf", "segoeui.ttf"):
            try:
                return ImageFont.truetype(candidate, font_size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _apply(self, session: AnnotationSession, command: SessionCommandRequest) -> ActionType:
        if command.type == SessionCommandType.SET_VIEWPORT:
            session.viewport = self._normalize_viewport(
                session,
                Viewport(
                    center_x=self._require(command.center_x, "centerX"),
                    center_y=self._require(command.center_y, "centerY"),
                    zoom=self._require(command.zoom, "zoom"),
                ),
            )
            return ActionType.VIEWPORT_SET

        if command.type == SessionCommandType.PAN_VIEWPORT:
            viewport = session.viewport
            session.viewport = self._normalize_viewport(
                session,
                Viewport(
                    center_x=viewport.center_x + (command.delta_x or 0),
                    center_y=viewport.center_y + (command.delta_y or 0),
                    zoom=viewport.zoom,
                ),
            )
            return ActionType.VIEWPORT_PANNED

        if command.type == SessionCommandType.ZOOM_TO_REGION:
            document = self._require_document(session)
            width = max(self._require(command.width, "width"), 1)
            height = max(self._require(command.height, "height"), 1)
            zoom = min(document.width / width, document.height / height)
            session.viewport = self._normalize_viewport(
                session,
                Viewport(
                    center_x=self._require(command.x, "x") + width / 2,
                    center_y=self._require(command.y, "y") + height / 2,
                    zoom=zoom,
                ),
            )
            return ActionType.VIEWPORT_ZOOMED

        if command.type == SessionCommandType.PLACE_MARKER:
            candidate = self._find_candidate(session, command.candidate_id) if command.candidate_id else None
            point_type = command.point_type or MarkerPointType.CENTER
            candidate_x = candidate.bbox_x if candidate and point_type == MarkerPointType.TOP_LEFT else candidate.center_x if candidate else None
            candidate_y = candidate.bbox_y if candidate and point_type == MarkerPointType.TOP_LEFT else candidate.center_y if candidate else None
            marker = Marker(
                label=self._clean_label(command.label if command.label is not None else candidate.suggested_label if candidate else None),
                x=self._clamp_x(session, command.x if command.x is not None else self._require(candidate_x, "x")),
                y=self._clamp_y(session, command.y if command.y is not None else self._require(candidate_y, "y")),
                point_type=point_type,
                status=command.status or self._default_marker_status(command.actor),
                confidence=command.confidence if command.confidence is not None else (candidate.suggested_confidence if candidate else None),
                created_by=command.actor,
                updated_by=command.actor,
            )
            session.markers.append(marker)
            if candidate:
                candidate.review_status = CandidateReviewStatus.ACCEPTED
                candidate.updated_at = datetime.utcnow()
            return ActionType.MARKER_CREATED

        if command.type == SessionCommandType.MOVE_MARKER:
            marker = self._find_marker(session, command.marker_id)
            marker.x = self._clamp_x(session, command.x if command.x is not None else marker.x + (command.delta_x or 0))
            marker.y = self._clamp_y(session, command.y if command.y is not None else marker.y + (command.delta_y or 0))
            if command.actor == Actor.HUMAN and marker.status in {MarkerStatus.HUMAN_CONFIRMED, MarkerStatus.HUMAN_CORRECTED}:
                marker.status = MarkerStatus.HUMAN_DRAFT
            marker.updated_by = command.actor
            marker.updated_at = datetime.utcnow()
            return ActionType.MARKER_MOVED

        if command.type == SessionCommandType.UPDATE_MARKER:
            marker = self._find_marker(session, command.marker_id)
            changed_label = False
            changed_point_type = False
            if command.label is not None:
                next_label = self._clean_label(command.label)
                changed_label = next_label != marker.label
                marker.label = next_label
            if command.point_type is not None:
                changed_point_type = command.point_type != marker.point_type
                marker.point_type = command.point_type
            if command.status is not None:
                marker.status = command.status
            if command.confidence is not None:
                marker.confidence = command.confidence
            elif command.actor == Actor.HUMAN and marker.status in {MarkerStatus.HUMAN_CONFIRMED, MarkerStatus.HUMAN_CORRECTED} and (changed_label or changed_point_type):
                marker.status = MarkerStatus.HUMAN_DRAFT
            marker.updated_by = command.actor
            marker.updated_at = datetime.utcnow()
            return ActionType.MARKER_UPDATED

        if command.type == SessionCommandType.CONFIRM_MARKER:
            marker = self._find_marker(session, command.marker_id)
            marker.status = command.status or MarkerStatus.HUMAN_CONFIRMED
            marker.updated_by = command.actor
            marker.updated_at = datetime.utcnow()
            return ActionType.MARKER_CONFIRMED

        if command.type == SessionCommandType.REJECT_MARKER:
            marker = self._find_marker(session, command.marker_id)
            marker.status = MarkerStatus.REJECTED
            marker.updated_by = command.actor
            marker.updated_at = datetime.utcnow()
            return ActionType.MARKER_REJECTED

        if command.type == SessionCommandType.DELETE_MARKER:
            marker = self._find_marker(session, command.marker_id)
            session.markers = [item for item in session.markers if item.marker_id != marker.marker_id]
            return ActionType.MARKER_DELETED

        if command.type == SessionCommandType.CLEAR_MARKERS:
            session.markers = []
            return ActionType.MARKERS_CLEARED

        if command.type == SessionCommandType.REJECT_CANDIDATE:
            candidate = self._find_candidate(session, command.candidate_id)
            candidate.review_status = CandidateReviewStatus.REJECTED
            candidate.updated_at = datetime.utcnow()
            return ActionType.CANDIDATE_REJECTED

        raise ValueError(f"Unsupported command type: {command.type}")

    @staticmethod
    def _job_row_bbox_kwargs(row: DrawingResultRow) -> dict[str, float]:
        if row.bbox is None:
            return {}
        return {
            "bbox_x": row.bbox.x,
            "bbox_y": row.bbox.y,
            "bbox_width": row.bbox.w,
            "bbox_height": row.bbox.h,
        }

    @staticmethod
    def _build_near_tie_index(items: list[dict]) -> dict[tuple[int, int, str], dict]:
        index: dict[tuple[int, int, str], dict] = {}
        for item in items:
            try:
                row = int(item.get("row"))
                page_index = int(item.get("page_index", 0))
            except (TypeError, ValueError):
                continue
            label = normalize_label(str(item.get("label") or ""))
            if not label:
                continue
            index[(row, page_index, label)] = item
        return index

    @staticmethod
    def _build_near_tie_markers_from_job_row(row: DrawingResultRow, near_tie_item: dict) -> list[Marker]:
        if row.bbox is None:
            marker = InMemorySessionStore._build_marker_from_job_row(row, status=MarkerStatus.AI_REVIEW)
            return [marker] if marker is not None else []

        primary_label = str(near_tie_item.get("ocr_best_label") or row.label or "").strip() or row.label
        alternative_label = str(near_tie_item.get("alternative_label") or "").strip()
        primary_score = near_tie_item.get("ocr_best_score")
        alternative_score = near_tie_item.get("ocr_second_score")

        center_x = row.bbox.x + (row.bbox.w / 2)
        center_y = row.bbox.y + (row.bbox.h / 2)
        vertical_split = row.bbox.h >= row.bbox.w * 1.4
        offset = max(8.0, min(row.bbox.h if vertical_split else row.bbox.w, 26.0) * 0.22)

        points: list[tuple[str, float | None, float, float]] = []
        if vertical_split:
            points.append((primary_label, primary_score, center_x, center_y - offset))
            if alternative_label:
                points.append((alternative_label, alternative_score, center_x, center_y + offset))
        else:
            points.append((primary_label, primary_score, center_x - offset, center_y))
            if alternative_label:
                points.append((alternative_label, alternative_score, center_x + offset, center_y))

        markers: list[Marker] = []
        seen_labels: set[str] = set()
        for label, score, x, y in points:
            normalized = normalize_label(label)
            if not normalized or normalized in seen_labels:
                continue
            seen_labels.add(normalized)
            markers.append(
                Marker(
                    label=label,
                    x=x,
                    y=y,
                    point_type=MarkerPointType.CENTER,
                    status=MarkerStatus.AI_REVIEW,
                    confidence=float(score) if isinstance(score, (int, float)) else row.final_score,
                    created_by=Actor.AI,
                    updated_by=Actor.AI,
                )
            )
        if not markers:
            marker = InMemorySessionStore._build_marker_from_job_row(row, status=MarkerStatus.AI_REVIEW)
            return [marker] if marker is not None else []
        return markers

    @staticmethod
    def _build_marker_from_job_row(row: DrawingResultRow, *, status: MarkerStatus) -> Marker | None:
        point = row.center
        point_type = MarkerPointType.CENTER

        if point is None and row.top_left is not None:
            point = row.top_left
            point_type = MarkerPointType.TOP_LEFT

        if point is None:
            return None

        return Marker(
            label=row.label,
            x=point.x,
            y=point.y,
            point_type=point_type,
            status=status,
            confidence=row.final_score,
            created_by=Actor.AI,
            updated_by=Actor.AI,
        )

    def _build_page_vocabulary_from_job_rows(self, rows: list[DrawingResultRow]) -> list[PageVocabularyEntry]:
        vocabulary_by_label: dict[str, PageVocabularyEntry] = {}

        for row in rows:
            if row.status == DrawingResultRowStatus.NOT_FOUND:
                continue

            normalized_label = normalize_label(row.label)
            if not normalized_label:
                continue

            entry = vocabulary_by_label.get(normalized_label)
            if entry is None:
                entry = PageVocabularyEntry(
                    label=row.label,
                    normalized_label=normalized_label,
                    occurrences=0,
                    max_confidence=row.final_score,
                    sources=[row.source_kind or "job-result"],
                    **self._job_row_bbox_kwargs(row),
                )
                vocabulary_by_label[normalized_label] = entry

            entry.occurrences += 1
            if row.final_score is not None and (entry.max_confidence is None or row.final_score > entry.max_confidence):
                entry.max_confidence = row.final_score
            source_kind = row.source_kind or "job-result"
            if source_kind not in entry.sources:
                entry.sources.append(source_kind)
            if row.bbox is not None and entry.bbox_width is None:
                entry.bbox_x = row.bbox.x
                entry.bbox_y = row.bbox.y
                entry.bbox_width = row.bbox.w
                entry.bbox_height = row.bbox.h

        return list(vocabulary_by_label.values())

    @staticmethod
    def _merge_job_missing_labels(missing_labels: list[str], rows: list[DrawingResultRow]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        def append_label(value: str | None) -> None:
            normalized_value = normalize_label(value)
            if not normalized_value or normalized_value in seen:
                return
            seen.add(normalized_value)
            ordered.append((value or "").strip())

        for label in missing_labels:
            append_label(label)
        for row in rows:
            if row.status == DrawingResultRowStatus.NOT_FOUND:
                append_label(row.label)

        return ordered

    def _record_action(self, session: AnnotationSession, actor: Actor, action_type: ActionType, payload: dict) -> None:
        session.action_log.append(
            ActionLogEntry(
                actor=actor,
                type=action_type,
                payload=payload,
            )
        )
        session.updated_at = datetime.utcnow()

    @staticmethod
    def _should_record_action(action_type: ActionType) -> bool:
        return action_type not in {
            ActionType.VIEWPORT_SET,
            ActionType.VIEWPORT_PANNED,
            ActionType.VIEWPORT_ZOOMED,
        }

    def _refresh_summary(self, session: AnnotationSession) -> None:
        summary = SessionSummary(total_markers=len(session.markers))
        for marker in session.markers:
            if marker.status == MarkerStatus.AI_DETECTED:
                summary.ai_detected += 1
            elif marker.status == MarkerStatus.AI_REVIEW:
                summary.ai_review += 1
            elif marker.status == MarkerStatus.HUMAN_CONFIRMED:
                summary.human_confirmed += 1
            elif marker.status == MarkerStatus.HUMAN_CORRECTED:
                summary.human_corrected += 1
            elif marker.status == MarkerStatus.REJECTED:
                summary.rejected += 1
        session.summary = summary

    def _normalize_viewport(self, session: AnnotationSession, viewport: Viewport) -> Viewport:
        document = session.document
        if document:
            center_x = min(max(viewport.center_x, 0), document.width)
            center_y = min(max(viewport.center_y, 0), document.height)
        else:
            center_x = max(viewport.center_x, 0)
            center_y = max(viewport.center_y, 0)
        zoom = min(max(viewport.zoom, 0.25), 12)
        return Viewport(center_x=center_x, center_y=center_y, zoom=zoom)

    def _find_marker(self, session: AnnotationSession, marker_id: str | None) -> Marker:
        if not marker_id:
            raise ValueError("markerId is required")
        for marker in session.markers:
            if marker.marker_id == marker_id:
                return marker
        raise KeyError(f"marker '{marker_id}' not found")

    def _find_candidate(self, session: AnnotationSession, candidate_id: str | None) -> CalloutCandidate:
        if not candidate_id:
            raise ValueError("candidateId is required")
        for candidate in session.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise KeyError(f"candidate '{candidate_id}' not found")

    def _get_session(self, session_id: str) -> AnnotationSession:
        if session_id not in self._sessions:
            raise KeyError(f"session '{session_id}' not found")
        return self._sessions[session_id]

    def _require_document(self, session: AnnotationSession) -> DocumentAsset:
        if not session.document:
            raise ValueError("session does not have a document yet")
        return session.document

    @staticmethod
    def _require(value: float | None, field_name: str) -> float:
        if value is None:
            raise ValueError(f"{field_name} is required")
        return value

    @staticmethod
    def _clean_label(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _default_marker_status(actor: Actor) -> MarkerStatus:
        if actor == Actor.AI:
            return MarkerStatus.AI_DETECTED
        if actor == Actor.HUMAN:
            return MarkerStatus.HUMAN_DRAFT
        return MarkerStatus.AI_REVIEW

    @staticmethod
    def _command_payload(command: SessionCommandRequest) -> dict:
        payload = command.model_dump(exclude_none=True, by_alias=True)
        payload.pop("type", None)
        return payload

    def _clamp_x(self, session: AnnotationSession, value: float) -> float:
        if session.document:
            return min(max(value, 0), session.document.width)
        return max(value, 0)

    def _clamp_y(self, session: AnnotationSession, value: float) -> float:
        if session.document:
            return min(max(value, 0), session.document.height)
        return max(value, 0)
