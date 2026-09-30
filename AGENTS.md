# AGENTS.md

Agent-focused guidance for this repository ([AGENTS.md format](https://agents.md/)). Human-facing docs live in `README.md`.

## Project overview

Google Scholar scraper and Flask API (`RummerLab/scholar`). Runtime deps: `requirements.txt`. Lint/test deps: `requirements-dev.txt` (includes runtime via `-r requirements.txt`). Docker base image: `python:3.10-slim-trixie` (see `base/Dockerfile`); web/cron build on `scholar-base:latest`.

## Setup commands

```bash
python -m venv scholar
# Windows: scholar\Scripts\activate
source scholar/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # ruff + pytest
```

Secrets: copy `.env.template` → `.env`. Never commit `.env` or `google-credentials.json` (both gitignored).

## Lint and format (mandatory)

After every edit to Python files, before considering a task complete:

```bash
ruff check . --fix && ruff format .
```

Or: `python -m ruff check . --fix && python -m ruff format .`

- `ruff check .` — lint (`--fix` where possible)
- `ruff format .` — format
- Fix any remaining Ruff issues before finishing

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
# CI excludes integration tests:
pytest tests/ -m "not integration"
```

Add or update tests for code you change. Fix failures before finishing.

## Code quality

- Refactor when touching code; remove dead code and obsolete files; keep the repo lean
- Prefer small, focused modules; clear names (`get_doi_from_url` not `fetch`); type hints where helpful
- DRY: extract shared helpers when the same block appears 2+ times
- Error handling: catch explicitly; log and re-raise or return meaningful values
- Comments explain *why*, not *what*; docstrings for public functions
- Python: PEP 8 (Ruff), `snake_case` / `PascalCase`, prefer `pathlib` where practical
- Path containment under user-influenced dirs: use `src.path_safety.safe_path_under` (CodeQL-friendly `realpath` + `startswith`), not only `Path.resolve` / `relative_to`

## README and docs

`README.md` is the primary human documentation. Keep it accurate when you change:

- Features, scripts, setup, env vars (also `.env.template`), API, dependencies, Docker/run commands

Checklist: working examples, current install steps, remove outdated sections.

## Security

- Do not commit secrets, tokens, or service-account JSON
- Do not weaken path/DOI validation without a security-minded reason
- Prefer rotating credentials if they may have been exposed

## Maintaining this file

Treat `AGENTS.md` as living agent docs (replaces former `.cursor/rules/*.mdc`):

- Add guidance when recurring patterns, tooling, or pitfalls emerge
- Update when standards change; remove redundant or conflicting text
- Keep sections focused and actionable; prefer concrete commands over vague advice


## Pull requests

Before merging any pull request:

1. **Read all comments** on the PR — conversation comments, review comments (including those on specific lines), and bot comments. Address or acknowledge them. Do not merge while review feedback is unresolved.
2. **Wait for CI to complete successfully.** GitHub Actions (and other required checks) on the PR must finish and pass. Do not merge while checks are pending, failed, cancelled, or skipped when they are required. If CI fails, fix the cause and wait for a green run before merging.
