# Project Context

Last updated: 2026-04-14

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
- Added an elevated Windows Scheduled Task (ONLOGON) for stable autostart:
  - Task: `BlueprintRecWebUI`
  - Runs: `C:/projects/sites/blueprint-rec-2/scripts/run_webui_public_tunnel.ps1`
- cloudflared updated to 2026.3.0.
- Tried forcing tunnel startup to `http2`, but Cloudflare edge returned TLS EOF on handshake here; kept `quic`.
- Old Startup leftovers removed:
  - `C:/Users/qwert/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/BlueprintRecWebUI.cmd`
  - `C:/Users/qwert/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/BlueprintRecWebUI.vbs`

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
- OpenRouter routing is now split by sheet type:
  - regular sheets:
    - `google/gemini-3.1-flash-image-preview`
  - heavy low-res sheets:
    - primary:
      - `google/gemini-3.1-pro-preview`
    - fallback:
      - `google/gemini-3.1-flash-image-preview`
- This heavy-sheet routing is wired into:
  - full-page vocabulary extraction
  - final per-candidate VLM review
  - indexed tile recovery
  - targeted missing-label VLM recovery
- VLM vocabulary extraction is now enabled for "heavy" sheets (min side <= 1600) even when the low-res circle mode does not trigger.
- Targeted missing-label recovery now includes a VLM locator pass:
  - Gemini is asked to return normalized center coordinates for missing labels
  - local OCR is used to confirm when possible
- Fallback was verified explicitly:
  - with heavy model intentionally set to a fake id, the same heavy candidate resolved through:
    - `openrouter-vlm:google/gemini-3.1-flash-image-preview`
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
- Stale RUNNING jobs now auto-fail after a max runtime so the UI doesn't stay "processing" forever.
  - current default:
    - 15 minutes
  - file:
    - `C:/projects/sites/blueprint-rec-2/services/inference/app/services/job_store.py`
- Running jobs now also have a hard execution timeout around the real background pipeline call, not only stale-state cleanup on read.
  - file:
    - `C:/projects/sites/blueprint-rec-2/services/inference/app/services/job_store.py`
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
- Home screen right panel no longer repeats the same job card metadata already shown on the left.
  - for jobs, the right side now focuses on:
    - actions
    - exports
    - failure/review notices
    - numeric result summary
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
- Preview workspace now loads the drawing through a real image loader with auto-retry instead of relying only on CSS background-image.
- Imported preview sessions no longer try to auto-run annotation again on open.

## Public deploy notes

- Domain traffic goes through Cloudflare tunnel to local frontend/backend.
- Cloudflare 1033 resolved by restarting the `cloudflared` tunnel for `blueprint-rec`.
- `cloudflared` is currently running and tunnel reports as connected (external check also succeeds), but user still reports timeouts on mobile — likely intermittent tunnel uptime, PC sleep, or network path issues.
- Strong external diagnosis for Russia:
  - the site answers `200` from outside Russia
  - Cloudflare officially reported on `2025-06-26` that since `2025-06-09` Russian ISPs throttle and block traffic to Cloudflare-protected sites
  - Cloudflare states this affects HTTP/1.1, HTTP/2, and HTTP/3/QUIC, and that they cannot restore reliable access for Russia-based users on their side
  - this matches the user's symptom: works in some environments, but times out on Russian mobile/Wi‑Fi without VPN
- Practical conclusion:
  - if stable access from Russia is required, the public user path should not depend on Cloudflare Tunnel / Cloudflare proxy
  - next infra direction is a non-Cloudflare public entrypoint
- Added a second public ingress mode for immediate fallback without Cloudflare:
  - script:
    - `C:/projects/sites/blueprint-rec-2/scripts/run_webui_public_tunnel.ps1 -Provider localhostrun`
  - it starts an SSH reverse tunnel to `localhost.run` against the local public proxy on `3020`
  - current tunnel URL is written to:
    - `C:/projects/sites/blueprint-rec-2/.codex-smoke/localhostrun.url.txt`
  - this path works as a quick public fallback, but the URL is temporary and changes after restart
  - so it is useful as an emergency external link, not yet as a permanent replacement for `blueprint-rec.ru`
- Tunnel ownership cleaned up:
  - `cloudflared` Windows service is now the only intended tunnel owner
  - startup script no longer spawns an extra manual tunnel when the service is already running
  - stale manual tunnel processes from earlier runs were terminated
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
  - the startup script now runs a headless homepage smoke-check through:
    - `C:/projects/sites/blueprint-rec-2/scripts/verify_homepage_smoke.mjs`
  - smoke-check success rules are now practical instead of fragile:
    - page title is correct
    - key UI texts are present
    - Next CSS asset is attached
    - heading is visibly styled
    - no browser console or page errors
- startup flow now restarts frontend once more if the homepage smoke-check fails
- startup flow now also restarts backend when backend source files are newer than the running uvicorn process
  - this closes the class of bugs where backend code changed but the health check stayed green and old logic kept serving
- Started migration away from Cloudflare as the public HTTP entrypoint.
  - practical target shape:
    - `blueprint-rec.ru -> Vercel`
    - Vercel production rewrites -> temporary non-Cloudflare upstream
    - current upstream:
      - `https://1728c6181fc90a.lhr.life`
  - Vercel production deployment is live and serves the site correctly on:
    - `https://blueprint-rec.vercel.app`
  - Cloudflare DNS apex record for `blueprint-rec.ru` was switched from tunnel CNAME to:
    - `A 76.76.21.21`
    - proxy disabled
  - old `www.blueprint-rec.ru` tunnel record was removed from Cloudflare DNS
  - replacement `www` DNS record now points to:
    - `cname.vercel-dns.com`
    - proxy disabled
  - remaining caveat:
    - public recursive resolvers were still returning the old Cloudflare tunnel answer right after the switch, so final outside behavior may lag until DNS propagation catches up
  - separate Vercel caveat:
    - `www.blueprint-rec.ru` still has an internal Vercel alias conflict from an accidental earlier CLI add against the wrong linked project
    - apex `blueprint-rec.ru` is the main path and is configured on the correct Vercel project
  - failure pattern discovered:
    - if the temporary `localhost.run` URL dies, Vercel starts returning `503` and the page body becomes:
      - `no tunnel here :(`
  - attempted improved public chain:
    - user -> `blueprint-rec.ru` -> Vercel
    - Vercel -> `https://blueprint-rec.blueprint-rec.ru`
    - internal tunnel hostname -> local public proxy on `3020`
  - current reality after live checks:
    - this internal-hop chain is not yet reliable enough for production
    - Vercel sometimes gets `no tunnel here :(` or `502` from that hostname
  - current working production path is back on:
    - user -> `blueprint-rec.ru` -> Vercel
    - Vercel -> current `localhost.run` upstream from `C:/projects/sites/blueprint-rec-2/.codex-smoke/localhostrun.url.txt`
  - later live check showed this fallback can also die while the SSH process is still present:
    - symptom: `blueprint-rec.ru` returns `503` and body `no tunnel here :(`
    - root cause was a dead `localhost.run` target behind Vercel
  - as of the latest fix on 2026-04-14, production Vercel rewrite was manually switched back to:
    - `https://blueprint-rec.blueprint-rec.ru/$1`
    - and `blueprint-rec.ru` again returned `200`
  - live failure causes found and fixed:
    - Vercel rewrite generator in `C:/projects/sites/blueprint-rec-2/scripts/run_webui_public_tunnel.ps1` was writing broken destination text (`/\\"` instead of `/$1`)
    - `C:/projects/sites/blueprint-rec-2/.codex-smoke/vercel-proxy` could silently deploy into the wrong Vercel project because its local `.vercel/project.json` drifted
  - startup script now also:
    - copies the root Vercel project link into `C:/projects/sites/blueprint-rec-2/.codex-smoke/vercel-proxy/.vercel/project.json`
    - writes `C:/projects/sites/blueprint-rec-2/.codex-smoke/vercel-proxy/.vercelignore` to exclude deploy logs from proxy deploys
  - practical limitation remains:
    - backend / OCR execution is still on this PC
    - for a truly stable service, worker execution must still move off this machine later
- public tunnel no longer points directly to `next start`
  - new public path is:
    - `cloudflared -> C:/projects/sites/blueprint-rec-2/scripts/web_public_proxy.mjs -> next start`
  - reason:
    - some users hit intermittent `ERR_CONNECTION_RESET` on `/_next/static/*.js`
    - the proxy now serves `/_next/static/*` directly from disk with fixed `content-length`
    - only page/app/API traffic is proxied upstream
  - local public proxy port:
    - `http://127.0.0.1:3020`
  - cloudflared config for `blueprint-rec.ru` is now rewritten automatically to target `3020`
  - follow-up scan found and fixed a PowerShell bug in the same startup script:
    - local variable `$home` collided with built-in `$HOME`
    - renamed to avoid random startup failure
  - follow-up fix for session crashes after rebuild:
    - startup script now restarts `next start` automatically when `.next/BUILD_ID` is newer than the running frontend process
    - this prevents old HTML from referencing deleted JS chunks after a rebuild
  - startup script also no longer crashes when `Win32_Process.CreationDate` is missing or malformed for the running frontend process

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
- Heavy OpenRouter routing checks:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/heavy-openrouter-page4/summary.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/heavy-openrouter-page4/artifacts/markers_v3.overlay.png`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/heavy-openrouter-page4/candidate-sources.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/heavy-openrouter-page4/fallback-check.json`
- Latest page4 run (after locator + forced vocab):
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/heavy-openrouter-page4-v8/job/page4_20260414_123841/work/pipeline/primary/page-001/markers_v3.overlay.png`
- Header masking fix:
  - a text-density based header cutoff is inferred and used to mask the top band for VLM vocab + locator
  - text candidates above that cutoff are dropped
  - verified on page4 with no header false positives:
    - `C:/projects/sites/blueprint-rec-2/.codex-smoke/heavy-openrouter-page4-v12/job/page4_20260414_130917/work/pipeline/primary/page-001/markers_v3.overlay.png`
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
- Startup homepage smoke:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/startup-home-smoke-local/homepage-smoke.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/startup-home-smoke-local/homepage.png`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/startup-home-smoke-manual/homepage-smoke.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/startup-home-smoke-manual/homepage.png`
- Public-proxy / chunk stability checks:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/domain-after-proxy/homepage-smoke.json`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/domain-after-proxy/homepage.png`
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/proxy-chunk-probe.json`
- Favicon checks:
  - `C:/projects/sites/blueprint-rec-2/.codex-smoke/favicon-check-v4/check.json`

## Current priorities

1. Improve heavy-sheet recognition quality on dense exploded-view drawings.
   - `page4` still misses visible labels `5` and `10`; model routing is improved, but the current internal low-res route still under-localizes these two positions.
2. Keep old jobs and batches self-healing so the UI does not show stale broken history.
3. Keep project context files compact and clean for future agents.
