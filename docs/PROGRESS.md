# Progress

## State (2026-11-14)

- **Phase 1 (the plan): accepted** by the supervisor (04b3f54); decisions on the open questions recorded (03dc702,
  `docs/plan/06-build-plan.md` 6.2). Q1 (cloud account and domain) is the user's and still open: until then M0–M3 and the parts of M4
  that need no VM are built.
- **M0 Foundations: done** (see below). Next: **M1 Level-2 access**.

## M0 — Foundations (done)

What was built:
- Python project: `pyproject.toml` (Python 3.12, `uv` lock), packages `engine`, `service`, `indexer`; ruff (lint + format), mypy
  strict, pytest with warnings as errors (one deprecation notice of the test client filtered: starlette suggests a new test-client
  package, `httpx2`, which has not been reviewed, so `httpx` is kept).
- API v1: `service/app.py` with `GET /api/v1/health` (ok, service, version, start time, uptime, product versions), security headers,
  `no-store` on API answers, the built web app served from `web/dist`.
- Data: `data/bands.json` (the seven named bands, read by `engine/bands.py`, validated), `data/targets.json` (the five test targets as
  SIMBAD gives them, written by `tools/snapshot_targets.py`; snapshot 2026-11-14 from the Harvard SIMBAD mirror — the CDS mirror
  answered with an error page that day).
- Web shell: `web/` (Vite 5, TypeScript 5.6, Preact 10), hash routes for Globe, Lab, Findings, Method, a status line that asks the
  service, phone layout.
- Tests: Python 14 (health, headers, static serving, bands, targets); web unit 3 (routes); end-to-end 3 in Chromium against the real
  service serving the built app (status line from the service, screens switched by clicking, a link opens its screen, no horizontal
  overflow at 375 px, no console error).
- CI: `.github/workflows/ci.yml` (Python: ruff, format, mypy, pytest; web: typecheck, unit, build, Playwright e2e). arm64 runners are
  not used: they are not free for private repositories; the arm64 run happens on the cloud VM (M4).
- `Makefile` (`setup`, `dev`, `lint`, `test`, `e2e`, `check`); decision records 0001–0003 in `docs/decisions/`.

Notes for whoever continues:
- On this Mac `uv` is reached through pyenv's 3.11.9 (`/Users/aostapenko/.pyenv/versions/3.11.9/bin/uv`); `make UV=<path>`.
- The global npm cache has files owned by another user (EACCES); install with `--cache <scratch dir>`.
- `@playwright/test` is pinned to 1.59.1 because that is the version whose Chromium (build 1217) is installed here; CI installs its
  own browser.
- Filter curves (2MASS J/H/Ks, WISE W1/W2) are not in `data/` yet: they are fetched from the SVO Filter Profile Service in M8, where
  the catalogue check needs them.
- The API runs on port 8780 (dev) and 8781 (e2e); the research service's port 8770 is never used.

## Milestones

| Milestone | State | Report |
|---|---|---|
| M0 Foundations | done | this file, 2026-11-14 |
| M1 Level-2 access | next | |
| M2 Exposure index | not started | |
| M3 Field engine | not started | |
| M4 Service core, cloud gate | not started (deployment waits for Q1) | |
| M5 Lab picture | not started | |
| M6 Globe | not started | |
| M7 Sources, Object card, archive images | not started | |
| M8 Spectra | not started | |
| M9 Search between passes, pairs, chance | not started | |
| M10 Film and its search | not started | |
| M11 Search inside a visit | not started | |
| M12 Anomaly map | not started | |
| M13 Bodies beyond Neptune | not started | |
| M14 Findings, Method | not started | |
| M15 Hardening | not started | |
| M16 Demo readiness | not started | |

## Open questions

Q1 (cloud account, domain): the user's. See [build plan, 6.2](plan/06-build-plan.md#62-open-questions-and-decisions).
