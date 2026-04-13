from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.inference.app.models.job_schemas import DrawingJob, DrawingJobInput
from services.inference.app.services.job_runner import JobPageArtifact, build_result_from_legacy_output


DEFAULT_BATCH_ROOT = Path(r"C:/projects/sites/blueprint-rec-2/output/current/batch")
DEFAULT_OUT_DIR = Path(r"C:/projects/sites/blueprint-rec-2/tmp/evals")


@dataclass(frozen=True)
class EvalCaseResult:
    case_name: str
    markers_path: Path
    total_legacy_markers: int
    kept_count: int
    held_back_count: int
    confidence: float
    kept_statuses: Counter[str]
    held_back_reason_buckets: Counter[str]
    kept_source_kinds: Counter[str]
    held_back_examples: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate current no-table filters on legacy markers_v3 corpora.")
    parser.add_argument(
        "--batch-root",
        type=Path,
        default=DEFAULT_BATCH_ROOT,
        help="Root directory that contains legacy batch run folders with markers_v3.json files.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit on number of folders to evaluate. 0 means all.",
    )
    parser.add_argument(
        "--match",
        type=str,
        default="",
        help="Optional substring filter for folder names.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Directory where the markdown/json reports will be written.",
    )
    parser.add_argument(
        "--min-markers",
        type=int,
        default=0,
        help="Skip cases with fewer than this many legacy final markers.",
    )
    parser.add_argument(
        "--held-back-limit",
        type=int,
        default=8,
        help="How many held-back labels/examples to include per case in the report.",
    )
    return parser.parse_args()


def iter_case_dirs(batch_root: Path, *, match: str = "", limit: int = 0) -> list[Path]:
    if not batch_root.is_dir():
        raise RuntimeError(f"Batch root not found: {batch_root}")
    case_dirs = []
    for child in sorted(batch_root.iterdir()):
        if not child.is_dir():
            continue
        if match and match.lower() not in child.name.lower():
            continue
        if not (child / "markers_v3.json").is_file():
            continue
        case_dirs.append(child)
    if limit > 0:
        case_dirs = case_dirs[:limit]
    return case_dirs


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def get_page_size(case_dir: Path, payload: Mapping[str, Any]) -> tuple[int, int]:
    source_size = payload.get("source_size")
    if isinstance(source_size, Mapping):
        width = int(source_size.get("width") or 0)
        height = int(source_size.get("height") or 0)
        if width > 0 and height > 0:
            return width, height

    overlay_path = case_dir / "markers_v3.overlay.png"
    if overlay_path.is_file():
        with Image.open(overlay_path) as image:
            return image.size

    raise RuntimeError(f"Could not determine page size for {case_dir}")


def make_job(case_name: str) -> DrawingJob:
    return DrawingJob(
        title=case_name,
        input=DrawingJobInput(
            drawing_name=f"{case_name}.png",
            drawing_url=f"/legacy/{case_name}.png",
            labels_name=None,
            labels_url=None,
            has_labels=False,
        ),
    )


def classify_held_back_reason(note: str | None) -> str:
    text = (note or "").lower()
    if "края листа" in text or "служебная цифра" in text:
        return "edge_furniture"
    if "слишком разреженный" in text or "слишком мелкий" in text:
        return "sparse_text"
    if "gemini по контексту считает" in text or "служебную область" in text:
        return "context_reject"
    if "two distinct part labels" in text or "rather than a single part number" in text:
        return "multi_label_ambiguity"
    if "no legible part number text" in text or "not a valid part number" in text:
        return "non_text_or_invalid_label"
    if "no visible part number text" in text or "mechanical parts but no visible part number text" in text:
        return "no_visible_number"
    if "только на ocr" in text or "ocr-only" in text:
        return "ocr_only_low_confidence"
    return "other"


def summarize_held_back_row(label: str, note: str | None) -> str:
    reason_bucket = classify_held_back_reason(note)
    compact_note = " ".join((note or "").split())
    if len(compact_note) > 180:
        compact_note = compact_note[:177].rstrip() + "..."
    if compact_note:
        return f"{label} [{reason_bucket}] - {compact_note}"
    return f"{label} [{reason_bucket}]"


def evaluate_case(case_dir: Path, *, held_back_limit: int = 8) -> EvalCaseResult:
    markers_path = case_dir / "markers_v3.json"
    payload = load_json(markers_path)
    width, height = get_page_size(case_dir, payload)
    markers = payload.get("markers", [])
    if not isinstance(markers, list):
        raise RuntimeError(f"Unexpected markers payload in {markers_path}")

    job = make_job(case_dir.name)
    page_artifacts = [
        JobPageArtifact(
            page_index=0,
            overlay_path=case_dir / "markers_v3.overlay.png" if (case_dir / "markers_v3.overlay.png").is_file() else None,
            source_json_path=markers_path,
            width=width,
            height=height,
        )
    ]
    result = build_result_from_legacy_output(
        job=job,
        page_artifacts=page_artifacts,
        page_payloads=[payload],
        expected_labels=[],
    )

    kept_statuses = Counter(str(row.status.value) for row in result.rows)
    kept_source_kinds = Counter((row.source_kind or "none") for row in result.rows)
    held_back_reason_buckets = Counter(classify_held_back_reason(row.note) for row in result.held_back_rows)
    held_back_examples = [
        summarize_held_back_row(row.label, row.note)
        for row in result.held_back_rows[: max(0, held_back_limit)]
    ]
    return EvalCaseResult(
        case_name=case_dir.name,
        markers_path=markers_path,
        total_legacy_markers=len(markers),
        kept_count=len(result.rows),
        held_back_count=len(result.held_back_rows),
        confidence=float(result.summary.document_confidence or 0.0),
        kept_statuses=kept_statuses,
        held_back_reason_buckets=held_back_reason_buckets,
        kept_source_kinds=kept_source_kinds,
        held_back_examples=held_back_examples,
    )


def format_counter(counter: Counter[str]) -> str:
    if not counter:
        return "-"
    return ", ".join(f"{key}: {value}" for key, value in counter.most_common())


def build_markdown_report(results: list[EvalCaseResult], *, batch_root: Path) -> str:
    total_legacy = sum(item.total_legacy_markers for item in results)
    total_kept = sum(item.kept_count for item in results)
    total_held_back = sum(item.held_back_count for item in results)
    overall_statuses: Counter[str] = Counter()
    overall_reasons: Counter[str] = Counter()
    overall_sources: Counter[str] = Counter()
    for item in results:
        overall_statuses.update(item.kept_statuses)
        overall_reasons.update(item.held_back_reason_buckets)
        overall_sources.update(item.kept_source_kinds)

    lines = [
        "# No-table Filter Eval",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Batch root: {batch_root}",
        f"Cases: {len(results)}",
        "",
        "## Aggregate",
        "",
        f"- Legacy final markers: {total_legacy}",
        f"- Kept by current no-table filter: {total_kept}",
        f"- Held back by current no-table filter: {total_held_back}",
        f"- Keep ratio: {(total_kept / total_legacy * 100):.1f}%\"".replace('"', "") if total_legacy else "- Keep ratio: -",
        f"- Held-back ratio: {(total_held_back / total_legacy * 100):.1f}%\"".replace('"', "") if total_legacy else "- Held-back ratio: -",
        f"- Kept statuses: {format_counter(overall_statuses)}",
        f"- Held-back buckets: {format_counter(overall_reasons)}",
        f"- Kept source kinds: {format_counter(overall_sources)}",
        "",
        "## Cases",
        "",
    ]

    for item in results:
        lines.extend(
            [
                f"### {item.case_name}",
                f"- Path: {item.markers_path}",
                f"- Legacy markers: {item.total_legacy_markers}",
                f"- Kept: {item.kept_count}",
                f"- Held back: {item.held_back_count}",
                f"- Document confidence: {item.confidence:.3f}",
                f"- Kept statuses: {format_counter(item.kept_statuses)}",
                f"- Held-back buckets: {format_counter(item.held_back_reason_buckets)}",
                f"- Kept source kinds: {format_counter(item.kept_source_kinds)}",
                f"- Held-back examples: {' | '.join(item.held_back_examples) if item.held_back_examples else '-'}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def write_reports(results: list[EvalCaseResult], *, out_dir: Path, batch_root: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    markdown_path = out_dir / f"no_table_filter_eval_{stamp}.md"
    json_path = out_dir / f"no_table_filter_eval_{stamp}.json"
    markdown_path.write_text(build_markdown_report(results, batch_root=batch_root), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            [
                {
                    "case_name": item.case_name,
                    "markers_path": str(item.markers_path),
                    "total_legacy_markers": item.total_legacy_markers,
                    "kept_count": item.kept_count,
                    "held_back_count": item.held_back_count,
                    "confidence": item.confidence,
                    "kept_statuses": dict(item.kept_statuses),
                    "held_back_reason_buckets": dict(item.held_back_reason_buckets),
                    "kept_source_kinds": dict(item.kept_source_kinds),
                    "held_back_examples": item.held_back_examples,
                }
                for item in results
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return markdown_path, json_path


def main() -> int:
    args = parse_args()
    case_dirs = iter_case_dirs(args.batch_root, match=args.match, limit=args.limit)
    if not case_dirs:
        raise RuntimeError("No markers_v3.json case directories matched the given filters.")

    results = [
        evaluate_case(case_dir, held_back_limit=args.held_back_limit)
        for case_dir in case_dirs
    ]
    if args.min_markers > 0:
        results = [item for item in results if item.total_legacy_markers >= args.min_markers]
    if not results:
        raise RuntimeError("All matched cases were filtered out by --min-markers.")
    markdown_path, json_path = write_reports(results, out_dir=args.out_dir, batch_root=args.batch_root)
    print(f"Markdown report: {markdown_path}")
    print(f"JSON report: {json_path}")
    print(f"Cases: {len(results)}")
    print(f"Legacy markers: {sum(item.total_legacy_markers for item in results)}")
    print(f"Kept: {sum(item.kept_count for item in results)}")
    print(f"Held back: {sum(item.held_back_count for item in results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
