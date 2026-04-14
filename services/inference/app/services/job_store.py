from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from ..core.config import settings
from ..models.job_schemas import (
    CreateJobResponse,
    DrawingJob,
    DrawingJobInput,
    DrawingJobStatus,
    JobListItem,
    JobListResponse,
)
from .job_runner import JobRunOutput, run_job_pipeline


ALLOWED_DRAWING_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
ALLOWED_LABEL_EXTENSIONS = {".xlsx", ".csv", ".txt", ".tsv"}


@dataclass(frozen=True)
class StoredJobUpload:
    file_name: str
    content_type: str
    size_bytes: int
    storage_path: Path
    storage_url: str


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, DrawingJob] = {}
        self._lock = asyncio.Lock()
        self._load_existing_jobs()

    @property
    def storage_root(self) -> Path:
        root = Path(settings.storage_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def jobs_root(self) -> Path:
        root = self.storage_root / "jobs"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def new_job_id(self) -> str:
        return str(uuid4())

    def _job_dir(self, job_id: str) -> Path:
        path = self.jobs_root / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _persist_job(self, job: DrawingJob) -> None:
        job_path = self._job_dir(job.job_id) / "job.json"
        payload = job.model_dump(mode="json", by_alias=True)
        job_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_existing_jobs(self) -> None:
        for manifest_path in self.jobs_root.glob("*/job.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                job = DrawingJob.model_validate(payload)
            except Exception:
                continue
            self._jobs[job.job_id] = job

    def _get_job(self, job_id: str) -> DrawingJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"Job '{job_id}' not found")
        return job

    def _job_needs_legacy_repair(self, job: DrawingJob) -> bool:
        if job.status not in {DrawingJobStatus.COMPLETED, DrawingJobStatus.FAILED}:
            return False
        legacy_error_message = str(job.error_message or "").strip().lower()
        if job.status == DrawingJobStatus.FAILED:
            return "legacy pipeline" in legacy_error_message
        result = job.result
        if result is None:
            return False
        if (
            not job.input.has_labels
            and not result.rows
            and bool(result.held_back_rows)
        ):
            return True
        summary = result.summary
        failure_message = str(summary.failure_message or "").strip().lower()
        degraded_reason = str(summary.degraded_reason or "").strip().lower()
        review_reasons = " ".join(summary.review_reasons or []).strip().lower()
        stale_legacy_error = (
            "legacy pipeline" in failure_message
            or "legacy pipeline" in degraded_reason
            or "legacy pipeline" in review_reasons
        )
        if not stale_legacy_error:
            return False
        return not result.rows and not result.held_back_rows

    def _job_is_stale_running(self, job: DrawingJob) -> bool:
        if job.status != DrawingJobStatus.RUNNING:
            return False
        max_runtime = timedelta(seconds=settings.job_max_runtime_seconds)
        return datetime.utcnow() - job.updated_at > max_runtime

    async def _fail_stale_running_job(self, snapshot: DrawingJob) -> DrawingJob:
        if not self._job_is_stale_running(snapshot):
            return snapshot
        async with self._lock:
            live_job = self._get_job(snapshot.job_id)
            if not self._job_is_stale_running(live_job):
                return live_job.model_copy(deep=True)
            live_job.status = DrawingJobStatus.FAILED
            live_job.error_message = (
                "Задача не завершилась за допустимое время. "
                "Запусти ещё раз."
            )
            live_job.result = None
            live_job.updated_at = datetime.utcnow()
            self._persist_job(live_job)
            return live_job.model_copy(deep=True)

    async def _repair_job_if_needed(self, snapshot: DrawingJob) -> DrawingJob:
        snapshot = await self._fail_stale_running_job(snapshot)
        if self._job_needs_legacy_repair(snapshot):
            await self.process_job(snapshot.job_id)
            async with self._lock:
                snapshot = self._get_job(snapshot.job_id).model_copy(deep=True)
        return snapshot

    async def repair_stale_jobs(self) -> int:
        async with self._lock:
            snapshots = [job.model_copy(deep=True) for job in self._jobs.values()]
        repaired = 0
        for snapshot in snapshots:
            if self._job_is_stale_running(snapshot):
                await self._fail_stale_running_job(snapshot)
                repaired += 1
                continue
            if not self._job_needs_legacy_repair(snapshot):
                continue
            await self._repair_job_if_needed(snapshot)
            repaired += 1
        return repaired

    def _storage_url_for(self, path: Path) -> str:
        relative = path.resolve().relative_to(self.storage_root.resolve())
        return f"{settings.storage_mount_path}/{relative.as_posix()}"

    def resolve_storage_url(self, url: str) -> Path:
        normalized = (url or "").strip()
        mount_path = settings.storage_mount_path.rstrip("/")
        if normalized.startswith(mount_path):
            relative = normalized[len(mount_path):].lstrip("/").replace("/", "\\")
            return self.storage_root / Path(relative)
        return Path(normalized)

    def _validate_extension(self, *, file_name: str, allowed: set[str], kind_label: str) -> str:
        suffix = Path(file_name).suffix.lower()
        if suffix not in allowed:
            raise ValueError(f"Неподдерживаемый формат {kind_label}: {suffix or file_name}")
        return suffix

    def prepare_upload(
        self,
        *,
        job_id: str,
        slot: str,
        file_name: str,
        content_type: str,
        source_stream: BinaryIO,
        size_bytes: int,
    ) -> StoredJobUpload:
        if slot == "drawing":
            suffix = self._validate_extension(file_name=file_name, allowed=ALLOWED_DRAWING_EXTENSIONS, kind_label="чертежа")
        elif slot == "labels":
            suffix = self._validate_extension(file_name=file_name, allowed=ALLOWED_LABEL_EXTENSIONS, kind_label="таблицы")
        else:
            raise ValueError(f"Unknown upload slot: {slot}")

        input_dir = self._job_dir(job_id) / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        storage_path = input_dir / f"{slot}{suffix}"
        with storage_path.open("wb") as target:
            source_stream.seek(0)
            shutil.copyfileobj(source_stream, target)
        return StoredJobUpload(
            file_name=file_name,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_path=storage_path,
            storage_url=self._storage_url_for(storage_path),
        )

    async def list_jobs(self) -> JobListResponse:
        async with self._lock:
            jobs = [job.model_copy(deep=True) for job in sorted(self._jobs.values(), key=lambda item: item.updated_at, reverse=True)]
        repaired_jobs: list[DrawingJob] = []
        for job in jobs:
            repaired_jobs.append(await self._repair_job_if_needed(job))
        return JobListResponse(jobs=[self._to_list_item(job) for job in repaired_jobs])

    async def get_job(self, job_id: str) -> DrawingJob:
        async with self._lock:
            snapshot = self._get_job(job_id).model_copy(deep=True)
        return await self._repair_job_if_needed(snapshot)

    async def create_job(
        self,
        *,
        job_id: str,
        title: str | None,
        drawing_upload: StoredJobUpload,
        labels_upload: StoredJobUpload | None,
    ) -> CreateJobResponse:
        job_title = (title or Path(drawing_upload.file_name).stem or "Untitled job").strip() or "Untitled job"
        async with self._lock:
            job = DrawingJob(
                job_id=job_id,
                title=job_title,
                status=DrawingJobStatus.QUEUED,
                input=DrawingJobInput(
                    drawing_name=drawing_upload.file_name,
                    drawing_url=drawing_upload.storage_url,
                    labels_name=labels_upload.file_name if labels_upload else None,
                    labels_url=labels_upload.storage_url if labels_upload else None,
                    has_labels=labels_upload is not None,
                ),
                updated_at=datetime.utcnow(),
            )
            self._jobs[job.job_id] = job
            self._persist_job(job)
            return CreateJobResponse(job=job.model_copy(deep=True))

    async def delete_job(self, job_id: str) -> None:
        async with self._lock:
            self._get_job(job_id)
            self._jobs.pop(job_id, None)
        shutil.rmtree(self.jobs_root / job_id, ignore_errors=True)

    async def process_job(self, job_id: str) -> None:
        async with self._lock:
            job = self._get_job(job_id)
            if job.status == DrawingJobStatus.RUNNING:
                return
            job.status = DrawingJobStatus.RUNNING
            job.error_message = None
            job.updated_at = datetime.utcnow()
            snapshot = job.model_copy(deep=True)
            self._persist_job(job)

        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(run_job_pipeline, self._job_dir(job_id), snapshot),
                timeout=settings.job_max_runtime_seconds,
            )
        except asyncio.TimeoutError:
            async with self._lock:
                live_job = self._get_job(job_id)
                live_job.status = DrawingJobStatus.FAILED
                live_job.error_message = (
                    "Задача обрабатывалась слишком долго и была остановлена. "
                    "Попробуй запустить ещё раз."
                )
                live_job.result = None
                live_job.updated_at = datetime.utcnow()
                self._persist_job(live_job)
            return
        except Exception as exc:
            async with self._lock:
                live_job = self._get_job(job_id)
                live_job.status = DrawingJobStatus.FAILED
                live_job.error_message = str(exc)
                live_job.result = None
                live_job.updated_at = datetime.utcnow()
                self._persist_job(live_job)
            return

        completed_result = self._finalize_result(output)
        async with self._lock:
            live_job = self._get_job(job_id)
            live_job.status = DrawingJobStatus.COMPLETED
            live_job.error_message = None
            live_job.result = completed_result
            live_job.updated_at = datetime.utcnow()
            self._persist_job(live_job)

    def _finalize_result(self, output: JobRunOutput):
        result = output.result.model_copy(deep=True)
        page_overlay_urls: dict[int, str] = {}
        for artifact in output.page_artifacts:
            if artifact.overlay_path is None:
                continue
            page_overlay_urls[artifact.page_index] = self._storage_url_for(artifact.overlay_path)
        result.artifacts.overlay_url = page_overlay_urls.get(0) or next(iter(page_overlay_urls.values()), None)
        result.artifacts.csv_url = self._storage_url_for(output.production_csv_path)
        result.artifacts.xlsx_url = self._storage_url_for(output.production_xlsx_path)
        result.artifacts.zip_url = self._storage_url_for(output.production_zip_path)
        result.artifacts.review_csv_url = self._storage_url_for(output.review_csv_path)
        result.artifacts.review_xlsx_url = self._storage_url_for(output.review_xlsx_path)
        result.artifacts.review_zip_url = self._storage_url_for(output.review_zip_path)
        result.artifacts.near_tie_csv_url = self._storage_url_for(output.near_tie_csv_path) if output.near_tie_csv_path else None
        result.artifacts.near_tie_json_url = self._storage_url_for(output.near_tie_json_path) if output.near_tie_json_path else None
        result.artifacts.source_json_url = self._storage_url_for(output.source_json_path) if output.source_json_path else None
        result.artifacts.result_json_url = self._storage_url_for(output.result_json_path)
        for page in result.pages:
            page.overlay_url = page_overlay_urls.get(page.page_index)
        return result

    def _to_list_item(self, job: DrawingJob) -> JobListItem:
        confidence = None
        degraded_recognition = False
        degraded_reason = None
        emergency_fallback_used = False
        if job.result is not None:
            confidence = job.result.summary.document_confidence
            degraded_recognition = job.result.summary.degraded_recognition
            degraded_reason = job.result.summary.degraded_reason
            emergency_fallback_used = job.result.summary.emergency_fallback_used
        return JobListItem(
            job_id=job.job_id,
            title=job.title,
            status=job.status,
            drawing_name=job.input.drawing_name,
            labels_name=job.input.labels_name,
            document_confidence=confidence,
            degraded_recognition=degraded_recognition,
            degraded_reason=degraded_reason,
            emergency_fallback_used=emergency_fallback_used,
            error_message=job.error_message,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
