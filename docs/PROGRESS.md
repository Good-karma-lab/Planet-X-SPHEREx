# Progress

## State (2026-11-14, evening)

- **Live:** https://spherex.space on a 16 vCPU / 48 GB Oracle VM; main + standby instances behind Caddy (zero-downtime deploys with
  `deploy/push.sh user@host`, which waits until no job runs). The page is served `no-cache`, hashed assets `immutable`.
- **Speed:** all compute runs in a forkserver process pool behind a person-first priority gate (`service/cpu.py`); the API process
  only serves files, small JSON and job state (/health ~1 ms; field opens 2 ms–1.7 s server side). PSF sets are loaded by the workers,
  WCS built once per file, exposure signatures memoised.
- **Lab (approved layout, v2.3):** title / Field / band buttons float over the picture; one time bar (play · Months | Days · Blink |
  Difference | Co-add · timeline · speed · Copy link · Download video); panel tabs Controls · Object · Spectrum · Findings; a one-line
  Scans tray (opens to one line per scan). Searches start by themselves: movers between passes (+ pairs), red sources, spectral
  anomalies, the asteroid search of the middle pass (film search). One marker per object with the measured rate ± error and the Gaia
  verdict; 2′ zoom inset; walls 3 × 2; Days draws the visits with the same renderer (Motion only is a switch); trajectory = the motion
  2000–2050 with year ticks outside the ring; archive images on the timeline with the target's path; portrait in the picture's colours.
- **Searches and data:** film search t4 (4 of 4 known bodies blind on the ecliptic Dec 2025 field, 0 false); pairs; Find Planet X
  (distance search, deep sum, link, jump); catalogue context; comets/asteroids; quick-look spectrum from the DR1 cube; extras
  (CADC deep references, AllWISE/Gaia cloud cross-match, ADES export, SSOIS, Gaia DR4 readiness, tracked stack, R Dra regression).
- **Warm set:** 30 fields and films, then every test field's searches (movers, pairs, red, anomalies, target and anomaly spectra)
  in the background; progress in /health (`warm.searches`).
- **Brown dwarf candidates** (/api/v1/browndwarfs) and **searched places** (/api/v1/searched) are live; the landing page is `/`,
  the globe `#/explore`.
- **Overnight (VM):** `tools/planet_nine_night.py` runs the deep sum (250–800 au, bg=1: every person's job first) on the IRAS/AKARI
  Planet Nine field and the ecliptic field of the 394 au peak; log in `/tmp/planet_nine_night.log` on the VM; results go to the
  searched layer and Findings.

## Checklist status (star-test docs/PROD_CHECKLIST.md), live at 8cbe33a

Done and deployed (commit):
- QA round 5: Copy link / Download video / speed reachable at 800 px (the time bar wraps), NONE draws black and says no band is
  chosen, the phone puts the band buttons under the picture, compact buttons with short band names in small pictures, the passport
  names the object once, the brown-dwarf summary wording — 8cbe33a.
- User list of 18:2x: rail fonts — 0101eab, 37c93c2; frame never jumps (one square in every view) — 56401e4; trajectory as the
  2000–2050 path with year ticks outside the ring — 0101eab; Copy link / Download video away from FIELD — 0101eab; Scans as a one-line
  tray — 0101eab; ε Indi B portrait in the picture's colours — 0101eab; passport opens with name, type and why — 0101eab, 8cbe33a;
  band toggles show the real band pictures without jump or flash — 0101eab; landing at `/`, globe at `#/explore` — 02824c6, b42b9fe;
  globe of real data only, one tone at every zoom — 07e24aa, 312e121.
- Lab: walls 3 × 2 with markers on the stars, floating rows clear of the panels — cb721d0, 56401e4; one marker per object with rate ±
  error and the Gaia tension in σ — e387fd3, 56401e4; 2′ zoom inset, Months | Days, anomaly click → passport, portrait, artifacts —
  e387fd3; Findings tab, square view buttons, one-line scans, Motion only — 25f4a2c; searches start by themselves — 827e9e1, af07bcd;
  archive images on the timeline with path and offset — e5841cc; Difference shows only real changes — e43cf88, 0c0b1fe; numbered track
  badges, click selects — 81e70aa, cc580cb; person-first priority — cb721d0.
- Speed: compute in worker processes — 299049c, ddcd434; Find Planet X in workers — 26e7e5f; film search in short stages — 4177964;
  the page is never served stale — 608dcd2.
- Globe: pins at the backdrop date, path 1950→2050, tiles from IRSA, no idle swaps, Show area epoch, re-entry, cutouts — 60b3d15;
  dust map, comets/asteroids list, searched layer — abec778.
- Searches and data: film search 4/4 blind — 08f4b6c; pairs, Find Planet X, context, known bodies — ea5da88; brown dwarf candidates —
  57c9bb7, 8a9e271, af07bcd; /searched and the sky scan tool — 93d035c, b15e982; Planet Nine prior and 1° fields — def66b8, c9f91f8;
  quick-look cube spectrum — 86de98c; extras — b59072b, 65b84d8.
- Warm set with every test field's searches, target and anomaly spectra — 62ac577, af07bcd. Pages: Method of this service — d156b3c;
  Findings cards, leads, tour, About — 794b8fa, d348017.

Open, with owner (the app pauses this session's messages to the other sessions, so these are the hand-over notes):
- lab-ui / design-review: the landing's "Watch a star move" (`path=1`) should draw Barnard's path on the globe at once; the hero card
  is cut on the right; the film slot waits for the new video; the landing quotes 10.4 / 10.7″/yr while the lab's 5′ track is
  11.4 ± 0.5 on a saturated core, 2.1σ from Gaia — quote the 20′ value (10.7 ± 0.8) or Gaia's.
- sky-service: Find Planet X in workers is slower on 5′ fields (43 s per block) and its ETA does not move; the ε Indi B 20′
  brown-dwarf scan finds nothing (the T dwarf should come out as known); WISE 0855 5′ movers find no track (blend).
- prod-build: Sunday video, slides and texts; Download video not clicked by QA (it writes a file).
- Overnight on the VM: IRAS/AKARI field done, nothing above threshold (best 6.6σ against 6.74σ, ceiling 6.41σ, 250–800 au, passes 1
  and 3); the ecliptic field of the 394 au peak follows.

## Next

1. Remaining items of the user's list (PROD_CHECKLIST "U, 18:2x"): band toggles checked on every combination and target; landing
   page routed as `/` with the globe at `#/explore` once its branch is ready; numbered track badges (search/tracks.ts).
2. 60′ fields report too many red sources / anomalies (49 / 50 on the ecliptic field): review the thresholds at 60′.
3. Sunday: final deploy ≤ 13:00 CET; re-record the video (`web/scripts/demo-video.mjs` then `demo-cut.mjs`; lead with stars and
   brown dwarfs moving between passes and Find Planet X) and the slides; final texts; links from a private window; submit ≤ 15:30.

## State (2026-11-14, afternoon)

- **NASA Space Apps 2026: submission closes Sun 15 Nov 23:59 CET** (team Good Karma Lab, Local Event Barcelona). Drafts in
  `docs/submission/`, requirements with sources in `docs/plan/07-submission-and-pitch.md`. The organisers rule out work before 9:00 local
  on 14 Nov (GH #1981, #2009, #2011, #2057); this repository starts after that.
- **M0, M1, M2: done.** M2: index of 1,628,230 files bootstrapped from IRSA; reconciliation against the bucket exact for every period
  (DR1 1,179,406, QR3 448,824); footprint margin 34.9″ worst of 60 files (all detectors), index margin 60″.
- **M3: validated on real fields** (report `docs/validation/2026-11-14-m3-field.md`): ε Indi B 4.58″/yr toward 122° (catalogue 4.68, 122°;
  rms 0.05″); Barnard's Star 10.08″/yr toward 355° (catalogue 10.39, 356°: 0.3″/yr low, outside the ±0.2 tolerance, open); FWHM 8.8″;
  registration residuals 0.14–0.26″; weight lattice 4.6–10.9 % in thin stacks (open). V1 closed against CWAVE.
- **M4: deployed.** https://spherex.space on an Oracle Cloud VM in Madrid (E5.Flex x86-64, 4 vCPU, 16 GB; an A1 aarch64 VM later),
  Caddy with automatic HTTPS in front, `deploy/provision.sh` + `deploy/push.sh`; a cold Barnard 5′ field builds on the VM from the
  bucket within 90 s; the warm runner fills the 28 fields of the test targets.
- **M5–M10 (core built, deployed):** Lab with one control panel; colour with a white balance on star peaks; walls; Difference; Co-add;
  Sharpen and target marker on; stretch and trim; video download; film of a pass; archive images (DSS2 red, 2MASS Ks, AllWISE W1) as a
  Reference inset and blinked before the seasons. Spectrum on click (PSF photometry in every exposure, streamed; validation star 357 of
  385 exposures, 42 mJy against 39 from J). Identification of any clicked source (SIMBAD, Gaia DR3, distance). Search between passes
  with the Gaia DR3 check and a chance control (Barnard's Star 20′: 10.66″/yr toward 353°, Gaia same motion; ε Indi B: 4.6–4.7″/yr,
  four passes, same motion; chance 0.0 tracks per field). Spectral anomaly map with passports (ε Indi 20′: ε Indi B first, score 13.8
  against 2.6, excess at 3.9–4.3 µm; ecliptic 60′: a Seyfert 2 galaxy first).
- **M6 (built, deployed):** Globe (DSS2 / 2MASS / AllWISE / IRIS tiles through the service, coverage layer, exposure count under the aim,
  catalogue of 4,100 SIMBAD objects); Findings read live from the service; Index, Method and About pages; phone layout.
- **Warm set:** 30 products (the five targets at five sizes, three demo fields, the ecliptic and 3I/ATLAS films); on start every
  product already built is counted before anything is built, so a restart resumes where it was; background work gives way only
  while a person's job waits; deploys wait until no job runs (`deploy/push.sh`, `--now` for a fix).
- **Service robustness:** a standby instance answers while the main one restarts (Caddy prefers the main one, health-checks both);
  the exposure index opens one read connection per thread and the product store locks its queries (shared SQLite connections had
  mixed cursors up: 500s).
- **More searches and checks (built, validated):** known bodies in a film (JPL Small-Body Identification + Horizons; plane counts
  equal to IRSA MOST: 119/107/118/121; visits stacked along the track, chance level beside them); 3I/ATLAS demo film and the CSA
  NEOSSat card (G 12.37, S/N 154 after a 43″ WCS refit on Gaia); red sources (3.8–5 µm against 1.1–1.6 µm; WISE 0855 and ε Indi B
  light up); spectrum stamps and the catalogue check (J 0.99, H 1.02, Ks 1.03, W1 0.985); ZTF and NEOWISE light curves; the
  SPHEREx DR1 all-sky cube on the globe; Gaia DR3 from the public Parquet copy on AWS when the archive does not answer (Gaia release
  switchable for DR4); recall of the movers search by injection (validation note); WiseView-style controls (every setting in the
  address, [ and ] for speed, a dragged box opens a new field, SIMBAD / VizieR / Legacy Survey / IRSA links).
- **Submission:** texts in `docs/submission/` match the deployed tool; 29.6 s demo video and 7-slide deck in `docs/submission/demo/`,
  reproducible with `web/scripts/demo-video.mjs`, `demo-cut.mjs`, `demo-shots.mjs`, `slides-pdf.mjs`.
- **Not built:** the distance slider and searches beyond Neptune, a blind in-pass (film) search for asteroids (known bodies are
  ringed and measured instead), pairs changing in place, the quick-look spectrum from the HiPS cube (a FITS tile is 320 MB), deep
  static references from CADC (1′ cutouts do not fit 5–60′ fields), synthetic injection in the Lab (the service-side test exists).

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
- The API runs on port 8780 (dev) and 8781 (e2e); port 8770 is never used.

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

## M2 — Exposure index (built; bootstrap running)

Decision (reported to the supervisor): the index is bootstrapped from IRSA's TAP service, one query per day of the survey
(`spherex.artifact` ⨝ `spherex.plane`: S3 path, size, MD5, detector, time span, 4-corner footprint), instead of crawling ~1.6 M headers
(~9 h, ~50 GB). Files IRSA does not list yet are found by the bucket reconciliation; exact offsets, MJD-AVG and the spacecraft position
come from each file's header when it is first used (cached).
- `indexer/irsa.py` (TAP by day, EWKB polygons, path → S3 key), `indexer/store.py` (SQLite with an R*Tree over bounding boxes, exact
  spherical overlap in numpy, RA 0/360 and poles), `indexer/build.py` (resumable `bootstrap`, `reconcile` against the bucket, `query`).
- Archive (2026-11-14): `spherex.artifact` = 1,628,318 files = DR1 (945,725 + deep 233,681) + QR3 (362,124 + deep 86,700); DR1 covers MJD
  60789.75–61174.49 and QR3 61174.51–61318.0 (no overlap, no gap); deep files are separate files in the same period folders.
- Footprint margin: the real SIP edge bows at most 35″ outside IRSA's 4-corner polygon (24 random files, all detectors); the index adds
  60″ to every overlap test.
- Bootstrap rate from this Mac: ~5 s per day with 3 parallel queries (~48 min for the survey).
- Tests: 10 (WKB, CSV parsing, overlap inside/at the edge/outside, RA 0, poles, time order, resumable days, box cover).

## M3 — Field engine (built; validation pending)

- `engine/`: `passes` (visits, passes 60/90 days, CDET), `grid` (north-up tangent plane, one orientation), `clean` (clipped median,
  40 wavelength bins from 10′, masks; transient-type flags masked only farther than 6 px from a bright core), `stack` (bilinear splat,
  σ 0.35/0.50 by count, online leave-one-out test), `visit_test` (≤ 12′), `halo`, `register` (iterative Gaussian-weighted centroids),
  `colour` (`lam_rgb`, 3×6 matrix, white balance), `diff` (z calibrated on empty sky, Stouffer), `centroid` (Moffat from the wings), `field`
  (map-reduce over (pass, detector): detector and band stacks, saturated share over CDET, flag census; quick subset).
- `service/workers/native.py` (one exposure inside a field), `fieldbuild.py` (index → passes → per-detector groups → register on pass 0).
- First real runs (Barnard's Star, pass of Aug–Sep 2025, from this Mac): 5′: 134 exposures, D1–D6, cosmic-ray pixels 29 (D6 18),
  per-visit pixels 454, FWHM 9.0″, 5.4 s compute warm. 20′: 149 exposures, 31 s cold (24 s reading 241 MB), 6.9 s compute, peak RSS 1.1 GB,
  FWHM 8.8″ (105 stars). Fixed on the way: a blue-grey ring around the saturated core (transient-type flags were masked at the core).
- Engine tests: splat sharper than interpolate-then-average (5.9 vs 6.4 grid px), flux kept, spike rejected and star cores kept, a hit
  shielded by a neighbouring hit still rejected, three synthetic passes give the injected motion within 0.25 px, z width 1 on noise,
  registration recovers a shift, halo rule, saturated zones, Moffat centre from the wings.

## M4 — Service core (parts without the VM built)

- `service/products/` (SXP1 format; store with versioned keys and lineage, atomic parts, SQLite catalogue, purge of stale versions),
  `service/jobs/scheduler.py` (P0–P3, pause at checkpoints while a person is active, attach by key, abandon and re-attach, cap on
  abandoned jobs, light/heavy slots, memory admission), `service/workers/fieldjob.py` (parts per pass: picture, dets, bands, flags; diff;
  meta), `service/app.py` (`/api/v1/health`, `/fields`, `/jobs/{id}`, `/fields/{key}/parts/{name}`; polling free; 60 new jobs per 10 min per
  client), `service/warm.py` (warm set from `data/warm.json`), `deploy/` (provisioning, units, Tunnel, R2 snapshot/restore).
- Tests: scheduler 6, products 3, SXP1 2, warm 3, a contract test of the whole field life cycle on synthetic files through a fake bucket.
- Decision: job state is in memory, results are durable in the product store (a restart re-attaches to complete products or recomputes).
- Deployed 2026-11-14: Madrid VM, Caddy (HTTPS), code by `deploy/push.sh`, index copied as a consistent snapshot.
- Not yet: the quick part, measurements of the cloud gate on the VM.

## Milestones

| Milestone | State | Report |
|---|---|---|
| M0 Foundations | done | this file, 2026-11-14 |
| M1 Level-2 access | done (V1 partly) | this file, 2026-11-14 |
| M2 Exposure index | done | this file, `docs/validation/2026-11-14-m2-index.md` |
| M3 Field engine | done except Barnard's motion (−0.3″/yr) and the thin-stack lattice | `docs/validation/2026-11-14-m3-field.md` |
| M4 Service core, cloud gate | deployed at https://spherex.space (Madrid VM, Caddy) | `deploy/README.md` |
| M5 Lab picture | in progress | this file |
| M6 Globe | in progress | this file |
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
