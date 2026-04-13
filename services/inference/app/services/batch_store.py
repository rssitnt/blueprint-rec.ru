from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..core.config import settings
from ..models.job_schemas import (
    BatchListItem,
    BatchListResponse,
    BatchResponse,
    BatchSummary,
    BatchJobWarning,
    DrawingJobBatch,
)
from .batch_exports import BatchExportMode, build_batch_export_zip, sanitize_name
from .job_store import InMemoryJobStore


class InMemoryBatchStore:
    def __init__(self, *, job_store: InMemoryJobStore) -> None:
        self._job_store = job_store
        self._batches: dict[str, DrawingJobBatch] = {}
        self._lock = asyncio.Lock()
        self._load_existing_batches()

    @property
    def storage_root(self) -> Path:
        root = Path(settings.storage_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def batches_root(self) -> Path:
        root = self.storage_root / "batches"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def new_batch_id(self) -> str:
        return str(uuid4())

    def _batch_dir(self, batch_id: str) -> Path:
        path = self.batches_root / batch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _persist_batch(self, batch: DrawingJobBatch) -> None:
        batch_path = self._batch_dir(batch.batch_id) / "batch.json"
        payload = batch.model_dump(mode="json", by_alias=True)
        batch_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_existing_batches(self) -> None:
        for manifest_path in self.batches_root.glob("*/batch.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                batch = DrawingJobBatch.model_validate(payload)
            except Exception:
                continue
            self._batches[batch.batch_id] = batch

    def _get_batch(self, batch_id: str) -> DrawingJobBatch:
        batch = self._batches.get(batch_id)
        if batch is None:
            raise KeyError(f"Batch '{batch_id}' not found")
        return batch

    async def create_batch(
        self,
        *,
        batch_id: str,
        title: str,
        archive_name: str,
        title_prefix: str | None,
        job_ids: list[str],
        warnings: list[BatchJobWarning],
    ) -> DrawingJobBatch:
        async with self._lock:
            batch = DrawingJobBatch(
                batch_id=batch_id,
                title=title.strip() or "Untitled batch",
                archive_name=archive_name,
                title_prefix=title_prefix.strip() if title_prefix else None,
                job_ids=job_ids,
                warnings=warnings,
                updated_at=datetime.utcnow(),
            )
            batch.summary = await self._build_summary(job_ids)
            self._batches[batch.batch_id] = batch
            self._persist_batch(batch)
            return batch.model_copy(deep=True)

    async def list_batches(self) -> BatchListResponse:
        jobs_response = await self._job_store.list_jobs()
        jobs_by_id = {job.job_id: job for job in jobs_response.jobs}
        async with self._lock:
            batches = sorted(self._batches.values(), key=lambda item: item.updated_at, reverse=True)
            items: list[BatchListItem] = []
            for batch in batches:
                existing_job_ids = [job_id for job_id in batch.job_ids if job_id in jobs_by_id]
                if not existing_job_ids:
                    continue
                summary = await self._build_summary(batch.job_ids)
                items.append(
                    BatchListItem(
                        batch_id=batch.batch_id,
                        title=batch.title,
                        archive_name=batch.archive_name,
                        job_count=len(existing_job_ids),
                        warning_count=len(batch.warnings),
                        created_at=batch.created_at,
                        updated_at=batch.updated_at,
                        summary=summary,
                    )
                )
            return BatchListResponse(batches=items)

    async def get_batch(self, batch_id: str) -> BatchResponse:
        async with self._lock:
            batch = self._get_batch(batch_id).model_copy(deep=True)
        jobs_response = await self._job_store.list_jobs()
        jobs_by_id = {job.job_id: job for job in jobs_response.jobs}
        existing_jobs = [jobs_by_id[job_id] for job_id in batch.job_ids if job_id in jobs_by_id]
        if not existing_jobs:
            raise KeyError(f"Batch '{batch_id}' not found")
        batch.summary = await self._build_summary(batch.job_ids)
        return BatchResponse(
            batch=batch,
            jobs=existing_jobs,
        )

    async def build_export(self, batch_id: str, *, mode: BatchExportMode):
        async with self._lock:
            batch = self._get_batch(batch_id).model_copy(deep=True)

        batch_response = await self.get_batch(batch_id)
        if not batch_response.batch.summary.finished:
            raise ValueError("Batch ещё не завершён. Дождись окончания всех задач.")

        completed_jobs = []
        export_warnings = [warning.model_dump(mode="json", by_alias=True) for warning in batch.warnings]
        for job_id in batch.job_ids:
            try:
                job = await self._job_store.get_job(job_id)
            except KeyError:
                export_warnings.append(
                    {
                        "code": "missing_job",
                        "message": f"Job {job_id} was not found at export time.",
                        "file_name": None,
                        "base_name": None,
                    }
                )
                continue
            if job.status.value != "completed" or job.result is None:
                export_warnings.append(
                    {
                        "code": "job_not_completed",
                        "message": f"Job {job.job_id} is not exportable: status={job.status.value}.",
                        "file_name": job.input.drawing_name,
                        "base_name": None,
                    }
                )
                continue
            completed_jobs.append(job)

        if not completed_jobs:
            raise ValueError("В batch нет готовых задач для экспорта.")

        export_dir = self._batch_dir(batch_id) / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        archive_basename = sanitize_name(batch.archive_name.rsplit(".", 1)[0] or batch.title)
        out_path = export_dir / f"{archive_basename}-{mode}-batch.zip"
        return build_batch_export_zip(
            out_path=out_path,
            batch_title=batch.title,
            archive_name=batch.archive_name,
            jobs=completed_jobs,
            warnings=export_warnings,
            mode=mode,
            resolve_url=self._job_store.resolve_storage_url,
        )

    async def _build_summary(self, job_ids: list[str]) -> BatchSummary:
        jobs_response = await self._job_store.list_jobs()
        jobs_by_id = {job.job_id: job for job in jobs_response.jobs}
        items = [jobs_by_id[job_id] for job_id in job_ids if job_id in jobs_by_id]
        missing_jobs = max(len(job_ids) - len(items), 0)
        queued = sum(1 for job in items if job.status == "queued")
        running = sum(1 for job in items if job.status == "running")
        completed = sum(1 for job in items if job.status == "completed")
        failed = sum(1 for job in items if job.status == "failed") + missing_jobs
        degraded = sum(1 for job in items if job.status == "completed" and job.degraded_recognition)
        rescued = sum(1 for job in items if job.status == "completed" and job.emergency_fallback_used)
        total = len(job_ids)
        return BatchSummary(
            total_jobs=total,
            queued_jobs=queued,
            running_jobs=running,
            completed_jobs=completed,
            failed_jobs=failed,
            degraded_jobs=degraded,
            rescued_jobs=rescued,
            finished=total > 0 and queued == 0 and running == 0 and completed + failed == total,
        )
