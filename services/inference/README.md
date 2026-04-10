# Annotation API Service

FastAPI service for session-based drawing annotation.

## What it does

- creates annotation sessions
- stores one raster drawing per session
- serves uploaded files from local storage
- stores viewport state
- stores markers and action history
- exposes a command-style mutation API for both humans and future AI tools

## Current limits

- in-memory session state
- raster images only
- no authentication
- no background AI worker yet
