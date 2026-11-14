# Progress

## State (2026-11-14)

- **Phase 1 (the plan): accepted** by the supervisor (04b3f54); decisions on the open questions recorded (03dc702,
  `docs/plan/06-build-plan.md` 6.2). Q1 (cloud account and domain) is the user's and still open: until then M0–M3 and the parts of M4
  that need no VM are built.
- **M0 Foundations: done.** **M1 Level-2 access: done** (V2 passed; V1 partly: see below). Next: **M2 Exposure index**.

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

## M1 — Level-2 access (done)

What was built:
- `engine/spx/`: the FITS header chain from bytes (`fitsio`), the layout of a file in both forms (`layout`: QR2 with plain FLAGS and
  a PSF cube; v27 = QR3 and DR1 with RICE-compressed FLAGS, one tile per row, and an EPSF table), row decoding including the RICE
  tiles (`rows`), the flag-bit table from the file (`HIERARCH MP_<NAME>` in QR2, `MSKN/MSKM` in v27) with masks by name (`flags`), the
  celestial WCS with SIP ignoring the alternate A/W descriptions (`wcs`), the wavelength map by bicubic spline with the D5/D6 v3.0
  offset (`wave`), the PSF sets with pixel integration of the QR2 cube (`psf`), the 12-vertex sky footprint for the index (`footprint`).
- `service/reader/`: async S3 range reads with one retry, a concurrency limit and a breaker (`s3`); the header-chain walk, header cache
  and 64-row band cache in hashed sub-folders with atomic writes (`files`); one PSF set per (detector, layout, size) (`psfs`).
- Tests: 34 (synthetic files of both layouts: header chain equal to astropy's, rows and RICE flags exact, flag tables, wavelength
  through the nodes and the calibration offset, PSF normalised and centred, WCS round trip, band cache with no second request, retry,
  404, breaker, footprint overlap incl. RA 0); 2 live tests on real QR2 and QR3 files (`SKY_NET=1`).

Facts found on the real archive (2026-11-14):
- The bucket has **DR1** (`dr1/level2/`, uploaded 2026-11-11): periods 2025W17_4B–2026W20_1A reprocessed with v27 (folders
  l2b-v27-2026-235, -247, …), plus `dr1/hips5_cube/` (an order-5 HiPS spectral cube, ~321 MB per tile). IRSA ObsCore now lists
  `spherex_dr1` 945,725, `spherex_dr1_deep` 233,681, `spherex_dr1_cube` 12,288, `spherex_qr3` 362,124, `spherex_qr3_deep` 86,700, and no
  longer lists QR2. The ObsCore lag of 2026-11-12 is gone.
- Layout facts: IMAGE data at 23040 (QR2) or 28800 (v27); v27 FLAGS RICE_1, BLOCKSIZE 32, BYTEPIX 4, tiles of one row; the PSF cube
  header is 43 kB (two reads); a layout walk takes 7 sequential reads, 1.4–2 s from this Mac (the index will hold the offsets).
- v3.0 wavelength tables of D5/D6 are exactly 0.0064 µm above v2.2 (DR1 already on v3.0); node grids are 9×9, 11×11 (D5), 13×20 (D4).
- QR2 flag table: 16 names; v27: 22 names (PHANTOM and REFERENCE gone; CROSSTALK, GHOST*, STREAK, BLOOM, SNOWBALL, HALO added); bit
  numbers agree on every shared name.

Validation (report `docs/validation/2026-11-14-m1-level2.md`):
- **V2 passed:** PSF data byte-identical for D1–D6 in QR2, QR3 and DR1 (first vs last period of each), and QR3 = DR1 for every detector.
- **V1 partial:** the spline reproduces the table nodes (≤ 3e-15 µm); spline − bilinear lookup: median 0.0002–0.0006 µm, max 0.0008–
  0.0059 µm. The comparison with IRSA's own tool is not run (it needs that tool); open.

## Milestones

| Milestone | State | Report |
|---|---|---|
| M0 Foundations | done | this file, 2026-11-14 |
| M1 Level-2 access | done (V1 partly) | this file, 2026-11-14 |
| M2 Exposure index | next | |
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
