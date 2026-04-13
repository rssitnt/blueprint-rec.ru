from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.datastructures import Headers
from fastapi.staticfiles import StaticFiles

from .api.jobs import router as jobs_router, service as jobs_service
from .api.sessions import router as sessions_router
from .core.config import settings


class StorageStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        origin = Headers(scope=scope).get("origin")
        if origin and origin in settings.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response


class StorageCorsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        origin = request.headers.get("origin")
        if request.url.path.startswith(settings.storage_mount_path) and origin and origin in settings.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_title,
        version="0.2.0",
        debug=settings.debug,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(StorageCorsMiddleware)
    storage_root = Path(settings.storage_dir)
    storage_root.mkdir(parents=True, exist_ok=True)
    app.mount(settings.storage_mount_path, StorageStaticFiles(directory=storage_root), name="storage")
    app.include_router(sessions_router)
    app.include_router(jobs_router)

    @app.on_event("startup")
    async def repair_stale_jobs_on_startup() -> None:
        asyncio.create_task(jobs_service.repair_stale_jobs())

    return app


app = create_app()
