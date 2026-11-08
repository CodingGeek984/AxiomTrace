# Contributing to AxiomTrace

Thanks for helping. This project is split into two halves that must stay in
sync: a Flask API (`backend/`) and a Next.js studio (`frontend/`).

## Ground rules

- **Never move logic across the boundary.** Backend work lives in `backend/`,
  UI work lives in `frontend/src/`. The frontend talks to the API through
  `frontend/src/lib/api.js` and nothing else.
- **No placeholders.** Every file that ships must be complete and runnable —
  no TODO stubs, no lorem ipsum, no dead exports.
- **Bounded memory is a feature.** Anything that accumulates (logs, history,
  caches, buffers) needs an explicit cap and a documented trimming strategy.
- **No secrets in the repo.** Configuration comes from environment variables
  read in `backend/config.py`.

## Development setup

```bash
# Backend
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# Frontend
cd frontend
npm install
```

Run the API with an explicit environment so you do not accidentally get
production defaults:

```bash
cd backend
AXIOM_ENV=development .venv/bin/python app.py
```

Run the studio:

```bash
cd frontend
npm run dev
```

## Branching and commits

- Keep commits focused: one concern per commit.
- Write imperative subjects — `fix sandbox timeout on fork` not `fixed ...`.
- Reference the area you touched (`backend:`, `frontend:`, `tests:`, `docs:`)
  when it is not obvious from the diff.

## Tests

Run both suites before opening a PR.

```bash
# Backend — parser, sandbox, mutation sweep, API (82 tests)
cd backend
AXIOM_ENV=testing .venv/bin/python -m pytest

# Frontend — lint + production build
cd frontend
npm run lint
npm run build
```

Notes:

- `AXIOM_ENV=testing` is what makes the sandbox deterministic (thread
  isolation, fixed budgets). Do not assert on wall-clock timings.
- Prefer behaviour tests over implementation tests: assert the envelope, the
  verdict, the cap — not the internal function that produced it.
- When you fix a bug, add a regression test that fails before the fix.

### Writing backend tests

Tests live in `backend/tests/` and share fixtures from `conftest.py`
(`client`, `app`, `sandbox`, `parser`). Follow the existing naming:
`test_<area>_<behaviour>` grouped in classes by feature.

### Adding an endpoint

1. View function in the relevant `backend/controllers/*.py`.
2. Register it in the blueprint (v1) and, if it replaces a legacy path, keep
   the `/api/*` alias working through `_register_legacy_compat`.
3. Return through `utils/response.py` so the `{ ok, code, ... }` envelope stays
   consistent.
4. Cover it in `backend/tests/test_api.py`.

## Frontend conventions

- Read `frontend/AGENTS.md` before touching Next.js code — this version has
  breaking changes and the local docs in `frontend/node_modules/next/dist/docs/`
  are authoritative.
- Components are `"use client"` unless there is a reason not to be.
- Design tokens live in `frontend/src/config/theme.js`; never hardcode hex
  colours in components. Tailwind picks the same values up via
  `tailwind.config.js`.
- New canvas state belongs in `frontend/src/store/useGraphStore.js`, and any
  buffer you add there needs a cap next to `MAX_EXECUTION_LOGS`.

## Sandbox changes

The sandbox is the most safety-critical code in the repo. Any change to
`backend/services/sandbox_service.py` must preserve:

- process isolation (never execute untrusted code in the request process),
- the wall-clock watchdog and instruction budget,
- the import guard,
- size-capped, `json_safe()` output.

Add a test for every new failure mode you introduce.

## Pull requests

- Describe *what* changed and *why*, not just where.
- Include the output of the backend test suite and `npm run lint && npm run build`.
- Keep diffs reviewable: separate refactors from behaviour changes.

## Reporting issues

Include the environment (`AXIOM_ENV`, Python and Node versions, OS), the
source snippet if it is reproducible, and the response body from
`/api/v1/health`.
