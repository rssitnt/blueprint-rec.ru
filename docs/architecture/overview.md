# Architecture Overview

## Goal

Build a shared annotation system, not an OCR pipeline.

## Layers

- `apps/web`
  - session launcher
  - drawing workspace
  - marker editing UI
- `services/inference`
  - session state
  - document storage
  - viewport and marker command handling
  - action history
- `packages/shared-types`
  - shared session and command contracts

## Product rule

The AI should not get a separate hidden workflow. It should operate through the same session + viewport + marker primitives that the human editor uses.
