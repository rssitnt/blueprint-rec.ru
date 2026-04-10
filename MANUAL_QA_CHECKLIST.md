# Manual QA Checklist

Last updated: 2026-04-09

## Before manual QA

Run the automated baseline first:

```powershell
cd C:/projects/sites/blueprint-rec-2
cmd /c npm.cmd run test:web
cmd /c npm.cmd run test:web:e2e
cmd /c npm.cmd run test:inference
cmd /c set NODE_OPTIONS=--max-old-space-size=4096 && npm.cmd run build --workspace @blueprint-rec/web
```

Optional but recommended before opening the app manually:

```powershell
cd C:/projects/sites/blueprint-rec-2
cmd /c npm.cmd run test:live-smoke
cmd /c npm.cmd run test:headless-manual-qa
```

This runs headless live checks on real local files and writes results to:

- `C:/projects/sites/blueprint-rec-2/.codex-smoke/live-smoke/latest-results.json`
- `C:/projects/sites/blueprint-rec-2/.codex-smoke/live-smoke/*.png`
- `C:/projects/sites/blueprint-rec-2/.codex-smoke/headless-manual-qa/latest-results.json`
- `C:/projects/sites/blueprint-rec-2/.codex-smoke/headless-manual-qa/latest-report.md`
- `C:/projects/sites/blueprint-rec-2/.codex-smoke/headless-manual-qa/*.png`

Recommended reading order before opening a visible browser:

1. `test:live-smoke` — fail-fast baseline for live API/UI/export on real files
2. `test:headless-manual-qa` — deeper hidden walkthrough for live candidate/history/export behavior

If `next build` throws transient `PageNotFoundError: /_document`, wait a few seconds and run the build again once. Right now this looks like a Next runtime race after other processes, not a product regression.

## Start the app

```powershell
cd C:/projects/sites/blueprint-rec-2
cmd /c npm.cmd run dev
```

Open the app manually and use one session that has:
- at least 2 `ai_review` ambiguity markers
- at least 2 pending candidates
- at least 1 candidate that can be turned into a human draft marker

## Core ambiguity review

### 1. Main review queue

Check:
- the left rail clearly shows `review` count
- selecting an ambiguity point shows the `AI review` card on the right
- `Подтвердить и дальше` moves to the next ambiguity point
- `Ложный и дальше` removes the point and moves to the next one
- keyboard flow works:
  - `A` / `D` or arrows switch points
  - `Enter` confirms
  - `Delete` / `Backspace` removes

Expected:
- queue progress updates correctly
- already processed points do not come back in the same pass

### 2. Deferred pass

Check:
- `Пропустить` removes the current point only from the current pass
- after all current-pass points are processed, the UI shows that the main pass is done but deferred points still exist
- opening `отложенные` shows `Отложенный проход`
- deferred points can be resolved with final actions
- skip is not offered as a real action there

Expected:
- deferred points stay separate from the main pass
- after deferred resolution, ambiguity flow closes cleanly

### 3. History-driven resume

Check:
- `История` opens with the sticky summary/header
- `следующий кейс` returns to the active unresolved queue
- `в review` / `в отложенные` switch to the correct queue
- history route/jump opens the correct point on canvas

Expected:
- history resume sends you back into the right queue context
- jumping from history focuses the right marker instead of a random point

## Candidate review

### 4. Reject candidate

Check:
- select a pending candidate in the left review list
- click `Ложный`

Expected:
- the right rail stays in candidate-review mode if more pending candidates still exist
- the UI does not suddenly switch to a marker inspector by itself

### 5. Candidate to marker flow

Check:
- select a pending candidate
- click `Создать точку`
- confirm that a draft marker is created
- adjust label if needed
- use `Подтвердить по лупе`

Expected:
- candidate turns into a human draft marker
- draft marker can be confirmed without losing focus
- history records both marker creation and confirmation

## Error recovery

### 6. Session reload after failure

Check:
- simulate a failing load or failed command
- use `Повторить загрузку` on initial load failure
- use `Обновить сессию` from the workspace error banner

Expected:
- the session can recover without a full browser reload
- failed `confirm/delete` must not falsely hide or complete the ambiguity point

## Export and pipeline

### 7. Export sanity

Check:
- try export on a session with no blocking conflicts
- try export on a session with hard blocking pipeline conflicts

Expected:
- export works only in valid cases
- blocking conflict sessions do not pretend to export successfully

## Done criteria

The build is ready for a broader human pass if:
- ambiguity review works end-to-end for `confirm`, `delete`, and `skip`
- deferred pass behaves as a distinct follow-up queue
- history can resume and jump back into live work
- candidate review stays stable after `Ложный`
- candidate -> draft marker -> confirm works
- load/command failures do not create false progress
