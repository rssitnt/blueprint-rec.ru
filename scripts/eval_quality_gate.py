from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.eval_no_table_filters import DEFAULT_BATCH_ROOT, DEFAULT_OUT_DIR, evaluate_case, iter_case_dirs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a simple quality gate over the current no-table filter corpus.")
    parser.add_argument("--batch-root", type=Path, default=DEFAULT_BATCH_ROOT)
    parser.add_argument("--match", type=str, default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-markers", type=int, default=5)
    parser.add_argument("--min-keep-ratio", type=float, default=0.90, help="Minimum acceptable kept/legacy ratio.")
    parser.add_argument("--max-held-back-ratio", type=float, default=0.12, help="Maximum acceptable held_back/legacy ratio.")
    parser.add_argument("--max-other-bucket-share", type=float, default=0.10, help="Maximum share of held-back rows allowed in the generic 'other' bucket.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    case_dirs = iter_case_dirs(args.batch_root, match=args.match, limit=args.limit)
    results = []
    for case_dir in case_dirs:
        result = evaluate_case(case_dir, held_back_limit=5)
        if result.total_legacy_markers < args.min_markers:
            continue
        results.append(result)

    if not results:
        raise RuntimeError("После фильтрации не осталось ни одного осмысленного кейса для quality gate.")

    total_legacy = sum(item.total_legacy_markers for item in results)
    total_kept = sum(item.kept_count for item in results)
    total_held_back = sum(item.held_back_count for item in results)
    total_other = sum(item.held_back_reason_buckets.get("other", 0) for item in results)
    keep_ratio = total_kept / total_legacy if total_legacy else 0.0
    held_back_ratio = total_held_back / total_legacy if total_legacy else 0.0
    other_bucket_share = total_other / total_held_back if total_held_back else 0.0

    checks = [
        {
            "name": "keep_ratio",
            "actual": round(keep_ratio, 4),
            "expected": f">= {args.min_keep_ratio:.4f}",
            "passed": keep_ratio >= args.min_keep_ratio,
        },
        {
            "name": "held_back_ratio",
            "actual": round(held_back_ratio, 4),
            "expected": f"<= {args.max_held_back_ratio:.4f}",
            "passed": held_back_ratio <= args.max_held_back_ratio,
        },
        {
            "name": "other_bucket_share",
            "actual": round(other_bucket_share, 4),
            "expected": f"<= {args.max_other_bucket_share:.4f}",
            "passed": other_bucket_share <= args.max_other_bucket_share,
        },
    ]
    passed = all(item["passed"] for item in checks)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "batch_root": str(args.batch_root),
        "cases": [item.case_name for item in results],
        "total_legacy_markers": total_legacy,
        "total_kept": total_kept,
        "total_held_back": total_held_back,
        "total_other_bucket": total_other,
        "keep_ratio": keep_ratio,
        "held_back_ratio": held_back_ratio,
        "other_bucket_share": other_bucket_share,
        "checks": checks,
        "passed": passed,
    }

    lines = [
        "# Quality Gate",
        "",
        f"Generated: {payload['generated_at']}",
        f"Batch root: {args.batch_root}",
        f"Cases used: {len(results)}",
        "",
        "## Aggregate",
        "",
        f"- Legacy markers: {total_legacy}",
        f"- Kept: {total_kept}",
        f"- Held back: {total_held_back}",
        f"- Keep ratio: {keep_ratio:.2%}",
        f"- Held-back ratio: {held_back_ratio:.2%}",
        f"- Other bucket share: {other_bucket_share:.2%}",
        "",
        "## Checks",
        "",
    ]
    for check in checks:
        marker = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- {check['name']}: {marker} (actual {check['actual']} vs {check['expected']})")
    lines.extend(["", f"Overall: {'PASS' if passed else 'FAIL'}", ""])

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = args.out_dir / f"quality_gate_{stamp}.md"
    json_path = args.out_dir / f"quality_gate_{stamp}.json"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Quality gate: {'PASS' if passed else 'FAIL'}")
    print(f"Markdown: {md_path}")
    print(f"JSON: {json_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
