# API Overview

The backend is now session-driven.

## Core endpoints

- `GET /api/health`
- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/sessions/{sessionId}`
- `POST /api/sessions/{sessionId}/document`
- `POST /api/sessions/{sessionId}/commands`

## Command surface

`POST /api/sessions/{sessionId}/commands` accepts one of:

- `set_viewport`
- `pan_viewport`
- `zoom_to_region`
- `place_marker`
- `move_marker`
- `update_marker`
- `confirm_marker`
- `reject_marker`
- `delete_marker`

This is the same mutation surface that the human UI uses today and that a future AI agent can use later.
