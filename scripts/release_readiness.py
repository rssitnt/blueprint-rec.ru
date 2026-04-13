from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "tmp" / "evals"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a consolidated release-readiness report for blueprint-rec-2.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-quality-gate", action="store_true")
    parser.add_argument("--skip-batch-smoke", action="store_true")
    parser.add_argument("--skip-fallback-smoke", action="store_true")
    parser.add_argument("--skip-ui-smoke", action="store_true")
    return parser.parse_args()


def run_step(name: str, command: list[str], *, cwd: Path) -> dict:
    effective_command = command
    if os.name == "nt" and command and command[0].lower() == "npm":
        effective_command = ["cmd", "/c", *command]
    completed = subprocess.run(
        effective_command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return {
        "name": name,
        "command": effective_command,
        "returncode": completed.returncode,
        "passed": completed.returncode == 0,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def find_latest_degraded_candidate() -> Path | None:
    candidates = [
        Path(r"C:/projects/sites/blueprint-rec-2/services/inference/var/00fc0445-b0eb-46a8-9732-caa7dac16171/20260330173016527456-page4.png"),
        Path(r"C:/projects/sites/blueprint-rec-2/services/inference/var/012acec8-d898-4d85-9bd6-0357446c57b6/20260409183248036625-test1.jpg"),
        Path(r"C:/projects/sites/blueprint-rec-2/services/inference/var/0091d38e-42a7-4708-9169-306d8db714f1/20260409182930067848-image001.png"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = args.out_dir / f"release_readiness_{stamp}.md"
    json_path = args.out_dir / f"release_readiness_{stamp}.json"

    steps: list[dict] = []

    if not args.skip_build:
        steps.append(
            run_step(
                "python_compileall",
                [sys.executable, "-m", "compileall", str(REPO_ROOT / "services" / "inference" / "app")],
                cwd=REPO_ROOT,
            )
        )
        steps.append(
            run_step(
                "npm_build_web",
                ["npm", "run", "build:web"],
                cwd=REPO_ROOT,
            )
        )
        steps.append(
            run_step(
                "backend_jobs_tests",
                ["cmd", "/c", "npm", "run", "test:inference:jobs"],
                cwd=REPO_ROOT,
            )
        )

    if not args.skip_quality_gate:
        steps.append(
            run_step(
                "quality_gate",
                [sys.executable, str(REPO_ROOT / "scripts" / "eval_quality_gate.py")],
                cwd=REPO_ROOT,
            )
        )

    if not args.skip_batch_smoke:
        steps.append(
            run_step(
                "batch_export_smoke",
                [sys.executable, str(REPO_ROOT / "scripts" / "smoke_batch_exports.py")],
                cwd=REPO_ROOT,
            )
        )

    if not args.skip_ui_smoke:
        steps.append(
            run_step(
                "live_smoke",
                ["cmd", "/c", "npm", "run", "test:live-smoke"],
                cwd=REPO_ROOT,
            )
        )
        steps.append(
            run_step(
                "headless_manual_qa",
                ["cmd", "/c", "npm", "run", "test:headless-manual-qa"],
                cwd=REPO_ROOT,
            )
        )

    degraded_candidate = find_latest_degraded_candidate()
    if not args.skip_fallback_smoke and degraded_candidate is not None:
        steps.append(
            run_step(
                "fallback_live_smoke",
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "smoke_live_job_fallback.py"),
                    "--drawing",
                    str(degraded_candidate),
                    "--pipeline-timeout",
                    "90",
                    "--fallback-timeout",
                    "90",
                    "--emergency-timeout",
                    "90",
                ],
                cwd=REPO_ROOT,
            )
        )
    elif not args.skip_fallback_smoke:
        steps.append(
            {
                "name": "fallback_live_smoke",
                "command": [],
                "returncode": 0,
                "passed": True,
                "stdout": "Skipped: no known degraded candidate drawing found.",
                "stderr": "",
            }
        )

    passed = all(step["passed"] for step in steps)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": [
            {
                "name": step["name"],
                "passed": step["passed"],
                "returncode": step["returncode"],
            }
            for step in steps
        ],
        "passed": passed,
    }

    md_lines = [
        "# Release Readiness",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Steps",
        "",
    ]
    for step in steps:
        md_lines.append(f"- {step['name']}: {'PASS' if step['passed'] else 'FAIL'}")
        if step["stdout"].strip():
            first_line = step["stdout"].strip().splitlines()[0]
            md_lines.append(f"  - {first_line}")
        if step["stderr"].strip():
            first_err = step["stderr"].strip().splitlines()[0]
            md_lines.append(f"  - stderr: {first_err}")
    md_lines.extend(["", f"Overall: {'PASS' if passed else 'FAIL'}", ""])

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Markdown: {md_path}")
    print(f"JSON: {json_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
