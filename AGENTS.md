# RASPLOT Agent Instructions

## Scope
- Work only on files explicitly named in the user's prompt.
- Do not scan or analyze the full repository unless explicitly requested.
- Do not inspect .venv, node_modules, or generated backend/app/static assets unless explicitly requested.
- Read the minimum code necessary to complete the requested task.

## Architecture
- Frontend source: frontend/src
- Backend source: backend/app
- backend/app/static contains generated frontend build output.
- Preserve existing frontend/backend architecture unless explicitly asked to change it.

## Engineering Safety
- Do not modify HDF parsing unless explicitly requested.
- Do not modify PDF/DXF export unless explicitly requested.
- Do not modify existing engineering calculations unless explicitly requested.
- Do not modify Panel Setup or Auto Generate behavior unless the task specifically requires it.
- Do not refactor unrelated code.

## Git
- Never commit, push, merge, switch branches, or delete branches unless explicitly requested.
- Do not discard existing user changes.
- Report changed source files after implementation.

## Validation
- Run only validation necessary for the files changed.
- Frontend-only change: run frontend build.
- Backend-only change: run targeted backend validation.
- Do not run broad repository analysis just for verification.

## Response
- Keep reports concise.
- State what changed, validation result, and changed files.
- Do not provide lengthy repository summaries unless requested.
