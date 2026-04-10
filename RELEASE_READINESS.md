# Release Readiness

Last updated: 2026-04-10

## Current status

The project is currently in a "ready for manual QA" state.

Verified baseline:

- `C:/projects/sites/blueprint-rec-2`: `cmd /c npm.cmd run test:web` -> passed
- `C:/projects/sites/blueprint-rec-2`: `cmd /c npm.cmd run test:web:e2e` -> passed
- `C:/projects/sites/blueprint-rec-2`: `cmd /c npm.cmd run test:inference` -> passed
- `C:/projects/sites/blueprint-rec-2`: `cmd /c set NODE_OPTIONS=--max-old-space-size=4096 && npm.cmd run build --workspace @blueprint-rec/web` -> passed
- `C:/projects/sites/blueprint-rec-2`: `cmd /c npm.cmd run test:live-smoke` -> passed
- `C:/projects/sites/blueprint-rec-2`: `cmd /c npm.cmd run test:headless-manual-qa` -> passed

Live sanity snapshot:

- `image001.png`: 3 markers, export succeeds
- `test1.jpg`: 32 markers, 7 `ai_review`, export is correctly blocked while pipeline conflicts remain

## Manual QA order

Use the heavy session first, not the easy one.

Recommended order:

1. `AI review` main queue and `отложенные` queue on `test1.jpg`
2. return from `История` back into live work on the same heavy session
3. candidate flow on the same heavy session:
   - `Ложный`
   - `Создать точку`
   - draft confirm
4. only after that run a quick light sanity check on `image001.png`

## Main risk zones

### 1. `AI review` and deferred flow

This is the heaviest live path and has the most UI state:

- queue switching
- deferred pass
- hotkeys
- progress counters
- finish state after current-pass completion

If something is visually confusing or jumps to the wrong place, it is most likely here.

### 2. `История` -> return to live work

This is the most tightly coupled navigation area:

- sticky header
- `следующий кейс`
- `в review`
- `в отложенные`
- jump from history back to a canvas point

Tests cover it, but manual navigation can still reveal awkward state transitions.

### 3. Candidate flow in the right panel

Recent wording and routing were simplified here, so the main manual risk is UX clarity, not backend breakage:

- rejecting a candidate should keep the operator in candidate mode
- `Создать точку` should lead into a stable draft-marker flow
- confirm should not throw the operator into the wrong panel or list

## Useful artifacts

- checklist: `C:/projects/sites/blueprint-rec-2/MANUAL_QA_CHECKLIST.md`
- live smoke results: `C:/projects/sites/blueprint-rec-2/.codex-smoke/live-smoke/latest-results.json`
- hidden manual QA results: `C:/projects/sites/blueprint-rec-2/.codex-smoke/headless-manual-qa/latest-results.json`
- hidden manual QA report: `C:/projects/sites/blueprint-rec-2/.codex-smoke/headless-manual-qa/latest-report.md`
- rolling project context: `C:/projects/sites/blueprint-rec-2/PROJECT_CONTEXT.md`
