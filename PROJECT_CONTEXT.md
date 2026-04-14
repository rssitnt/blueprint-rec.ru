# Project Context

Last updated: 2026-04-13

## Current product state

- Main working tree:
  - `C:/projects/sites/blueprint-rec-2`
- Extra local tree removed:
  - `C:/projects/sites/blueprint-rec`
- Public domain:
  - `https://blueprint-rec.ru`
- Local frontend:
  - `http://localhost:3010`
- Local backend:
  - `http://127.0.0.1:8010`

## User constraints

- Never open visible browser windows on the user's screen.
- Any browser automation must stay headless / hidden / offscreen.
- Always keep compact context markdown files up to date.
- Use full absolute Windows paths in user-facing responses.
- User expects changes for this project to be pushed by default after edits.
- Startup script now points to `C:/projects/sites/blueprint-rec-2/scripts/run_webui_public_tunnel.ps1` to avoid VBS errors after cleanup.

## Product shape

- Main flow is job-first:
  - upload drawing or archive
  - optional labels table
  - background processing
  - result preview / exports
- Frontend:
  - `C:/projects/sites/blueprint-rec-2/apps/web`
  - main screen is `job-home`
  - legacy session workspace is kept as preview/manual-correction surface
- Backend:
  - `C:/projects/sites/blueprint-rec-2/services/inference`
  - FastAPI
  - in-memory job store
  - in-memory batch store
  - built-in fallback pipeline via `InMemorySessionStore`

## Recognition direction

- Recognition strategy is now:
  - `Gemini-first truth + local localization`
- PaddleOCR is integrated as local detector/readout helper.
- Heavy exploded-view sheets have a dedicated tiny-callout fallback path.
- Vocabulary extraction no longer slices drawings into tiles.
- Vocabulary prompt no longer asks for only "clearly" visible labels; it asks for all visible callout labels on the full drawing.
- On dense benchmark `test1.jpg`, direct `gemini-2.5-flash` request failed, but OpenRouter Gemini 3.1 models proved the truth-extraction idea:
  - `google/gemini-3.1-pro-preview`
    - returned full label set `1..42` with `29A`, `29B`, `30A`
  - `google/gemini-3.1-flash-image-preview`
    - returned near-full list with duplicates/repeats
- Main remaining recognition gap:
  - dense exploded-view sheets like `test1.jpg`
  - truth-guided localization of small missing labels is still incomplete
  - next obvious upgrade path is wiring stronger Gemini 3.1 truth extraction into the backend, not relying on the current direct 2.5 vocab call
  - after switching the vocab step to full-image Gemini 3.1 Pro, the vocab step itself now returns 43 labels on `test1.jpg`, but the final pipeline still lands at 25 unique labels, so the bottleneck moved clearly to localization / candidate generation / candidate-to-truth merge
  - missing-label localization is now partially truth-driven:
    - recovery passes use the missing Gemini truth-set as `allowed_labels`
    - low-res missing-label VLM recovery now works for numeric labels too, not only suffix / compound labels
    - on `test1.jpg` this raised the final unique labels from `25` to `27`
    - newly recovered labels include `21`, `29A`, `29B`
    - still missing after the latest pass: `4, 5, 6, 8, 10, 11, 13, 14, 15, 17, 20, 24, 31, 37, 38, 42`

## Important backend fixes already present

- Missing external legacy script no longer blocks job execution.
  - file:
    - `C:/projects/sites/blueprint-rec-2/services/inference/app/services/job_runner.py`
- Built-in fallback with labels now matches base table labels to drawing subpositions.
  - example:
    - table `14`
    - drawing `14-4(1)`
    - now matches correctly
- No-table built-in fallback no longer drops good markers because of enum-like statuses such as `MarkerStatus.AI_DETECTED`.
- Old completed broken jobs auto-repair on `GET /api/jobs/{id}`.
- Old no-table jobs auto-repair from `/api/jobs` list too.
- Old failed jobs with legacy-pipeline error auto-repair on open/list.
- Old stale jobs now also auto-repair automatically on backend startup.
  - files:
    - `C:/projects/sites/blueprint-rec-2/services/inference/app/services/job_store.py`
    - `C:/projects/sites/blueprint-rec-2/services/inference/app/main.py`
- Dense-sheet recovery now keeps same-label neighbors and expands low-res recovery search.
  - files:
    - `C:/projects/sites/blueprint-rec-2/services/inference/app/services/session_store.py`

## Important batch fixes already present

- Missing jobs in old batches are counted as failed instead of leaving the batch forever unfinished.
- Orphan batches are hidden from batch list output.
- Direct open for orphan batch ids returns `404`.
- Batch exports work and return zip archives.

## Important frontend / UX state

- Home screen simplified and moved to warm dark theme.
- Home screen uses a two-column architecture:
  - Left: compact tabbed lists (jobs / batches) with internal scroll.
  - Right: sticky detail panel for the active job or batch, keeping exports/actions visible.
- Non-workspace pages now allow vertical page scroll again (layout shell no longer locks overflow).
- Global dark background tweaked slightly darker for better contrast.
- Job list "Открыть" now opens a preview session for completed jobs (fallback to detail for unfinished).
- Hover animations and hover color shifts were removed from the main UI.
- Batch completion sound was removed from the UI.
- Browser notification popup on batch completion was removed from the UI.
- Compact preview workspace rail was widened and repacked for mobile-like widths; warm theme applied there too.
- Annotation rail buttons now wrap instead of clipping; AI summary chips wrap on small widths.
- Bottom toolbar now respects left/right rail insets to avoid overlapping side panels.
- AI summary tiles now wrap long labels and avoid clipping on narrow rails.
- Bottom toolbar palette aligned to the warm dark theme.
- Center/angle switch palette aligned to warm dark theme.
- Left/right rails now run in condensed mode with minimal summaries and collapsible sections to avoid vertical scrolling.
- Conflict banner text color increased for readability.
- Global drag-and-drop on the home screen auto-detects:
  - archive
  - drawing
  - labels table
- Archive pairing rule:
  - drawing and labels file must have the same basename
  - one pair = one job
- Supported archives:
  - `zip`
  - `7z`
  - `rar`
  - `tar`
  - `tar.gz` / `tgz`
  - `tar.bz2` / `tbz2`
  - `tar.xz` / `txz`
- Result UI no longer exposes raw internal OCR/pipeline text or raw filesystem paths to the user.
- Annotation workspace theme refreshed: removed most borders in left/right rails, history/summary overlays, candidate cards, and canvas banners; replaced with warm backgrounds and subtle shadows for separation.
- Frontend API calls now use a 20s timeout and return a clear “backend not responding” error instead of hanging on submit.
- Annotation rails now default to compact mode with popover lists for candidates/markers and collapsible inspector blocks to avoid vertical scrolling in left/right panels.
- Left rail condensed mode now hides long AI/vocabulary blocks and replaces them with a short summary plus quick-open buttons for overlays.
- Right rail condensed mode now collapses guidance, related-candidate lists, and coordinate fields behind toggles.

## Public deploy notes

- Domain traffic goes through Cloudflare tunnel to local frontend/backend.
- Cloudflare 1033 resolved by restarting the `cloudflared` tunnel for `blueprint-rec`.
- Active project path for site and API is:
  - `C:/projects/sites/blueprint-rec-2`
- Old mirror tree is gone; local services and tunnel must not reference `C:/projects/sites/blueprint-rec` anymore.
- Frontend uses same-origin rewrites for:
  - `/api/*`
  - `/storage/*`
- Favicon is served from:
  - `C:/projects/sites/blueprint-rec-2/apps/web/public/favicon.ico`
- Plain-HTML / no-style failure root cause:
  - `next start` was serving from a broken / non-production `.next` state
  - `/_next/static/*.css` and `/_next/static/*.js` returned `400`
  - fixed by rebuilding the web app and hardening `C:/projects/sites/blueprint-rec-2/scripts/run_webui_public_tunnel.ps1`
  - the startup script now ensures a production web build exists before launching the frontend on `3010`
  - the startup script now also probes real frontend asset health, not just "port is listening"
  - the startup script also probes backend `/health`, so a stale dead process on `8010` gets restarted
  - follow-up scan found and fixed a PowerShell bug in the same startup script:
    - local variable `$home` collided with built-in `$HOME`
    - renamed to avoid random startup failure

## Useful verification artifacts

- Gemini dense-sheet comparison:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/gemini-model-compare/test1/compare_report.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/gemini-model-compare/test1/gemini-2.5-flash-direct/overlay.png`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/gemini-model-compare/test1/gemini-3.1-flash-image-preview/overlay.png`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/gemini-model-compare/test1/gemini-3.1-pro-preview/overlay.png`
- Full-image vocab + full pipeline rerun on `test1.jpg`:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/test1-full-run/vocabulary-step.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/test1-full-run/summary.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/test1-full-run/truth-compare.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/test1-full-run/markers-overlay.png`
- Targeted missing-label recovery rerun on `test1.jpg`:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/test1-targeted-run-v3/summary.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/test1-targeted-run-v3/truth-compare.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/test1-targeted-run-v3/markers-overlay.png`
- No-table status fix:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/no-table-status-fix/final-v2.json`
- Legacy/no-table auto-repair:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/legacy-auto-repair/job-332f-after-v2.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/list-auto-repair/jobs-after-v2.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/failed-auto-repair/synthetic-failed-after.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/startup-auto-repair/jobs-after.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/startup-auto-repair/synthetic-startup-after.json`
- Public domain checks:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/public-domain-check/check.json`
- Site style fix verification:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/site-style-fix/check.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/site-style-fix/home.png`
- Favicon checks:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/favicon-check-v4/check.json`

## Current priorities

1. Improve heavy-sheet recognition quality on dense exploded-view drawings.
2. Keep old jobs and batches self-healing so the UI does not show stale broken history.
3. Keep project context files compact and clean for future agents.
