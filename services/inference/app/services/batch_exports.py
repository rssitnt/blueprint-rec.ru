from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable, Literal

from ..models.job_schemas import DrawingJob, DrawingResultRow
from .result_exports import CSV_COLUMNS, _as_csv_row, _format_cell


BatchExportMode = Literal["production", "review"]

_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-zА-Яа-я._-]+")


def sanitize_name(value: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("-", (value or "").strip())
    cleaned = cleaned.strip("-._")
    return cleaned or "item"


def _summary_manifest_rows(jobs: Iterable[DrawingJob]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in jobs:
        summary = job.result.summary if job.result else None
        rows.append(
            {
                "job_id": job.job_id,
                "title": job.title,
                "status": job.status.value,
                "drawing_name": job.input.drawing_name,
                "labels_name": job.input.labels_name or "",
                "document_confidence": _format_cell(summary.document_confidence) if summary else "",
                "degraded_recognition": "yes" if summary and summary.degraded_recognition else "no",
                "emergency_fallback_used": "yes" if summary and summary.emergency_fallback_used else "no",
                "total_rows": summary.total_rows if summary else 0,
                "found_count": summary.found_count if summary else 0,
                "missing_count": summary.missing_count if summary else 0,
                "uncertain_count": summary.uncertain_count if summary else 0,
                "held_back_count": summary.held_back_count if summary else 0,
                "review_recommended": "yes" if summary and summary.review_recommended else "no",
                "failure_message": summary.failure_message if summary and summary.failure_message else job.error_message or "",
            }
        )
    return rows


def _combined_result_rows(jobs: Iterable[DrawingJob], *, mode: BatchExportMode) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in jobs:
        if not job.result:
            continue
        export_rows = list(job.result.rows)
        if mode == "production":
            export_rows = [row for row in export_rows if row.status.value != "uncertain"]
        for result_row in export_rows:
            row_payload = _as_csv_row(result_row)
            rows.append(
                {
                    "job_id": job.job_id,
                    "title": job.title,
                    "drawing_name": job.input.drawing_name,
                    "labels_name": job.input.labels_name or "",
                    **{key: _format_cell(value) for key, value in row_payload.items()},
                }
            )
    return rows


def _write_csv_to_archive(archive: zipfile.ZipFile, *, arcname: str, columns: list[str], rows: list[dict[str, Any]]) -> None:
    import csv
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    archive.writestr(arcname, buffer.getvalue().encode("utf-8-sig"))


def _iter_job_files(job: DrawingJob, *, mode: BatchExportMode, resolve_url) -> Iterable[tuple[Path, str]]:
    if not job.result:
        return []

    artifacts = job.result.artifacts
    if mode == "production":
        urls = [
            ("coordinates.csv", artifacts.csv_url),
            ("coordinates.xlsx", artifacts.xlsx_url),
        ]
    else:
        urls = [
            ("review.csv", artifacts.review_csv_url),
            ("review.xlsx", artifacts.review_xlsx_url),
            ("near-tie.csv", artifacts.near_tie_csv_url),
            ("near-tie.json", artifacts.near_tie_json_url),
            ("result.json", artifacts.result_json_url),
        ]

    overlay_urls = []
    if artifacts.overlay_url:
        overlay_urls.append(("overlay-page-001.png", artifacts.overlay_url))
    for page in job.result.pages:
        if page.overlay_url:
            overlay_urls.append((f"overlay-page-{page.page_index + 1:03d}.png", page.overlay_url))

    seen_paths: set[Path] = set()
    for file_name, url in [*urls, *overlay_urls]:
        if not url:
            continue
        path = resolve_url(url)
        if not path.exists() or path in seen_paths:
            continue
        seen_paths.add(path)
        yield path, file_name


def build_batch_export_zip(
    *,
    out_path: str | Path,
    batch_title: str,
    archive_name: str,
    jobs: list[DrawingJob],
    warnings: list[dict[str, Any]],
    mode: BatchExportMode,
    resolve_url,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_rows = _summary_manifest_rows(jobs)
    combined_rows = _combined_result_rows(jobs, mode=mode)
    combined_columns = ["job_id", "title", "drawing_name", "labels_name", *CSV_COLUMNS]

    batch_slug = sanitize_name(batch_title or Path(archive_name).stem)
    root_dir = f"{batch_slug}-{mode}"
    export_issues: list[dict[str, Any]] = []

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_csv_to_archive(
            archive,
            arcname=f"{root_dir}/manifest/jobs.csv",
            columns=[
                "job_id",
                "title",
                "status",
                "drawing_name",
                "labels_name",
                "document_confidence",
                "degraded_recognition",
                "emergency_fallback_used",
                "total_rows",
                "found_count",
                "missing_count",
                "uncertain_count",
                "held_back_count",
                "review_recommended",
                "failure_message",
            ],
            rows=manifest_rows,
        )
        _write_csv_to_archive(
            archive,
            arcname=f"{root_dir}/manifest/rows.csv",
            columns=combined_columns,
            rows=combined_rows,
        )
        archive.writestr(
            f"{root_dir}/manifest/warnings.json",
            json.dumps(warnings, ensure_ascii=False, indent=2).encode("utf-8"),
        )

        for job in jobs:
            job_dir = f"{root_dir}/jobs/{sanitize_name(job.title)}-{job.job_id[:8]}"
            archive.writestr(
                f"{job_dir}/job.json",
                json.dumps(job.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2).encode("utf-8"),
            )
            exported_any = False
            expected_urls = []
            artifacts = job.result.artifacts if job.result else None
            if artifacts:
                if mode == "production":
                    expected_urls.extend(
                        [
                            ("coordinates.csv", artifacts.csv_url),
                            ("coordinates.xlsx", artifacts.xlsx_url),
                        ]
                    )
                else:
                    expected_urls.extend(
                        [
                            ("review.csv", artifacts.review_csv_url),
                            ("review.xlsx", artifacts.review_xlsx_url),
                            ("result.json", artifacts.result_json_url),
                        ]
                    )

            for file_path, export_name in _iter_job_files(job, mode=mode, resolve_url=resolve_url):
                archive.write(file_path, arcname=f"{job_dir}/{export_name}")
                exported_any = True
            for expected_name, url in expected_urls:
                if not url:
                    export_issues.append(
                        {
                            "job_id": job.job_id,
                            "title": job.title,
                            "severity": "warning",
                            "code": "missing_artifact_url",
                            "file": expected_name,
                            "message": f"Job has no artifact URL for {expected_name}.",
                        }
                    )
                    continue
                resolved_path = resolve_url(url)
                if not resolved_path.exists():
                    export_issues.append(
                        {
                            "job_id": job.job_id,
                            "title": job.title,
                            "severity": "warning",
                            "code": "missing_artifact_file",
                            "file": expected_name,
                            "message": f"Artifact file is missing on disk: {resolved_path}",
                        }
                    )
            if not exported_any:
                export_issues.append(
                    {
                        "job_id": job.job_id,
                        "title": job.title,
                        "severity": "error",
                        "code": "empty_job_export",
                        "file": None,
                        "message": "No exportable files were included for this completed job.",
                    }
                )

        archive.writestr(
            f"{root_dir}/manifest/export-issues.json",
            json.dumps(export_issues, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    return out_path
