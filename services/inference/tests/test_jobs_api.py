from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import json
import sys
import zipfile
import py7zr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from PIL import Image

from app.api import jobs as jobs_api
from app.api import sessions as sessions_api
from app.main import create_app
from app.core.config import settings
from app.models.job_schemas import (
    DrawingJob,
    DrawingJobInput,
    DrawingJobResult,
    DrawingJobSummary,
    DrawingJobStatus,
    DrawingResultPage,
    DrawingResultRow,
    DrawingResultRowStatus,
    ResultPoint,
)
from app.services.batch_store import InMemoryBatchStore
from app.services.job_runner import JobPageArtifact, JobRunOutput
from app.services.job_session_adapter import JobPreviewSessionAdapter
from app.services.job_store import InMemoryJobStore
from app.services.result_exports import (
    write_json_payload,
    write_result_csv,
    write_result_json,
    write_result_xlsx,
    write_result_zip,
)
from app.services.session_store import InMemorySessionStore


def make_png(width: int = 800, height: int = 600) -> BytesIO:
    image = Image.new("RGB", (width, height), color=(245, 244, 238))
    payload = BytesIO()
    image.save(payload, format="PNG")
    payload.seek(0)
    return payload


def make_client(tmp_path, monkeypatch) -> tuple[TestClient, InMemoryJobStore, InMemoryBatchStore, InMemorySessionStore]:
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "var"))
    session_store = InMemorySessionStore()
    job_store = InMemoryJobStore()
    batch_store = InMemoryBatchStore(job_store=job_store)
    preview_adapter = JobPreviewSessionAdapter(job_store=job_store, session_store=session_store)

    sessions_api.service = session_store
    jobs_api.service = job_store
    jobs_api.batch_service = batch_store
    jobs_api.preview_adapter = preview_adapter

    app = create_app()
    return TestClient(app), job_store, batch_store, session_store


def build_run_output(job_dir: Path, result: DrawingJobResult) -> JobRunOutput:
    artifacts_dir = job_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    production_rows = [row for row in result.rows if row.status != DrawingResultRowStatus.UNCERTAIN]
    production_csv_path = write_result_csv(result, artifacts_dir / "coordinates.csv", rows=production_rows)
    production_xlsx_path = write_result_xlsx(result, artifacts_dir / "coordinates.xlsx", rows=production_rows, export_label="production")
    review_csv_path = write_result_csv(result, artifacts_dir / "review.csv", rows=result.rows)
    review_xlsx_path = write_result_xlsx(result, artifacts_dir / "review.xlsx", rows=result.rows, export_label="review")
    result_json_path = write_result_json(result, artifacts_dir / "result.json")

    source_json_payload = {
        "markers": [
            {
                "label": row.label,
                "page_index": row.page_index,
                "status": row.status.value,
            }
            for row in result.rows
        ]
    }
    source_json_path = write_json_payload(source_json_payload, artifacts_dir / "source-markers.json")

    held_back_csv_path = None
    if result.held_back_rows:
        held_back_csv_path = write_result_csv(result, artifacts_dir / "held-back.csv", rows=result.held_back_rows)

    near_tie_csv_path = None
    near_tie_json_path = None
    if result.summary.near_tie_ambiguity_count > 0:
        near_tie_rows = [
            {
                "row": row.row,
                "page_index": row.page_index,
                "label": row.label,
                "alt_label": "5",
                "ocr_gap": 0.0052,
                "note": row.note or "",
            }
            for row in result.held_back_rows[:1]
        ]
        near_tie_csv_path = artifacts_dir / "near-tie.csv"
        near_tie_csv_path.write_text("row,page_index,label,alt_label,ocr_gap,note\n1,0,7,5,0.0052,near-tie\n", encoding="utf-8")
        near_tie_json_path = write_json_payload({"items": near_tie_rows}, artifacts_dir / "near-tie.json")

    overlay_paths: list[Path] = []
    page_artifacts: list[JobPageArtifact] = []
    for page in result.pages:
        overlay_path = artifacts_dir / f"overlay-page-{page.page_index + 1:03d}.png"
        Image.new("RGB", (page.width, page.height), color=(255, 255, 255)).save(overlay_path, format="PNG")
        overlay_paths.append(overlay_path)
        page_artifacts.append(
            JobPageArtifact(
                page_index=page.page_index,
                overlay_path=overlay_path,
                source_json_path=source_json_path,
                width=page.width,
                height=page.height,
            )
        )

    production_zip_path = write_result_zip(artifacts_dir / "production.zip", [production_csv_path, production_xlsx_path, *overlay_paths])
    review_zip_path = write_result_zip(
        artifacts_dir / "review.zip",
        [
            review_csv_path,
            review_xlsx_path,
            result_json_path,
            source_json_path,
            *overlay_paths,
            *([held_back_csv_path] if held_back_csv_path else []),
            *([near_tie_csv_path] if near_tie_csv_path else []),
            *([near_tie_json_path] if near_tie_json_path else []),
        ],
    )

    return JobRunOutput(
        result=result,
        production_csv_path=production_csv_path,
        production_xlsx_path=production_xlsx_path,
        production_zip_path=production_zip_path,
        review_csv_path=review_csv_path,
        held_back_csv_path=held_back_csv_path,
        near_tie_csv_path=near_tie_csv_path,
        near_tie_json_path=near_tie_json_path,
        review_xlsx_path=review_xlsx_path,
        review_zip_path=review_zip_path,
        source_json_path=source_json_path,
        result_json_path=result_json_path,
        page_artifacts=page_artifacts,
    )


def make_single_page_result() -> DrawingJobResult:
    return DrawingJobResult(
        source_file="drawing.png",
        source_labels_file="labels.csv",
        pages=[DrawingResultPage(page_index=0, width=800, height=600, row_count=2, held_back_count=0)],
        rows=[
            DrawingResultRow(
                row=1,
                label="12",
                page_index=0,
                center=ResultPoint(x=120, y=150),
                top_left=ResultPoint(x=110, y=140),
                final_score=0.94,
                status=DrawingResultRowStatus.FOUND,
            ),
            DrawingResultRow(
                row=2,
                label="7",
                page_index=0,
                center=ResultPoint(x=320, y=350),
                top_left=ResultPoint(x=310, y=340),
                final_score=0.72,
                status=DrawingResultRowStatus.UNCERTAIN,
                note="needs review",
            ),
        ],
        summary=DrawingJobSummary(
            total_rows=2,
            found_count=1,
            uncertain_count=1,
            document_confidence=0.83,
            review_recommended=True,
            review_reasons=["Есть спорные точки."],
            status_text="ready",
        ),
    )


def make_multi_page_result() -> DrawingJobResult:
    return DrawingJobResult(
        source_file="drawing.pdf",
        source_labels_file="labels.csv",
        pages=[
            DrawingResultPage(page_index=0, width=800, height=600, row_count=1, held_back_count=0),
            DrawingResultPage(page_index=1, width=800, height=600, row_count=1, held_back_count=1),
        ],
        rows=[
            DrawingResultRow(
                row=1,
                label="10",
                page_index=0,
                center=ResultPoint(x=100, y=120),
                top_left=ResultPoint(x=90, y=110),
                final_score=0.95,
                status=DrawingResultRowStatus.FOUND,
            ),
            DrawingResultRow(
                row=2,
                label="20",
                page_index=1,
                center=ResultPoint(x=210, y=260),
                top_left=ResultPoint(x=200, y=250),
                final_score=0.76,
                status=DrawingResultRowStatus.UNCERTAIN,
                note="page 2 review",
            ),
        ],
        held_back_rows=[
            DrawingResultRow(
                row=3,
                label="7",
                page_index=1,
                center=ResultPoint(x=240, y=290),
                top_left=ResultPoint(x=230, y=280),
                final_score=0.55,
                status=DrawingResultRowStatus.UNCERTAIN,
                note="OCR near-tie ambiguity: bbox спорит между 7 и 5.",
            )
        ],
        summary=DrawingJobSummary(
            total_rows=2,
            found_count=1,
            uncertain_count=1,
            held_back_count=1,
            near_tie_ambiguity_count=1,
            review_recommended=True,
            review_reasons=["Есть спорные точки на странице 2."],
            status_text="ready",
        ),
    )


def test_create_job_processes_and_lists_completed_result(tmp_path, monkeypatch):
    client, job_store, _, _ = make_client(tmp_path, monkeypatch)

    def fake_run_job_pipeline(job_dir: Path, job: DrawingJob):
        return build_run_output(job_dir, make_single_page_result())

    monkeypatch.setattr("app.services.job_store.run_job_pipeline", fake_run_job_pipeline)

    response = client.post(
        "/api/jobs",
        data={"title": "Smoke job"},
        files={
            "drawing": ("drawing.png", make_png(), "image/png"),
            "labels": ("labels.csv", b"12\n7\n", "text/csv"),
        },
    )
    assert response.status_code == 201
    job_id = response.json()["job"]["jobId"]
    payload = client.get(f"/api/jobs/{job_id}").json()["job"]
    assert payload["status"] == "completed"
    assert payload["result"]["artifacts"]["csvUrl"]
    assert payload["result"]["summary"]["foundCount"] == 1

    jobs = client.get("/api/jobs").json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Smoke job"
    assert jobs[0]["status"] == "completed"
    assert job_store.resolve_storage_url(payload["result"]["artifacts"]["csvUrl"]).exists()


def test_preview_session_for_multi_page_job_uses_selected_page(tmp_path, monkeypatch):
    client, job_store, _, session_store = make_client(tmp_path, monkeypatch)

    def fake_run_job_pipeline(job_dir: Path, job: DrawingJob):
        work_pages = job_dir / "work" / "prepared-pages"
        work_pages.mkdir(parents=True, exist_ok=True)
        for page_idx in range(2):
            Image.new("RGB", (800, 600), color=(255, 255, 255)).save(work_pages / f"prepared-page-{page_idx + 1:03d}.png", format="PNG")
        return build_run_output(job_dir, make_multi_page_result())

    monkeypatch.setattr("app.services.job_store.run_job_pipeline", fake_run_job_pipeline)

    created = client.post(
        "/api/jobs",
        data={"title": "Multi page"},
        files={
            "drawing": ("drawing.pdf", b"%PDF-1.4 fake", "application/pdf"),
            "labels": ("labels.csv", b"10\n20\n", "text/csv"),
        },
    )
    assert created.status_code == 201
    job_id = created.json()["job"]["jobId"]

    preview = client.post(f"/api/jobs/{job_id}/preview-session?page_index=1")
    assert preview.status_code == 200
    session_id = preview.json()["sessionId"]

    session = session_store._get_session(session_id)
    labels = sorted(marker.label for marker in session.markers if marker.label)
    assert "20" in labels
    assert "10" not in labels
    assert any(conflict.message and "review" in conflict.message.lower() for conflict in session.pipeline_conflicts)


def test_batch_create_list_get_and_export(tmp_path, monkeypatch):
    client, job_store, batch_store, _ = make_client(tmp_path, monkeypatch)

    def fake_run_job_pipeline(job_dir: Path, job: DrawingJob):
        return build_run_output(job_dir, make_single_page_result())

    monkeypatch.setattr("app.services.job_store.run_job_pipeline", fake_run_job_pipeline)

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("first.png", make_png().getvalue())
        archive.writestr("first.csv", "12\n7\n")
        archive.writestr("second.png", make_png().getvalue())
        archive.writestr("second.csv", "12\n7\n")
    zip_buffer.seek(0)

    created = client.post(
        "/api/jobs/batch",
        data={"title_prefix": "Batch"},
        files={"archive": ("batch.zip", zip_buffer.getvalue(), "application/zip")},
    )
    assert created.status_code == 201
    payload = created.json()
    batch_id = payload["batchId"]
    assert len(payload["jobs"]) == 2

    listing = client.get("/api/batches")
    assert listing.status_code == 200
    assert any(item["batchId"] == batch_id for item in listing.json()["batches"])

    detailed = client.get(f"/api/batches/{batch_id}")
    assert detailed.status_code == 200
    assert detailed.json()["batch"]["summary"]["finished"] is True

    production_export = client.get(f"/api/batches/{batch_id}/export?mode=production")
    assert production_export.status_code == 200
    with zipfile.ZipFile(BytesIO(production_export.content)) as archive:
        names = set(archive.namelist())
        assert any(name.endswith("/manifest/rows.csv") for name in names)
        assert any(name.endswith("/coordinates.csv") for name in names)
        issues_name = next(name for name in names if name.endswith("/manifest/export-issues.json"))
        issues = json.loads(archive.read(issues_name).decode("utf-8"))
        assert issues == []

    review_export = client.get(f"/api/batches/{batch_id}/export?mode=review")
    assert review_export.status_code == 200
    with zipfile.ZipFile(BytesIO(review_export.content)) as archive:
        names = set(archive.namelist())
        assert any(name.endswith("/review.csv") for name in names)
        assert any(name.endswith("/result.json") for name in names)

    assert batch_store._get_batch(batch_id).job_ids
    assert job_store._jobs


def test_batch_accepts_7z_archive_with_strict_pairs(tmp_path, monkeypatch):
    client, job_store, batch_store, _ = make_client(tmp_path, monkeypatch)

    def fake_run_job_pipeline(job_dir: Path, job: DrawingJob):
        return build_run_output(job_dir, make_single_page_result())

    monkeypatch.setattr("app.services.job_store.run_job_pipeline", fake_run_job_pipeline)

    archive_path = tmp_path / "batch.7z"
    with py7zr.SevenZipFile(archive_path, "w") as archive:
        archive.writestr(make_png().getvalue(), "first.png")
        archive.writestr("12\n7\n", "first.csv")
        archive.writestr(make_png().getvalue(), "orphan.png")

    created = client.post(
        "/api/jobs/batch",
        files={"archive": ("batch.7z", archive_path.read_bytes(), "application/x-7z-compressed")},
    )
    assert created.status_code == 201
    payload = created.json()
    assert len(payload["jobs"]) == 1
    assert any(warning["code"] == "orphan_drawing" for warning in payload["warnings"])

    batch_id = payload["batchId"]
    detailed = client.get(f"/api/batches/{batch_id}")
    assert detailed.status_code == 200
    assert detailed.json()["batch"]["summary"]["totalJobs"] == 1
    assert batch_store._get_batch(batch_id).job_ids
    assert job_store._jobs


def test_batch_export_reports_missing_job_as_issue(tmp_path, monkeypatch):
    client, job_store, batch_store, _ = make_client(tmp_path, monkeypatch)

    job_id = f"broken-job-{tmp_path.name}"
    result = make_single_page_result()
    result.artifacts.csv_url = "/storage/missing/coordinates.csv"
    result.artifacts.xlsx_url = "/storage/missing/coordinates.xlsx"
    broken_job = DrawingJob(
        job_id=job_id,
        title="Broken files",
        status=DrawingJobStatus.COMPLETED,
        input=DrawingJobInput(
            drawing_name="broken.png",
            drawing_url="/input/broken",
            labels_name="broken.csv",
            labels_url="/labels/broken",
            has_labels=True,
        ),
        result=result,
    )
    job_store._jobs[job_id] = broken_job
    job_store._persist_job(broken_job)

    batch_id = batch_store.new_batch_id()
    asyncio.run(
        batch_store.create_batch(
            batch_id=batch_id,
            title="Broken batch",
            archive_name="broken.zip",
            title_prefix=None,
            job_ids=[job_id],
            warnings=[],
        )
    )

    response = client.get(f"/api/batches/{batch_id}/export?mode=production")
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        issues_name = next(name for name in archive.namelist() if name.endswith("/manifest/export-issues.json"))
        issues = json.loads(archive.read(issues_name).decode("utf-8"))
    assert any(item["code"] == "missing_artifact_file" for item in issues)
