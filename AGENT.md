# Project Overrides For Current Work

These rules override the older spec below whenever they conflict with it.

## Current product direction

- This repository is no longer an OCR-first callout extraction product.
- Current product is an annotation-first workspace for manual and AI-assisted point placement on raster drawings.

## Browser automation rule

- By default, any browser testing, browser-driven annotation, recording, or export verification must run in the background.
- Do **not** open visible browser windows or foreground tabs if the same task can be done headlessly.
- Do **not** steal focus from the user with browser automation.
- Visible browser windows are allowed only if the user explicitly asks to see the browser interaction live.
- If a video of site work is requested, prefer a headless workflow:
  - render frames in the background;
  - assemble them into `mp4`;
  - save the result to disk;
- avoid showing the automation window on screen.

## GitHub access rule

- If the user asks to push or set up GitHub access, first try local `gh`:
  - check `gh auth status`;
  - attempt repo create if needed;
  - only ask the user when `gh` requires login or lacks permission.

## Source-of-truth rule for markup

- If the user asks to mark a drawing "from scratch", do not reuse old point tables, OCR outputs, or legacy marker JSON as the source of coordinates.
- In that mode, the drawing itself is the only source of truth.

## Candidate OCR state

- Candidate-first review is now partially live.
- Backend candidates may include:
  - `suggested_label`
  - `suggested_confidence`
  - `suggested_source`
- The UI already renders these OCR hints and uses them when creating a marker from a candidate.
- This is verified on `image001.png` and should not be oversold:
  - simple sheets already benefit;
  - harder sheets like `page4.png` still suffer because the current detector is too circle-heavy and generates too many raw candidates.
- If the next agent continues this work, the root next move is improving candidate generation for text callouts.

## 2026-04-07 checkpoint

- The pipeline direction is now:
  - drawing
  - candidate detector
  - local crop OCR
  - conflict marking
  - optional review UI
- `page4.png` is already strong in one-shot mode.
- Recent fixes already in code:
  - reduced low-res circle-only OCR from multiple candidates per cluster to 1 best candidate;
  - added pruning for oversized low-res numeric circles;
  - skipped low-res shape fallback when enough candidates already exist;
  - made low-res final OCR rerun only on suspicious candidates;
  - allowed tiny-circle `88 -> 8` style correction when inner circle OCR clearly sees a single digit.
- There is currently no external VLM key in env:
  - `OPENAI_API_KEY` missing
  - `GEMINI_API_KEY` missing
  - `OPENROUTER_API_KEY` missing
- Because of that, current work is still limited to local detector/OCR improvements unless the user provides auth.

# SWE Agent Task: Callout Number Extraction System for Raster Drawings

## 1. Project goal

Build a production-oriented web system that processes raster mechanical drawings / exploded views and extracts **callout numbers** from the drawing.

For each detected callout number, the system must return:

- recognized label text;
- **center coordinates of the number** in pixels relative to the original image;
- geometry of the detected region;
- confidence values;
- matching status against an optional table of allowed labels.

The product must expose this workflow through a **minimalist web UI built with Next.js + shadcn/ui**.

The system must support:

- single drawing upload;
- single drawing + optional table upload;
- batch upload of up to **100 drawings**;
- batch upload of up to **100 drawing/table pairs**, where table filenames must match drawing filenames by base name.

Priority order:

1. do **not delete valid points**;
2. maximize detection/recognition quality;
3. return reliable center coordinates;
4. correctly reconcile results with an optional table.

---

## 2. Domain constraints

This project is specifically about exploded-view / assembly-like drawings where:

- callout numbers may be inside circles or may appear without circles;
- numbers may be small, noisy, rotated, or near leader lines;
- many **non-callout circular shapes** exist on the parts themselves;
- therefore **global circle detection must not be the primary candidate generator**;
- the system must detect **callout text objects**, not circles;
- if a table exists, it must constrain the final matching logic.

Important policy:

- a circle is only a **local attribute** of an already found candidate;
- the system must not start from "find all circles on the page";
- Gemini / LLM vision must not be the primary source of coordinates.

---

## 3. Product scope

### In scope

- upload drawings and optional tables;
- pair drawing/table files by filename in batch mode;
- run CV/OCR pipeline;
- reconcile detected labels with optional table values;
- display visual overlay and structured results;
- export results;
- support per-file status and batch summary.

### Out of scope for first delivery unless required later

- user accounts / authentication;
- collaborative review workflows;
- annotation tool inside the product;
- cloud deployment hardening;
- multi-tenant permissions;
- full MLOps platform.

---

## 4. Supported inputs

### Drawings

Support these input formats:

- PNG
- JPG / JPEG
- TIFF
- PDF

### Tables

Support these input formats:

- XLSX
- CSV

---

## 5. Current work notes (2026-04-08)

- Low-res one-shot reliability: a global label vocabulary is now used to gate low-res OCR and tile-VLM label updates, and also the final per-candidate OCR/VLM refinements. This is meant to prevent hallucinated labels (for example `298` instead of `29B`).
- Browser automation must run headless or in the background only. Do not open new visible Chrome windows.
- PDF
- PNG / JPG / JPEG (image-based table)

### Batch mode rules

The product must accept up to **100 files/pairs** in one batch.

Batch pairing rule:

- pair drawing and table by the same **base filename**;
- extensions may differ.

Examples:

- `assembly_001.png` + `assembly_001.xlsx`
- `sheet12.jpg` + `sheet12.csv`

If a drawing has no table, it must still be processed.

If a table has no drawing, show it as an orphan table.

If there are multiple tables with the same base filename, mark as conflict.

The system must never fail the whole batch because of a single bad file.

---

## 5. Required outputs

For each processed drawing, the system must produce:

1. structured JSON output;
2. tabular export (CSV and/or XLSX);
3. preview image with overlays;
4. concise per-file summary.

### Required JSON shape per detected candidate

```json
{
  "source_file": "assembly_001.png",
  "candidate_id": "cand_0001",
  "label_text": "26",
  "ocr_top_k": [
    {"text": "26", "score": 0.81},
    {"text": "28", "score": 0.63}
  ],
  "center_text_x": 1842,
  "center_text_y": 936,
  "polygon": [[1810,910],[1876,908],[1878,960],[1812,962]],
  "has_circle": true,
  "center_marker_x": 1844,
  "center_marker_y": 937,
  "det_conf": 0.91,
  "ocr_conf": 0.81,
  "match_conf": 0.88,
  "table_row_id": "row_12",
  "status": "matched",
  "review_reason": null
}
```

### Per-file summary fields

At minimum:

- total candidates;
- confirmed results;
- matched;
- missing_on_drawing;
- extra_not_in_table;
- review;
- duplicate_label;
- processing errors.

### Batch summary fields

At minimum:

- total uploaded files;
- total drawings processed;
- drawings with tables;
- drawings without tables;
- orphan tables;
- conflicts;
- total matched / review / missing / extra.

---

## 6. Status model

Use these statuses consistently across backend and UI:

- `matched`
- `review`
- `extra_not_in_table`
- `missing_on_drawing`
- `duplicate_label`
- `detected_no_table`
- `processing_error`

Notes:

- `missing_on_drawing` applies to labels present in the table but not found in the drawing;
- `detected_no_table` applies when the drawing had no table;
- `review` must be preferred over aggressive deletion when the system is uncertain.

---

## 7. Core ML / CV pipeline requirements

## 7.1. High-level pipeline

The system must follow this logical flow:

1. input ingestion;
2. drawing/table pairing;
3. drawing preprocessing;
4. high-recall candidate generation for callout numbers;
5. OCR on candidate crops;
6. precise center estimation for the number;
7. optional table extraction;
8. constrained matching against the table when present;
9. visual/semantic verification for ambiguous cases;
10. final result assembly;
11. export + UI rendering.

---

## 7.2. Detection rules

The detector must search for **callout numbers / callout text objects**, not circles.

Requirements:

- high recall is more important than early precision;
- small objects must be supported;
- tiled/sliced inference must be supported for large drawings;
- oriented boxes or polygons are preferred over plain axis-aligned boxes;
- the system must preserve original-image coordinates after tiling and resizing.

The detector must be robust to hard negatives such as:

- holes;
- washers;
- circular part geometry;
- UI overlays;
- title blocks;
- borders;
- table cells;
- decorative circles;
- dimensioning artifacts.

---

## 7.3. OCR rules

OCR must operate primarily on **candidate crops**, not only on the whole page.

For each candidate, the backend must retain:

- top-1 OCR text;
- top-k alternatives;
- OCR confidence;
- a quality/ambiguity flag.

The OCR layer must not be treated as the only source of truth.

---

## 7.4. Center coordinates

The primary required output is the **center of the text number**:

- `center_text_x`
- `center_text_y`

This is more important than the center of an enclosing circle.

Secondary optional outputs:

- `has_circle`
- `center_marker_x`
- `center_marker_y`

Fallback behavior for early versions is allowed:

- use polygon center / OBB center / bbox center if a dedicated point model is unavailable.

However, the architecture must allow later replacement with a more precise center-estimation model.

---

## 7.5. Circle handling

Circle detection must never be the primary proposal mechanism.

Allowed usage of circles:

- local refinement after a candidate callout has already been found;
- local feature for verification;
- optional attribute for UI and debugging.

Forbidden usage:

- global search for circles across the whole drawing as the first step.

Reason:

- many circles belong to the parts and would create too many false positives.

---

## 7.6. Table handling

If a table is provided, the system must switch to a **closed-world matching** regime.

That means:

- extract the set of valid labels from the table;
- use that set to constrain final label assignment;
- table data must influence final matching, not just post-hoc reporting.

Important:

- do not equate `Qty` with the number of visible callouts on the page;
- the table defines allowed position identifiers, not always expected visual multiplicity.

---

## 7.7. Matching logic

After OCR, the system must perform a global candidate-to-label reconciliation stage.

Behavioral requirements:

- if table exists, match detected candidates against allowed labels from the table;
- unresolved or ambiguous cases must become `review`, not silently disappear;
- labels from the table that were not matched must appear as `missing_on_drawing`;
- candidates that do not map to the table must appear as `extra_not_in_table` or `review`.

The implementation may use assignment / ranking logic internally, but the observable behavior must satisfy the status model above.

---

## 7.8. Gemini / LLM vision usage rules

Gemini may be used only as a **conservative verifier / reranker / auditor**.

Valid uses:

- rerank OCR hypotheses for a single existing candidate;
- classify `callout / not_callout / review` for a single existing candidate;
- choose the best match from a restricted set of labels;
- inspect low-confidence or conflicting cases.

Invalid uses:

- find all points from scratch on the whole page;
- define final pixel coordinates;
- aggressively delete candidates.

Decision policy must be conservative:

- false deletion is worse than false retention;
- if uncertain, output `review`.

If the codebase includes Gemini support, it must be implemented as an optional stage that is only invoked on ambiguous candidates.

---

## 8. Web application requirements

## 8.1. Tech stack

Frontend:

- Next.js
- TypeScript
- shadcn/ui
- Tailwind CSS

Backend:

- Python service for CV/OCR/ML processing
- HTTP API or queue-based job execution

The UI and inference service must be separated.

---

## 8.2. Required screens

### A. Upload screen

Must support:

- upload of a single drawing;
- upload of a single drawing + optional table;
- batch upload up to 100 files/pairs;
- automatic pairing by base filename;
- display of:
  - paired files,
  - drawings without tables,
  - orphan tables,
  - conflicts.

### B. Processing screen

Must show:

- per-file status;
- overall progress;
- success / error state per item;
- processing summary.

### C. Results screen

For each drawing, show:

- original preview;
- overlay preview with detected points/polygons;
- table/list of results;
- filters by status;
- toggle between source and annotated view.

### D. Detail panel / detail view

For a selected candidate/result, show:

- label text;
- coordinates;
- confidence values;
- status;
- OCR alternatives;
- linked table row if available;
- review reason.

---

## 8.3. Minimalist UI requirement

The interface should be minimal, functional, and task-oriented.

Avoid:

- decorative complexity;
- heavy dashboards unrelated to the task;
- unnecessary charting.

Prioritize:

- clean upload flow;
- transparent batch pairing;
- readable result inspection;
- obvious export actions.

---

## 9. API contract requirements

Implement backend endpoints that support at least the following operations.

### Upload / job creation

- create a processing job;
- upload single or batch files;
- return paired/unpaired/conflict analysis.

### Processing

- start processing for a job;
- query processing status;
- fetch per-file results.

### Results

- get JSON results;
- get preview/overlay image;
- download export file(s);
- get batch summary.

### Suggested endpoint set

This exact naming is flexible, but equivalent behavior is required:

- `POST /api/jobs`
- `POST /api/jobs/:jobId/files`
- `POST /api/jobs/:jobId/start`
- `GET /api/jobs/:jobId/status`
- `GET /api/jobs/:jobId/results`
- `GET /api/jobs/:jobId/results/:fileId`
- `GET /api/jobs/:jobId/exports`

---

## 10. File pairing logic

Implement deterministic pairing by filename stem.

Example logic:

- drawing stem = filename without extension;
- table stem = filename without extension;
- if stems match, pair them;
- if one drawing has multiple matching tables, mark conflict;
- if a table has no drawing, mark orphan;
- if a drawing has no table, process without table.

This pairing result must be visible before starting processing.

---

## 11. Data model requirements

At minimum define internal models for:

- `Job`
- `SourceFile`
- `PairedInput`
- `ProcessingResult`
- `Candidate`
- `TableEntry`
- `BatchSummary`

### Suggested `Candidate` fields

- `candidateId`
- `sourceFile`
- `labelText`
- `ocrTopK`
- `centerTextX`
- `centerTextY`
- `polygon`
- `hasCircle`
- `centerMarkerX`
- `centerMarkerY`
- `detConf`
- `ocrConf`
- `matchConf`
- `status`
- `tableRowId`
- `reviewReason`

### Suggested `TableEntry` fields

- `rowId`
- `label`
- `rawRow`
- `normalizedLabel`

---

## 12. Export requirements

Provide export options for:

- per-file JSON;
- per-file CSV or XLSX;
- batch-level consolidated export;
- annotated preview image(s).

Batch export should include enough information for offline QA.

---

## 13. Error handling requirements

The system must handle errors per file and continue processing the rest.

Examples:

- unreadable image;
- invalid PDF;
- malformed XLSX;
- unsupported format;
- pairing conflicts;
- model inference failure for one file.

Expose file-level error messages in UI and API.

Do not hide failures silently.

---

## 14. Observability and debugging

The implementation must preserve intermediate artifacts useful for debugging when enabled.

Examples:

- preprocessed image;
- candidate proposals;
- OCR crops;
- overlay images;
- matching diagnostics;
- review reasons.

This can be controlled by a debug flag/configuration.

---

## 15. Definition of done

The task is complete when all of the following are true:

1. user can upload one drawing, with or without a table;
2. user can upload a batch up to 100 items;
3. batch pairing by filename works and is visible in UI;
4. system processes drawings and returns detected labels with center coordinates;
5. system can reconcile detections with a provided table;
6. non-callout circles on the parts do not serve as the primary proposal mechanism;
7. uncertain cases are surfaced as `review` rather than silently removed;
8. results are visible in a minimalist Next.js + shadcn UI;
9. per-file and batch exports are available;
10. one bad file does not break the whole batch.

---

## 16. Implementation guidance for the agent

Implement this in two layers:

### Layer 1 — Product skeleton

Deliver first:

- Next.js UI;
- upload flow;
- pairing logic;
- job/result pages;
- backend API scaffolding;
- placeholder inference integration boundary.

### Layer 2 — Inference pipeline integration

Then integrate:

- preprocessing;
- candidate generation;
- OCR;
- table extraction;
- matching;
- optional verifier/reranker stage.

### Layer 3 — Quality improvements

Then add:

- better center estimation;
- debug artifacts;
- improved review logic;
- export refinement;
- performance tuning for batch mode.

---

## 17. Non-negotiable rules

- Do not make global circle detection the main detection strategy.
- Do not let ambiguous candidates disappear silently.
- Do not use Gemini as the primary locator or coordinate source.
- Do not tie UI directly to heavy inference logic in the same runtime path.
- Do not assume table `Qty` equals number of visible callouts.
- Do not fail the full batch because of one broken file.
- Before giving the user a final export path, always open the exact exported artifact and verify it is not empty or obviously wrong.
- Never report a candidate-review export as a final result if it still has `0` confirmed markers.
- Real provider keys now exist only in the ignored local file `C:/projects/sites/blueprint-rec-2/services/inference/.env.local`; never print them.
- Treat `C:/projects/sites/blueprint-rec-2/blueprints-test/test1.jpg` as the single production gate. `image001.png` and `page4.png` are already considered solved benchmarks; optimize the pipeline against `test1.jpg`.

---

## 18. Suggested repository structure

This exact structure is flexible, but use a clean separation of concerns.

```text
root/
  apps/
    web/                  # Next.js + shadcn UI
  services/
    inference/            # Python CV/OCR/ML service
  packages/
    shared-types/         # shared DTOs / schemas
  docs/
    api/
    examples/
  samples/
    inputs/
    outputs/
```

---

## 19. Final expected user experience

A user should be able to:

1. open the site;
2. upload a drawing and optionally a table, or upload a batch up to 100;
3. review pairing results before processing;
4. start processing;
5. inspect each drawing visually with overlays and structured rows;
6. see which labels matched the table and which require review;
7. export results.

That is the target behavior the implementation must achieve.

- 2026-04-08: для hard-case low-res страниц не включать тяжёлые context/sequence tile-проходы без крайней нужды; сначала OCR recovery и vocabulary-aware recovery.
