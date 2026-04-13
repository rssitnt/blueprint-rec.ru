from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.inference.app.models.job_schemas import DrawingJob, DrawingJobInput
from services.inference.app.core.config import settings
from services.inference.app.services.job_runner import (
    _fallback_ocr_engines,
    _fallback_cli_args,
    _normalize_ocr_engine,
    _should_replace_primary_result,
    build_result_from_legacy_output,
    prepare_drawing_for_legacy_pipeline,
    prepare_labels_for_legacy_pipeline,
    run_legacy_pipeline,
    JobPageArtifact,
)


DEFAULT_OUT_DIR = Path(r"C:/projects/sites/blueprint-rec-2/tmp/evals")


@dataclass(frozen=True)
class EngineEval:
    engine: str
    result: Any
    page_artifacts: list[JobPageArtifact]
    source_payloads: list[dict[str, Any]]
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run primary/fallback OCR comparison on one drawing.")
    parser.add_argument("--drawing", type=Path, required=True, help="Path to source drawing image/PDF.")
    parser.add_argument("--labels", type=Path, default=None, help="Optional labels table.")
    parser.add_argument("--title", type=str, default="", help="Optional report title.")
    parser.add_argument("--primary-engine", type=str, default="both", help="Primary OCR engine: both/easy/rapid.")
    parser.add_argument(
        "--engines",
        type=str,
        default="",
        help="Optional comma-separated OCR engines to run in order, e.g. both,rapid. First one is treated as primary.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Optional max number of prepared pages to evaluate. 0 means all pages.",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Where to write markdown/json reports.")
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


def _run_engine_eval(
    *,
    job: DrawingJob,
    drawing: Path,
    labels: Path | None,
    engine: str,
    primary_engine: str,
    max_pages: int = 0,
    use_fallback_profile: bool = False,
) -> EngineEval:
    try:
        with tempfile.TemporaryDirectory(prefix=f"fallback_eval_{engine}_") as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            work_dir = temp_dir / "work"
            pipeline_dir = temp_dir / "pipeline"
            logs_dir = temp_dir / "logs"
            artifacts_dir = temp_dir / "artifacts"
            work_dir.mkdir(parents=True, exist_ok=True)
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            prepared_pages = prepare_drawing_for_legacy_pipeline(drawing, work_dir / "prepared-drawing.png")
            if max_pages > 0:
                prepared_pages = prepared_pages[:max_pages]
            labels_xlsx_path, expected_labels = prepare_labels_for_legacy_pipeline(labels, work_dir)

            page_payloads: list[dict[str, Any]] = []
            page_artifacts: list[JobPageArtifact] = []
            for prepared_page in prepared_pages:
                page_slug = f"page-{prepared_page.page_index + 1:03d}"
                legacy_output = run_legacy_pipeline(
                    image_path=prepared_page.raster_path,
                    labels_xlsx_path=labels_xlsx_path,
                    out_dir=pipeline_dir / page_slug,
                    log_dir=logs_dir / page_slug,
                    ocr_engine=engine,
                    timeout_seconds=(
                        settings.legacy_fallback_pipeline_timeout_seconds
                        if use_fallback_profile
                        else settings.legacy_pipeline_timeout_seconds
                    ),
                    extra_cli_args=_fallback_cli_args() if use_fallback_profile else None,
                )
                page_payloads.append(legacy_output.payload)
                page_artifacts.append(
                    JobPageArtifact(
                        page_index=prepared_page.page_index,
                        overlay_path=legacy_output.overlay_path,
                        source_json_path=legacy_output.markers_json_path,
                        width=prepared_page.width,
                        height=prepared_page.height,
                    )
                )

            result = build_result_from_legacy_output(
                job=job,
                page_artifacts=page_artifacts,
                page_payloads=page_payloads,
                expected_labels=expected_labels,
                selected_ocr_engine=engine,
                fallback_used=use_fallback_profile,
            )
            return EngineEval(
                engine=engine,
                result=result,
                page_artifacts=page_artifacts,
                source_payloads=page_payloads,
            )
    except Exception as exc:
        return EngineEval(
            engine=engine,
            result=None,
            page_artifacts=[],
            source_payloads=[],
            error=str(exc),
        )


def _select_best(primary: EngineEval, fallbacks: list[EngineEval]) -> EngineEval:
    selected = primary
    for fallback in fallbacks:
        if fallback.error or fallback.result is None:
            continue
        if selected.result is None:
            selected = fallback
            continue
        if _should_replace_primary_result(selected.result, fallback.result):
            selected = fallback
    return selected


def _parse_engines(raw: str, primary_engine: str) -> list[str]:
    if not raw.strip():
        return [primary_engine, *_fallback_ocr_engines(primary_engine)]

    ordered: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        engine = _normalize_ocr_engine(part)
        if engine in seen:
            continue
        seen.add(engine)
        ordered.append(engine)
    return ordered or [primary_engine, *_fallback_ocr_engines(primary_engine)]


def _build_markdown_report(
    *,
    drawing: Path,
    labels: Path | None,
    primary_engine: str,
    evaluations: list[EngineEval],
    selected: EngineEval,
) -> str:
    lines = [
        "# Degraded OCR Fallback Eval",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Drawing: {drawing}",
        f"Labels: {labels if labels else '-'}",
        f"Primary engine: {primary_engine}",
        f"Selected engine: {selected.engine}",
        "",
        "## Engines",
        "",
    ]

    for evaluation in evaluations:
        if evaluation.error or evaluation.result is None:
            lines.extend(
                [
                    f"### {evaluation.engine}",
                    f"- error: {evaluation.error}",
                    "",
                ]
            )
            continue
        summary = evaluation.result.summary
        lines.extend(
            [
                f"### {evaluation.engine}",
                f"- found: {summary.found_count}",
                f"- uncertain: {summary.uncertain_count}",
                f"- held_back: {summary.held_back_count}",
                f"- discarded: {summary.discarded_count}",
                f"- confidence: {summary.document_confidence}",
                f"- degraded: {'yes' if summary.degraded_recognition else 'no'}",
                f"- degraded_reason: {summary.degraded_reason or '-'}",
                f"- status: {summary.status_text}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    args = parse_args()
    drawing = args.drawing.resolve()
    labels = args.labels.resolve() if args.labels else None
    if not drawing.is_file():
        raise RuntimeError(f"Drawing not found: {drawing}")
    if labels is not None and not labels.is_file():
        raise RuntimeError(f"Labels file not found: {labels}")

    primary_engine = _normalize_ocr_engine(args.primary_engine)
    engines_to_run = _parse_engines(args.engines, primary_engine)
    primary_engine = engines_to_run[0]
    job = _make_job(drawing, labels, args.title)

    evaluations: list[EngineEval] = []
    primary_eval = _run_engine_eval(
        job=job,
        drawing=drawing,
        labels=labels,
        engine=primary_engine,
        primary_engine=primary_engine,
        max_pages=args.max_pages,
        use_fallback_profile=False,
    )
    evaluations.append(primary_eval)

    fallback_evals: list[EngineEval] = []
    for engine in engines_to_run[1:]:
        fallback_eval = _run_engine_eval(
            job=job,
            drawing=drawing,
            labels=labels,
            engine=engine,
            primary_engine=primary_engine,
            max_pages=args.max_pages,
            use_fallback_profile=True,
        )
        fallback_evals.append(fallback_eval)
        evaluations.append(fallback_eval)

    selected = _select_best(primary_eval, fallback_evals)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = drawing.stem.replace(" ", "_")
    markdown_path = args.out_dir / f"degraded_fallback_eval_{stem}_{stamp}.md"
    json_path = args.out_dir / f"degraded_fallback_eval_{stem}_{stamp}.json"

    markdown_path.write_text(
        _build_markdown_report(
            drawing=drawing,
            labels=labels,
            primary_engine=primary_engine,
            evaluations=evaluations,
            selected=selected,
        ),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(
            {
                "drawing": str(drawing),
                "labels": str(labels) if labels else None,
                "primary_engine": primary_engine,
                "selected_engine": selected.engine,
                "engines": [
                    {
                        "engine": evaluation.engine,
                        "summary": evaluation.result.summary.model_dump(mode="json", by_alias=True) if evaluation.result is not None else None,
                        "error": evaluation.error,
                    }
                    for evaluation in evaluations
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Markdown report: {markdown_path}")
    print(f"JSON report: {json_path}")
    print(f"Selected engine: {selected.engine}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
