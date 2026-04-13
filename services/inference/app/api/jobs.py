from __future__ import annotations

from io import BytesIO
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse

import py7zr

from ..models.job_schemas import (
    BatchListResponse,
    BatchResponse,
    BatchJobWarning,
    CreateBatchJobsResponse,
    CreateJobResponse,
    JobListResponse,
    PreviewSessionResponse,
)
from ..services.batch_store import InMemoryBatchStore
from ..services.job_store import ALLOWED_DRAWING_EXTENSIONS, ALLOWED_LABEL_EXTENSIONS, InMemoryJobStore
from ..services.job_session_adapter import JobPreviewSessionAdapter
from .sessions import service as session_service


router = APIRouter()
service = InMemoryJobStore()
batch_service = InMemoryBatchStore(job_store=service)
preview_adapter = JobPreviewSessionAdapter(job_store=service, session_store=session_service)

SUPPORTED_ARCHIVE_SUFFIXES = (
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".tgz",
    ".tar.gz",
    ".tbz2",
    ".tar.bz2",
    ".txz",
    ".tar.xz",
)


def _normalized_base_name(file_name: str) -> str:
    path = PurePosixPath(str(file_name).replace("\\", "/"))
    return path.stem.strip().lower()


def _display_name_from_batch(base_name: str, title_prefix: str | None) -> str:
    pretty_name = base_name.strip() or "drawing"
    if title_prefix:
        return f"{title_prefix.strip()} · {pretty_name}"
    return pretty_name


def _archive_suffix(file_name: str) -> str:
    normalized = PurePosixPath(str(file_name).replace("\\", "/")).name.lower()
    for suffix in sorted(SUPPORTED_ARCHIVE_SUFFIXES, key=len, reverse=True):
        if normalized.endswith(suffix):
            return suffix
    return Path(normalized).suffix.lower()


def _read_extracted_entries(root_dir: Path) -> list[tuple[str, bytes, int]]:
    entries: list[tuple[str, bytes, int]] = []
    for file_path in sorted(root_dir.rglob("*")):
        if not file_path.is_file():
            continue
        relative_name = file_path.relative_to(root_dir).as_posix()
        payload = file_path.read_bytes()
        entries.append((relative_name, payload, len(payload)))
    return entries


def _extract_with_tar_cli(archive_name: str, archive_body: bytes) -> list[tuple[str, bytes, int]]:
    tar_executable = shutil.which("tar")
    if not tar_executable:
        raise HTTPException(status_code=400, detail="RAR-архивы сейчас недоступны: в системе не найден tar.exe.")

    with tempfile.TemporaryDirectory(prefix="blueprint-rec-archive-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        archive_path = temp_dir / PurePosixPath(archive_name).name
        archive_path.write_bytes(archive_body)
        extract_dir = temp_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [tar_executable, "-xf", str(archive_path), "-C", str(extract_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            detail = "Не удалось распаковать архив через системный tar."
            if stderr:
                detail = f"{detail} {stderr}"
            raise HTTPException(status_code=400, detail=detail)
        return _read_extracted_entries(extract_dir)


def _iter_archive_entries(archive_name: str, archive_body: bytes) -> list[tuple[str, bytes, int]]:
    archive_suffix = _archive_suffix(archive_name)
    archive_buffer = BytesIO(archive_body)

    if archive_suffix == ".zip":
        try:
            zip_file = zipfile.ZipFile(archive_buffer)
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="Не удалось прочитать ZIP-архив.") from exc

        entries: list[tuple[str, bytes, int]] = []
        with zip_file:
            for entry in zip_file.infolist():
                if entry.is_dir():
                    continue
                with zip_file.open(entry, "r") as stream:
                    entries.append((entry.filename, stream.read(), entry.file_size))
        return entries

    if archive_suffix == ".7z":
        with tempfile.TemporaryDirectory(prefix="blueprint-rec-7z-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            archive_path = temp_dir / PurePosixPath(archive_name).name
            archive_path.write_bytes(archive_body)
            extract_dir = temp_dir / "extracted"
            extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                with py7zr.SevenZipFile(archive_path, mode="r") as archive_file:
                    archive_file.extractall(path=extract_dir)
            except py7zr.Bad7zFile as exc:
                raise HTTPException(status_code=400, detail="Не удалось прочитать 7Z-архив.") from exc
            return _read_extracted_entries(extract_dir)

    if archive_suffix == ".rar":
        return _extract_with_tar_cli(archive_name, archive_body)

    try:
        tar_archive = tarfile.open(fileobj=archive_buffer, mode="r:*")
    except tarfile.TarError as exc:
        supported = ", ".join(SUPPORTED_ARCHIVE_SUFFIXES)
        raise HTTPException(status_code=400, detail=f"Архив не удалось открыть. Сейчас поддерживаются: {supported}.") from exc

    entries = []
    with tar_archive:
        for member in tar_archive.getmembers():
            if not member.isfile():
                continue
            extracted = tar_archive.extractfile(member)
            if extracted is None:
                continue
            with extracted:
                entries.append((member.name, extracted.read(), member.size))
    return entries


@router.get("/api/jobs", response_model=JobListResponse)
async def list_jobs() -> JobListResponse:
    return await service.list_jobs()


@router.get("/api/batches", response_model=BatchListResponse)
async def list_batches() -> BatchListResponse:
    return await batch_service.list_batches()


@router.get("/api/batches/{batch_id}", response_model=BatchResponse)
async def get_batch(batch_id: str) -> BatchResponse:
    try:
        return await batch_service.get_batch(batch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/batches/{batch_id}/export")
async def export_batch(batch_id: str, mode: str = Query(default="production")) -> FileResponse:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"production", "review"}:
        raise HTTPException(status_code=400, detail="mode должен быть production или review.")
    try:
        archive_path = await batch_service.build_export(batch_id, mode=normalized_mode)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=archive_path.name,
    )


@router.post("/api/jobs", response_model=CreateJobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    background_tasks: BackgroundTasks,
    drawing: UploadFile = File(description="Drawing file"),
    labels: UploadFile | None = File(default=None, description="Optional labels table"),
    title: str | None = Form(default=None),
) -> CreateJobResponse:
    job_id = service.new_job_id()
    try:
        drawing_name = drawing.filename or "drawing.png"
        drawing.file.seek(0)
        drawing_body = drawing.file.read()
        drawing.file.seek(0)
        stored_drawing = service.prepare_upload(
            job_id=job_id,
            slot="drawing",
            file_name=drawing_name,
            content_type=drawing.content_type or "application/octet-stream",
            source_stream=drawing.file,
            size_bytes=len(drawing_body),
        )

        stored_labels = None
        if labels is not None:
            labels_name = labels.filename or "labels.xlsx"
            labels.file.seek(0)
            labels_body = labels.file.read()
            labels.file.seek(0)
            stored_labels = service.prepare_upload(
                job_id=job_id,
                slot="labels",
                file_name=labels_name,
                content_type=labels.content_type or "application/octet-stream",
                source_stream=labels.file,
                size_bytes=len(labels_body),
            )

        response = await service.create_job(
            job_id=job_id,
            title=title,
            drawing_upload=stored_drawing,
            labels_upload=stored_labels,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        drawing.file.close()
        if labels is not None:
            labels.file.close()

    background_tasks.add_task(service.process_job, job_id)
    return response


@router.post("/api/jobs/batch", response_model=CreateBatchJobsResponse, status_code=status.HTTP_201_CREATED)
async def create_batch_jobs(
    background_tasks: BackgroundTasks,
    archive: UploadFile = File(description="Archive with drawings and optional labels tables"),
    title_prefix: str | None = Form(default=None),
) -> CreateBatchJobsResponse:
    archive_name = archive.filename or "batch.zip"

    try:
        archive.file.seek(0)
        archive_body = archive.file.read()
        archive_entries = _iter_archive_entries(archive_name, archive_body)

        drawing_entries: dict[str, tuple[bytes, str, int]] = {}
        label_entries: dict[str, tuple[bytes, str, int]] = {}
        warnings: list[BatchJobWarning] = []
        batch_id = batch_service.new_batch_id()

        for entry_name_raw, entry_bytes, entry_size in archive_entries:
            entry_name = PurePosixPath(entry_name_raw).name
            if not entry_name or entry_name.startswith("."):
                continue

            suffix = Path(entry_name).suffix.lower()
            base_name = _normalized_base_name(entry_name)
            if not base_name:
                warnings.append(
                    BatchJobWarning(
                        code="invalid_name",
                        message="Файл пропущен: не удалось определить базовое имя.",
                        file_name=entry_name,
                        base_name=None,
                    )
                )
                continue

            if suffix in ALLOWED_DRAWING_EXTENSIONS:
                if base_name in drawing_entries:
                    warnings.append(
                        BatchJobWarning(
                            code="duplicate_drawing",
                            message="В архиве несколько чертежей с одним и тем же именем без расширения. Взял только первый.",
                            file_name=entry_name,
                            base_name=base_name,
                        )
                    )
                    continue
                drawing_entries[base_name] = (entry_bytes, entry_name, entry_size)
                continue

            if suffix in ALLOWED_LABEL_EXTENSIONS:
                if base_name in label_entries:
                    warnings.append(
                        BatchJobWarning(
                            code="duplicate_labels",
                            message="В архиве несколько таблиц с одним и тем же именем без расширения. Взял только первую.",
                            file_name=entry_name,
                            base_name=base_name,
                        )
                    )
                    continue
                label_entries[base_name] = (entry_bytes, entry_name, entry_size)
                continue

            warnings.append(
                BatchJobWarning(
                    code="ignored_file",
                    message="Файл пропущен: неподдерживаемый формат.",
                    file_name=entry_name,
                    base_name=base_name,
                )
            )

        created_jobs = []
        for base_name, (drawing_bytes, drawing_name, drawing_size) in sorted(drawing_entries.items()):
            labels_match = label_entries.get(base_name)
            if labels_match is None:
                warnings.append(
                    BatchJobWarning(
                        code="orphan_drawing",
                        message="Чертёж пропущен: в архиве нет таблицы с таким же именем.",
                        file_name=drawing_name,
                        base_name=base_name,
                    )
                )
                continue

            job_id = service.new_job_id()
            with BytesIO(drawing_bytes) as drawing_buffer:
                stored_drawing = service.prepare_upload(
                    job_id=job_id,
                    slot="drawing",
                    file_name=drawing_name,
                    content_type="application/octet-stream",
                    source_stream=drawing_buffer,
                    size_bytes=drawing_size,
                )

            stored_labels = None
            labels_bytes, labels_name, labels_size = labels_match
            with BytesIO(labels_bytes) as labels_buffer:
                stored_labels = service.prepare_upload(
                    job_id=job_id,
                    slot="labels",
                    file_name=labels_name,
                    content_type="application/octet-stream",
                    source_stream=labels_buffer,
                    size_bytes=labels_size,
                )

            response = await service.create_job(
                job_id=job_id,
                title=_display_name_from_batch(base_name, title_prefix),
                drawing_upload=stored_drawing,
                labels_upload=stored_labels,
            )
            created_jobs.append(response.job)
            background_tasks.add_task(service.process_job, job_id)

        for base_name, (_, labels_name, _) in sorted(label_entries.items()):
            if base_name in drawing_entries:
                continue
            warnings.append(
                BatchJobWarning(
                    code="orphan_labels",
                    message="Таблица пропущена: в архиве нет чертежа с таким же именем.",
                    file_name=labels_name,
                    base_name=base_name,
                )
            )

        if not created_jobs:
            raise HTTPException(status_code=400, detail="В архиве не найдено ни одной пары чертёж+таблица с одинаковым именем.")

        await batch_service.create_batch(
            batch_id=batch_id,
            title=(title_prefix or "").strip() or Path(archive_name).stem or "Batch",
            archive_name=archive_name,
            title_prefix=title_prefix,
            job_ids=[job.job_id for job in created_jobs],
            warnings=warnings,
        )
        return CreateBatchJobsResponse(batch_id=batch_id, jobs=created_jobs, warnings=warnings)
    finally:
        archive.file.close()


@router.get("/api/jobs/{job_id}", response_model=CreateJobResponse)
async def get_job(job_id: str) -> CreateJobResponse:
    try:
        job = await service.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CreateJobResponse(job=job)


@router.post("/api/jobs/{job_id}/preview-session", response_model=PreviewSessionResponse)
async def open_job_preview_session(job_id: str, page_index: int | None = Query(default=None, ge=0)) -> PreviewSessionResponse:
    try:
        return await preview_adapter.open_preview_session(job_id, page_index=page_index)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_job(job_id: str) -> Response:
    try:
        await service.delete_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
