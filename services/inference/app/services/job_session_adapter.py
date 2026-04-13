from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..models.job_schemas import DrawingJobStatus, PreviewSessionResponse
from .job_store import InMemoryJobStore
from .session_store import InMemorySessionStore


class JobPreviewSessionAdapter:
    def __init__(self, *, job_store: InMemoryJobStore, session_store: InMemorySessionStore) -> None:
        self._job_store = job_store
        self._session_store = session_store
        self._lock = asyncio.Lock()
        self._preview_sessions: dict[str, str] = {}

    async def open_preview_session(self, job_id: str, *, page_index: int | None = None) -> PreviewSessionResponse:
        preview_key = self._preview_key(job_id, page_index)
        existing_session_id = await self._get_existing_session_id(preview_key)
        if existing_session_id is not None:
            return PreviewSessionResponse(session_id=existing_session_id)

        job = await self._job_store.get_job(job_id)
        if job.status != DrawingJobStatus.COMPLETED:
            raise ValueError("Preview можно открыть только после завершения распознавания.")
        if job.result is None:
            raise ValueError("У задачи ещё нет результата для preview.")
        prepared_raster_path, effective_page_index = self._resolve_prepared_raster_path(job_id, job, page_index)
        if not prepared_raster_path.is_file():
            raise ValueError(f"Не найден подготовленный raster для preview: {prepared_raster_path}")
        near_tie_items = self._load_near_tie_items(job_id)
        page_rows = [row for row in job.result.rows if row.page_index == effective_page_index]
        page_held_back_rows = [row for row in job.result.held_back_rows if row.page_index == effective_page_index]
        page_missing_labels = job.result.missing_labels if len(job.result.pages) <= 1 else []
        page_title_suffix = (
            f" · стр. {effective_page_index + 1}"
            if len(job.result.pages) > 1
            else ""
        )

        session_response = await self._session_store.create_session_from_job_result(
            title=f"{job.title}{page_title_suffix} · preview",
            source_file_name=job.input.drawing_name,
            raster_path=prepared_raster_path,
            rows=page_rows,
            held_back_rows=page_held_back_rows,
            missing_labels=page_missing_labels,
            document_confidence=job.result.summary.document_confidence,
            source_job_id=job.job_id,
            near_tie_items=[item for item in near_tie_items if int(item.get("page_index") or 0) == effective_page_index],
        )

        async with self._lock:
            self._preview_sessions[preview_key] = session_response.session.session_id

        return PreviewSessionResponse(session_id=session_response.session.session_id)

    async def _get_existing_session_id(self, preview_key: str) -> str | None:
        async with self._lock:
            session_id = self._preview_sessions.get(preview_key)
        if not session_id:
            return None

        try:
            await self._session_store.get_session(session_id)
        except KeyError:
            async with self._lock:
                self._preview_sessions.pop(preview_key, None)
            return None

        return session_id

    def _preview_key(self, job_id: str, page_index: int | None) -> str:
        if page_index is None:
            return job_id
        return f"{job_id}:page:{page_index}"

    def _resolve_prepared_raster_path(self, job_id: str, job, page_index: int | None) -> tuple[Path, int]:
        page_count = len(job.result.pages) if job.result is not None else 0
        if page_count <= 1:
            return self._job_store.jobs_root / job_id / "work" / "prepared-drawing.png", 0

        if page_index is None:
            raise ValueError("Для многостраничного PDF сначала выбери страницу, которую нужно проверить вручную.")
        if page_index < 0 or page_index >= page_count:
            raise ValueError(f"Страница {page_index + 1} выходит за пределы документа.")

        prepared_pages_dir = self._job_store.jobs_root / job_id / "work" / "prepared-pages"
        raster_path = prepared_pages_dir / f"prepared-page-{page_index + 1:03d}.png"
        return raster_path, page_index

    def _load_near_tie_items(self, job_id: str) -> list[dict]:
        artifacts_dir = self._job_store.jobs_root / job_id / "artifacts"
        near_tie_files = sorted(artifacts_dir.glob("*.near-tie.json"))
        if not near_tie_files:
            return []
        try:
            payload = json.loads(near_tie_files[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]
