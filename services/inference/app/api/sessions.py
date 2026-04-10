from __future__ import annotations

from datetime import datetime
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from ..models.schemas import (
    Actor,
    CreateSessionRequest,
    CreateSessionResponse,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionListResponse,
    UploadDocumentResponse,
)
from ..services.session_store import InMemorySessionStore


router = APIRouter()
service = InMemorySessionStore()


@router.get("/health")
@router.get("/api/health")
async def healthcheck():
    return {
        "status": "ok",
        "service": "annotation",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions() -> SessionListResponse:
    return await service.list_sessions()


@router.post("/api/sessions", response_model=CreateSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(payload: CreateSessionRequest) -> CreateSessionResponse:
    return await service.create_session(payload)


@router.get("/api/sessions/{session_id}", response_model=CreateSessionResponse)
async def get_session(session_id: str) -> CreateSessionResponse:
    try:
        session = await service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return CreateSessionResponse(session=session)


@router.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_session(session_id: str) -> Response:
    try:
        await service.delete_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/sessions/{session_id}/document", response_model=UploadDocumentResponse)
async def upload_document(session_id: str, file: UploadFile = File(description="Drawing image")) -> UploadDocumentResponse:
    try:
        await service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        file_name = file.filename or "drawing.png"
        file.file.seek(0)
        body = file.file.read()
        file.file.seek(0)
        stored = service.prepare_upload(
            session_id=session_id,
            file_name=file_name,
            content_type=file.content_type or "application/octet-stream",
            source_stream=file.file,
            size_bytes=len(body),
        )
        return await service.upload_document(session_id, stored)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        file.file.close()


@router.post("/api/sessions/{session_id}/commands", response_model=SessionCommandResponse)
async def apply_command(session_id: str, payload: SessionCommandRequest) -> SessionCommandResponse:
    try:
        return await service.apply_command(session_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/sessions/{session_id}/detect-candidates", response_model=CreateSessionResponse)
async def detect_candidates(session_id: str) -> CreateSessionResponse:
    try:
        return await service.detect_candidates(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/sessions/{session_id}/auto-annotate", response_model=CreateSessionResponse)
async def auto_annotate(session_id: str) -> CreateSessionResponse:
    try:
        return await service.auto_annotate(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/sessions/{session_id}/candidates/{candidate_id}/reject", response_model=CreateSessionResponse)
async def reject_candidate(session_id: str, candidate_id: str) -> CreateSessionResponse:
    try:
        return await service.reject_candidate(session_id, candidate_id, actor=Actor.HUMAN)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str) -> StreamingResponse:
    try:
        archive_name, archive_body = await service.export_session_archive(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(archive_name)}"
    }
    return StreamingResponse(BytesIO(archive_body), media_type="application/zip", headers=headers)
