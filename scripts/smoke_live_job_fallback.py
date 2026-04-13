from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.inference.app.core.config import settings
from services.inference.app.models.job_schemas import DrawingJob, DrawingJobInput
from services.inference.app.services.job_runner import run_job_pipeline


DEFAULT_OUT_DIR = Path(r"C:/projects/sites/blueprint-rec-2/tmp/evals")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the real job pipeline on one drawing and capture fallback summary.")
    parser.add_argument("--drawing", type=Path, required=True, help="Path to the drawing image/PDF.")
    parser.add_argument("--labels", type=Path, default=None, help="Optional labels table.")
    parser.add_argument("--title", type=str, default="", help="Optional job title.")
    parser.add_argument("--ocr-engine", type=str, default="both", help="Primary OCR engine for the live run.")
    parser.add_argument("--pipeline-timeout", type=int, default=90, help="Primary pipeline timeout in seconds.")
    parser.add_argument("--fallback-timeout", type=int, default=90, help="Safe fallback timeout in seconds.")
    parser.add_argument("--emergency-timeout", type=int, default=90, help="Emergency fallback timeout in seconds.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Where to write reports.")
    parser.add_argument(
        "--preserve-job-dir",
        type=Path,
        default=None,
        help="Optional directory where the temporary live job folder should be copied after the smoke run.",
    )
    return parser.parse_args()


def _make_job(drawing: Path, labels: Path | None, title: str) -> DrawingJob:
    return DrawingJob(
        title=title or drawing.stem,
        input=DrawingJobInput(
            drawing_name=drawing.name,
            drawing_url=str(drawing),
            labels_name=labels.name if labels else None,
            labels_url=str(labels) if labels else None,
            has_labels=labels is not None,
        ),
    )


@contextmanager
def _temporary_settings_overrides(
    *,
    pipeline_timeout: int,
    fallback_timeout: int,
    emergency_timeout: int,
) -> Iterator[None]:
    previous = {
        "legacy_pipeline_timeout_seconds": settings.legacy_pipeline_timeout_seconds,
        "legacy_fallback_pipeline_timeout_seconds": settings.legacy_fallback_pipeline_timeout_seconds,
        "legacy_emergency_fallback_pipeline_timeout_seconds": settings.legacy_emergency_fallback_pipeline_timeout_seconds,
    }
    settings.legacy_pipeline_timeout_seconds = pipeline_timeout
    settings.legacy_fallback_pipeline_timeout_seconds = fallback_timeout
    settings.legacy_emergency_fallback_pipeline_timeout_seconds = emergency_timeout
    try:
        yield
    finally:
        for key, value in previous.items():
            setattr(settings, key, value)


def _copy_input_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _build_markdown_report(
    *,
    drawing: Path,
    labels: Path | None,
    output_dir: Path,
    report_path: Path,
    output,
    log_files: list[Path],
    pipeline_timeout: int,
    fallback_timeout: int,
    emergency_timeout: int,
    ocr_engine: str,
) -> str:
    summary = output.result.summary
    lines = [
        "# Live Job Fallback Smoke",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Drawing: {drawing}",
        f"Labels: {labels if labels else '-'}",
        f"Primary OCR engine: {ocr_engine}",
        f"Primary timeout: {pipeline_timeout}s",
        f"Safe fallback timeout: {fallback_timeout}s",
        f"Emergency timeout: {emergency_timeout}s",
        f"Working directory: {output_dir}",
        "",
        "## Summary",
        "",
        f"- selected_ocr_engine: {summary.selected_ocr_engine or '-'}",
        f"- fallback_used: {'yes' if summary.fallback_used else 'no'}",
        f"- fallback_attempted: {'yes' if summary.fallback_attempted else 'no'}",
        f"- fallback_failure_count: {summary.fallback_failure_count}",
        f"- degraded_recognition: {'yes' if summary.degraded_recognition else 'no'}",
        f"- degraded_reason: {summary.degraded_reason or '-'}",
        f"- found_count: {summary.found_count}",
        f"- uncertain_count: {summary.uncertain_count}",
        f"- held_back_count: {summary.held_back_count}",
        f"- discarded_count: {summary.discarded_count}",
        f"- document_confidence: {summary.document_confidence}",
        f"- failure_message: {summary.failure_message or '-'}",
        f"- status_text: {summary.status_text}",
        "",
        "## Review Reasons",
        "",
    ]
    if summary.review_reasons:
        lines.extend(f"- {reason}" for reason in summary.review_reasons)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- production_csv: {output.production_csv_path}",
            f"- review_csv: {output.review_csv_path}",
            f"- review_zip: {output.review_zip_path}",
            f"- result_json: {output.result_json_path}",
            f"- source_json: {output.source_json_path or '-'}",
            "",
            "## Fallback Logs",
            "",
        ]
    )
    if log_files:
        lines.extend(f"- {path}" for path in log_files)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Pages",
            "",
        ]
    )
    if output.result.pages:
        for page in output.result.pages:
            lines.extend(
                [
                    f"### Page {page.page_index + 1}",
                    f"- width: {page.width}",
                    f"- height: {page.height}",
                    f"- rows: {page.row_count}",
                    f"- held_back: {page.held_back_count}",
                    f"- overlay: {page.overlay_url or '-'}",
                    "",
                ]
            )
    else:
        lines.append("- none")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    args = parse_args()
    drawing = args.drawing.resolve()
    labels = args.labels.resolve() if args.labels else None
    if not drawing.is_file():
        raise RuntimeError(f"Drawing not found: {drawing}")
    if labels is not None and not labels.is_file():
        raise RuntimeError(f"Labels file not found: {labels}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = drawing.stem.replace(" ", "_")
    report_path = args.out_dir / f"live_job_fallback_smoke_{stem}_{stamp}.md"
    json_path = args.out_dir / f"live_job_fallback_smoke_{stem}_{stamp}.json"

    preserved_job_dir: Path | None = None
    with tempfile.TemporaryDirectory(prefix="live_job_fallback_") as temp_dir_raw:
        job_dir = Path(temp_dir_raw) / "job"
        input_dir = job_dir / "input"
        _copy_input_file(drawing, input_dir / f"drawing{drawing.suffix.lower()}")
        if labels is not None:
            _copy_input_file(labels, input_dir / f"labels{labels.suffix.lower()}")

        job = _make_job(drawing, labels, args.title)

        original_env_engine = None
        try:
            original_env_engine = os.environ.get("WEBUI_OCR_ENGINE")
            os.environ["WEBUI_OCR_ENGINE"] = args.ocr_engine
            with _temporary_settings_overrides(
                pipeline_timeout=args.pipeline_timeout,
                fallback_timeout=args.fallback_timeout,
                emergency_timeout=args.emergency_timeout,
            ):
                output = run_job_pipeline(job_dir, job)
        finally:
            if original_env_engine is None:
                os.environ.pop("WEBUI_OCR_ENGINE", None)
            else:
                os.environ["WEBUI_OCR_ENGINE"] = original_env_engine

        log_files = sorted((job_dir / "logs").rglob("*.txt"))
        report_path.write_text(
            _build_markdown_report(
                drawing=drawing,
                labels=labels,
                output_dir=job_dir,
                report_path=report_path,
                output=output,
                log_files=log_files,
                pipeline_timeout=args.pipeline_timeout,
                fallback_timeout=args.fallback_timeout,
                emergency_timeout=args.emergency_timeout,
                ocr_engine=args.ocr_engine,
            ),
            encoding="utf-8",
        )
        json_path.write_text(
            json.dumps(
                {
                    "drawing": str(drawing),
                    "labels": str(labels) if labels else None,
                    "ocr_engine": args.ocr_engine,
                    "pipeline_timeout": args.pipeline_timeout,
                    "fallback_timeout": args.fallback_timeout,
                    "emergency_timeout": args.emergency_timeout,
                    "summary": output.result.summary.model_dump(mode="json", by_alias=True),
                    "artifacts": {
                        "production_csv": str(output.production_csv_path),
                        "review_csv": str(output.review_csv_path),
                        "review_zip": str(output.review_zip_path),
                        "result_json": str(output.result_json_path),
                        "source_json": str(output.source_json_path) if output.source_json_path else None,
                    },
                    "log_files": [str(path) for path in log_files],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if args.preserve_job_dir is not None:
            preserved_job_dir = args.preserve_job_dir.resolve() / f"{stem}_{stamp}"
            if preserved_job_dir.exists():
                shutil.rmtree(preserved_job_dir)
            preserved_job_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(job_dir, preserved_job_dir)

    print(f"Markdown report: {report_path}")
    print(f"JSON report: {json_path}")
    if preserved_job_dir is not None:
        print(f"Preserved job dir: {preserved_job_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
