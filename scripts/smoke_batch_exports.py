from __future__ import annotations

import asyncio
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.inference.app.api.jobs import batch_service, service
from services.inference.app.models.job_schemas import (
    BatchJobWarning,
    DrawingJob,
    DrawingJobInput,
    DrawingJobResult,
    DrawingJobStatus,
    DrawingJobSummary,
    DrawingResultPage,
    DrawingResultRow,
    DrawingResultRowStatus,
)
from services.inference.app.services.result_exports import write_json_payload, write_result_csv, write_result_xlsx


OUT_DIR = Path(r"C:/projects/sites/blueprint-rec-2/tmp/evals")


def _make_result(label: str, drawing_name: str, labels_name: str) -> DrawingJobResult:
    return DrawingJobResult(
        source_file=drawing_name,
        source_labels_file=labels_name,
        pages=[DrawingResultPage(page_index=0, width=1200, height=900, row_count=1, held_back_count=0)],
        rows=[
            DrawingResultRow(
                row=1,
                label=label,
                page_index=0,
                status=DrawingResultRowStatus.FOUND,
                final_score=0.93,
            )
        ],
        summary=DrawingJobSummary(total_rows=1, found_count=1, status_text="ok"),
    )


async def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = OUT_DIR / f"smoke_batch_exports_{stamp}.md"
    json_path = OUT_DIR / f"smoke_batch_exports_{stamp}.json"

    created_job_ids: list[str] = []
    created_batch_id: str | None = None

    try:
        jobs: list[DrawingJob] = []
        for idx in range(2):
            job_id = f"smoke-batch-{uuid4()}"
            created_job_ids.append(job_id)
            job_dir = service.jobs_root / job_id
            artifacts_dir = job_dir / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            drawing_name = f"drawing-{idx + 1}.png"
            labels_name = f"labels-{idx + 1}.csv"
            result = _make_result(str(100 + idx), drawing_name, labels_name)

            csv_path = write_result_csv(result, artifacts_dir / "result.csv")
            xlsx_path = write_result_xlsx(result, artifacts_dir / "result.xlsx")
            review_csv_path = write_result_csv(result, artifacts_dir / "review.csv")
            review_xlsx_path = write_result_xlsx(result, artifacts_dir / "review.xlsx", export_label="review")
            result_json_path = write_json_payload(result.model_dump(mode="json", by_alias=True), artifacts_dir / "result.json")
            overlay_path = artifacts_dir / "overlay-page-001.png"
            overlay_path.write_bytes(b"fake")

            result.artifacts.csv_url = service._storage_url_for(csv_path)
            result.artifacts.xlsx_url = service._storage_url_for(xlsx_path)
            result.artifacts.review_csv_url = service._storage_url_for(review_csv_path)
            result.artifacts.review_xlsx_url = service._storage_url_for(review_xlsx_path)
            result.artifacts.result_json_url = service._storage_url_for(result_json_path)
            result.artifacts.overlay_url = service._storage_url_for(overlay_path)
            result.pages[0].overlay_url = result.artifacts.overlay_url

            job = DrawingJob(
                job_id=job_id,
                title=f"Smoke Job {idx + 1}",
                status=DrawingJobStatus.COMPLETED,
                input=DrawingJobInput(
                    drawing_name=drawing_name,
                    drawing_url=f"/input/{idx + 1}",
                    labels_name=labels_name,
                    labels_url=f"/labels/{idx + 1}",
                    has_labels=True,
                ),
                result=result,
            )
            service._jobs[job_id] = job
            service._persist_job(job)
            jobs.append(job)

        created_batch_id = f"smoke-batch-{uuid4()}"
        await batch_service.create_batch(
            batch_id=created_batch_id,
            title="Smoke Batch",
            archive_name="smoke-batch.zip",
            title_prefix=None,
            job_ids=[job.job_id for job in jobs],
            warnings=[BatchJobWarning(code="ignored_file", message="ignored", file_name="note.txt", base_name="note")],
        )

        production_zip = await batch_service.build_export(created_batch_id, mode="production")
        review_zip = await batch_service.build_export(created_batch_id, mode="review")

        with zipfile.ZipFile(production_zip) as archive:
            production_names = sorted(archive.namelist())
        with zipfile.ZipFile(review_zip) as archive:
            review_names = sorted(archive.namelist())

        checks = [
            ("production_manifest_rows", any(name.endswith("/manifest/rows.csv") for name in production_names)),
            ("production_job_csv", any(name.endswith("/coordinates.csv") for name in production_names)),
            ("production_job_xlsx", any(name.endswith("/coordinates.xlsx") for name in production_names)),
            ("review_manifest_rows", any(name.endswith("/manifest/rows.csv") for name in review_names)),
            ("review_job_csv", any(name.endswith("/review.csv") for name in review_names)),
            ("review_result_json", any(name.endswith("/result.json") for name in review_names)),
        ]
        passed = all(check for _, check in checks)

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "production_zip": str(production_zip),
            "review_zip": str(review_zip),
            "production_count": len(production_names),
            "review_count": len(review_names),
            "checks": [{"name": name, "passed": ok} for name, ok in checks],
            "passed": passed,
        }

        md_lines = [
            "# Batch Export Smoke",
            "",
            f"Generated: {payload['generated_at']}",
            f"Production ZIP: {production_zip}",
            f"Review ZIP: {review_zip}",
            "",
            "## Checks",
            "",
        ]
        for name, ok in checks:
            md_lines.append(f"- {name}: {'PASS' if ok else 'FAIL'}")
        md_lines.extend(["", f"Overall: {'PASS' if passed else 'FAIL'}", ""])

        md_path.write_text("\n".join(md_lines), encoding="utf-8")
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"Markdown: {md_path}")
        print(f"JSON: {json_path}")
        return 0 if passed else 1
    finally:
        for job_id in created_job_ids:
            service._jobs.pop(job_id, None)
            shutil.rmtree(service.jobs_root / job_id, ignore_errors=True)
        if created_batch_id:
            batch_service._batches.pop(created_batch_id, None)
            shutil.rmtree(batch_service.batches_root / created_batch_id, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
