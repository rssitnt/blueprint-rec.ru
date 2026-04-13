# Implementation Plan

## 1. Main Technical Decision

Build the final product inside `C:/projects/sites/blueprint-rec-2`, but do **not** keep its current session-heavy architecture as the main product model.

Use this repo as the delivery chassis because it already has:

- clear monorepo separation
- modern web stack
- reusable UI components
- cleaner docs/tests structure

At the same time, use the extraction core from `C:/projects/sites/blueprint-rec` as the primary recognition engine, because it is closer to the actual target product:

- batch-oriented
- result-oriented
- stronger current OCR/VLM extraction path

So the practical direction is:

- keep `blueprint-rec-2` as the main codebase
- transplant recognition logic from `blueprint-rec`
- simplify `blueprint-rec-2` from `session-first` to `job/result-first`

## 2. What To Keep From `C:/projects/sites/blueprint-rec`

These parts are the closest to the final product and should be treated as the initial extraction core.

### Keep As Core

- `C:/projects/sites/blueprint-rec/scripts/run_v3_number_pipeline.py`
  - main OCR + VLM extraction logic
  - closest to the user's target outcome
- `C:/projects/sites/blueprint-rec/webui/pipeline_helpers.py`
  - result flattening
  - export helpers
  - coordinate table generation
- `C:/projects/sites/blueprint-rec/webui/app.py`
  - keep as reference for:
    - upload handling
    - PDF/image normalization
    - job directory layout
    - result manifest pattern
    - simple process/result endpoints

### Keep As Secondary Utilities

- `C:/projects/sites/blueprint-rec/scripts/render_overlay_from_markers_json.py`
  - useful for offline debug and consistent overlay regeneration
- `C:/projects/sites/blueprint-rec/scripts/merge_markers_jsons.py`
  - useful if ensemble remains part of the quality strategy
- `C:/projects/sites/blueprint-rec/scripts/evaluate_overlay_with_gemini.py`
  - can later help with confidence/failure reporting

### Keep Only As Historical Reference

- legacy XLSX feedback cycle scripts in `C:/projects/sites/blueprint-rec/scripts`
  - useful only as research history
  - do not make them the mainline of the new product

## 3. What To Keep From `C:/projects/sites/blueprint-rec-2`

These parts are useful, but mostly as product shell and review layer.

### Keep As Main Product Shell

- `C:/projects/sites/blueprint-rec-2/apps/web`
- `C:/projects/sites/blueprint-rec-2/services/inference`
- `C:/projects/sites/blueprint-rec-2/packages/shared-types`

Reason:

- this gives a cleaner long-term delivery structure than the old single FastAPI + Jinja setup

### Keep For Review UI

- `C:/projects/sites/blueprint-rec-2/apps/web/components/annotation/annotation-workspace.tsx`
  - reuse ideas and pieces for preview/edit mode
- `C:/projects/sites/blueprint-rec-2/apps/web/components/annotation/annotation-workspace-state.ts`
  - reuse only parts related to selection, review queues, and edit state if still useful

### Keep For App Wiring

- `C:/projects/sites/blueprint-rec-2/services/inference/app/main.py`
  - FastAPI app bootstrap
  - CORS/static storage setup
- `C:/projects/sites/blueprint-rec-2/apps/web/lib/api.ts`
  - frontend-to-backend wiring style

### Keep As Potential Later Reuse

- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/candidate_vlm_recognizer.py`
  - may later be useful for provider abstraction and vocabulary extraction
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/page_vocabulary.py`
  - may later help false-positive filtering and confidence logic
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/conflict_engine.py`
  - may later help review triage and unresolved issue surfacing

These are **not** phase-1 must-haves.

## 4. What Not To Carry Into MVP As-Is

These parts should not remain central in the final MVP architecture.

### Do Not Keep As Product Center

- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/session_store.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/api/sessions.py`
- `C:/projects/sites/blueprint-rec-2/apps/web/components/annotation/session-home.tsx`

Reason:

- they are built around a persistent `session` model
- the target product is not “session management”
- the target product is “submit drawing -> wait -> get result -> optionally correct”

### Do Not Keep As Primary AI Path

- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/candidate_detector.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/candidate_recognizer.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/candidate_association.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/leader_topology.py`

Reason:

- they fit the current candidate/review/session architecture
- they are interesting building blocks, but not the shortest path to the user's target
- the stronger near-term engine already lives in `run_v3_number_pipeline.py`

## 5. Target Architecture

## 5.1 Backend Model

Replace session-centric API with job/result-centric API.

Recommended primary backend concepts:

- `Job`
- `JobInput`
- `JobResult`
- `ReviewPatch`
- `ExportBundle`

Recommended endpoint surface:

- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/{jobId}`
- `GET /api/jobs/{jobId}/result`
- `POST /api/jobs/{jobId}/review`
- `GET /api/jobs/{jobId}/export.csv`
- `GET /api/jobs/{jobId}/overlay.png`
- `POST /api/batches`
- `GET /api/batches/{batchId}`

## 5.2 Frontend Model

Replace “create session / open workspace” home flow with:

- upload screen
- processing state screen
- result screen
- preview/edit screen

The preview/edit screen can still reuse parts of the current workspace, but the mental model shown to the user should be:

- file result
- confidence
- missing labels
- edit and export

not:

- session
- candidates
- pipeline internals

## 5.3 Internal Processing Model

Recommended internal flow:

1. ingest input files
2. normalize to images per page
3. run extraction core
4. build canonical result JSON
5. compute confidence and failure summary
6. generate exports
7. expose preview/edit
8. re-export after edits

## 6. New Modules To Create In `C:/projects/sites/blueprint-rec-2`

### Backend

Create these modules:

- `C:/projects/sites/blueprint-rec-2/services/inference/app/api/jobs.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/api/batches.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/pipeline_runner.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/input_normalizer.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/job_store.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/result_exports.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/review_editor.py`
- `C:/projects/sites/blueprint-rec-2/services/inference/app/services/confidence_report.py`

### Shared Types

Create or replace contracts around:

- `JobStatus`
- `ResultLabelRow`
- `DrawingResult`
- `BatchResult`
- `ReviewEditCommand`
- `ConfidenceSummary`

### Frontend

Create or reshape:

- upload page
- processing page
- result page
- preview/edit page

Likely new frontend entry points:

- `C:/projects/sites/blueprint-rec-2/apps/web/app/page.tsx`
- `C:/projects/sites/blueprint-rec-2/apps/web/app/jobs/[jobId]/page.tsx`
- `C:/projects/sites/blueprint-rec-2/apps/web/app/batches/[batchId]/page.tsx`

## 7. Migration Strategy

Do **not** try to merge both systems all at once.

Instead, use a staged replacement.

### Stage 1. Freeze The Target Contract

First define:

- canonical result JSON
- final CSV columns
- not-found row behavior
- confidence summary structure
- review edit commands

Without this, both backend and frontend will drift.

### Stage 2. Port Extraction Core

Port `run_v3_number_pipeline.py` into `blueprint-rec-2` backend as an internal service runner.

Initial goal:

- one drawing in
- one result out
- no session model involved

### Stage 3. Build Result-Centric API

Implement minimal job endpoints:

- submit
- poll
- fetch result
- export CSV
- export overlay

At this stage, ignore ZIP and notifications.

### Stage 4. Build Thin Result UI

Implement:

- upload form
- processing state
- result view with:
  - confidence
  - missing labels
  - downloads
  - open preview

This replaces the current home/session UX.

### Stage 5. Reuse Workspace As Review Layer

Extract only the useful editing subset from the current workspace:

- drag point
- add point
- delete point
- relabel point

Hide pipeline complexity from the user.

### Stage 6. Add Batch ZIP

After single-job flow is stable:

- add ZIP ingestion
- add basename pairing
- add per-file progress/result pages

### Stage 7. Add Notifications

Implement:

- in-page sound/status first
- browser notification second

Closed-tab notification is not a first coding step.

## 8. Concrete Order Of Rewriting

### First

Rewrite backend around jobs.

Why first:

- the current user target is pipeline-first
- UI should reflect backend truth, not the other way around

### Second

Rewrite home/result flow in frontend.

Why second:

- current session-first UI creates the wrong product shape

### Third

Port review editing into the new result model.

Why third:

- manual correction is important, but it is a thin layer over the extracted result

### Fourth

Add batch ZIP and notifications.

Why fourth:

- these are multiplicative UX features
- they should come after the single drawing path is solid

## 9. Immediate First Sprint

The first sprint should do only this:

1. define final result schema
2. port `run_v3_number_pipeline.py` into `blueprint-rec-2`
3. expose one `POST /api/jobs`
4. expose one `GET /api/jobs/{jobId}`
5. return result JSON + CSV + overlay
6. replace current homepage with upload -> processing -> result

Nothing else should distract the sprint.

## 10. Immediate Second Sprint

The second sprint should do:

1. result confidence/failure reporting
2. manual preview correction
3. re-export after correction
4. first hardening on false positives and not-found handling

## 11. Immediate Third Sprint

The third sprint should do:

1. ZIP batch flow
2. batch result pages
3. progress tracking
4. notifications

## 12. Final Recommendation In One Line

Use `C:/projects/sites/blueprint-rec-2` as the product shell, but replace its current session-centered heart with the extraction and export heart from `C:/projects/sites/blueprint-rec`.
