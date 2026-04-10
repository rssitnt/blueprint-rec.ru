# Blueprint Annotation Desk

Monorepo for a shared drawing-annotation workspace where an AI agent and a human use the same session model, viewport controls, markers, and action history.

## What is in the repo now

- `apps/web` — Next.js workspace UI
- `services/inference` — FastAPI session API and local file storage
- `packages/shared-types` — shared TypeScript contracts for sessions, markers, viewport, and commands
- `docs` — compact docs for architecture, API shape, and contracts

## Current MVP

The repository no longer targets OCR extraction.

The current product is an annotation-first system:

- create a session
- upload one raster drawing into that session
- open a canvas workspace
- zoom, pan, place markers, move markers, update status, confirm, reject, delete
- persist marker state and viewport state
- store a full action log that can later be used by an AI tool layer

## Local run

### Web

```bash
npm install
npm run dev:web
```

Set `apps/web/.env.local` if the API is not on the default local address. The current local dev default in this repo is `http://127.0.0.1:8010`.

### API

```bash
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r services/inference/requirements.txt
npm run dev:inference
```

Uploaded drawings are stored locally under `services/inference/var/`.

## Checks

```bash
npm run lint:web
npm run build:web
npm run test:inference
```
