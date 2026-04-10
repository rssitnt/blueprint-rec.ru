# Shared Contracts

The minimal shared model is:

- `AnnotationSession`
- `DocumentAsset`
- `Viewport`
- `Marker`
- `ActionLogEntry`
- `SessionSummary`
- `SessionCommandRequest`

## Marker statuses

- `ai_detected`
- `ai_review`
- `human_confirmed`
- `human_corrected`
- `rejected`

## Why commands matter

The command layer is the bridge between human UI and future AI tooling. Instead of adding ad-hoc internal mutations later, new automation should use the same command contract that already powers the workspace.
