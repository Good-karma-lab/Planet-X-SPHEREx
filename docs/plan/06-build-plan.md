# 6. Build plan

Milestones in order. Each ends with: tests green; the milestone's validation rows (section 2.7) reproduced and written into a dated
report; end-to-end checks in a real browser by clicking where there is a UI; `docs/PROGRESS.md` updated; commit and push; a short report
to the supervisor. Small commits throughout. The walking skeleton is deployed early (M4) so that the cloud's real speed shapes the rest.

## M0 — Foundations

- **Build:** repository layout (5.2); Python project with `uv`, ruff, mypy, pytest; web project with Vite, TypeScript, Vitest,
  Playwright; GitHub Actions (lint, type check, unit tests, web build; arm64 and amd64); `docs/PROGRESS.md`; decision records
  (`docs/decisions/`); `data/bands.json`, filter curves, test-target snapshot.
- **Test:** CI runs on push; an empty API answers `/api/v1/health`; the web shell builds and loads.
- **Done:** CI green on `main`; a newcomer can run `make dev` and see the empty app.

## M1 — Level-2 access

- **Build:** `engine/spx` (header chain from bytes, flag table, celestial WCS with SIP, wavelength WCS with the D5/D6 v3.0 offset, PSF
  formats: QR2 cube integrated over the pixel, QR3 EPSF); `service/reader` (async range reader, row bands of 64 rows, compressed FLAGS
  tiles, window cache, retries, stall handling, breaker); PSF provider with the identity check.
- **Test:** unit tests on synthetic FITS of both releases (built by the test suite, including compressed FLAGS); recorded real headers
  as fixtures; validation V1 (wavelength vs IRSA) and V2 (PSF identity) on live files.
- **Done:** reading the rows of a 5′ box from any QR2 or QR3 file gives native pixels, full flag word, sky position and wavelength of
  every pixel; V1, V2 pass.

## M2 — Exposure index

- **Build:** `indexer/`: list periods, read headers, footprints (12 vertices), HEALPix shards (columnar, immutable per period), query by
  polygon overlap, coverage map, daily follow, IRSA ObsCore reconciliation, SIA2 fallback for unindexed periods, snapshot/restore to R2.
- **Test:** unit tests (footprint geometry, shard read/write, overlap at poles and RA 0/360); a partial build of a few periods; V3.
- **Done:** a full index built (≈ 9 h first crawl, run in the background on the VM or a laptop and published to R2); queries < 50 ms
  p99; V3 exact for the snapshot.

## M3 — The field engine

- **Build:** background (clipped median, 40 wavelength bins from 10′), masks, splat with the 0.35/0.50 rule, streaming per-pass build
  with the online leave-one-out test, per-visit test (≤ 12′), halo map, saturated share, registration on pass 0, white frame over
  CDET, colour matrix, named-band stacks, quick part, flag census, Difference z maps with calibration, thinning to 800 exposures.
- **Test:** unit tests of 5.11 (synthetic); equivalence with an in-memory reference build (V13); validation V4–V12 on Barnard's Star and
  ε Indi B; timings measured cold/warm and split into reading and computing; the same run in a container limited to 2 CPUs / 12 GB.
- **Done:** V4–V13 pass; timings written down, including 2-CPU numbers (input to the cloud gate).

## M4 — Service core and the walking skeleton in the cloud (cloud gate)

- **Build:** API v1 (fields, jobs, parts, health), SQLite job table, scheduler with P0–P3 classes, worker processes with checkpoints and
  pause, abandon/attach, memory admission, product store (versioned keys, dependency graph, atomic writes, purge on bump), `SXP1` parts,
  rate limits and budgets, signed background marker, warm runner (fields only for now), structured logs; deploy scripts; first
  deployment to the Oracle A1 VM behind a Cloudflare Tunnel (needs the user's account — open question Q1).
- **Test:** contract tests with the fake S3 (lifecycle, priorities, abandon/attach, purge-and-rewarm in a temporary store, polling free,
  immutable only when complete); on the VM: cold and warm field times for the five targets at 5′/20′/60′; S3 throughput from Ashburn.
- **Done:** a field of any test target is computed on the VM and its parts served through Cloudflare; **cloud gate decided** with
  numbers: stay on 2 OCPU, or move to the budget fallback; warm-set field parts complete on the VM.

## M5 — The lab picture (the first thing a visitor sees)

- **Build:** web app shell, router and address state; WebGL2 renderer (pendulum, colour with per-pass balance, named bands, Co-add,
  wall of bands, wall of detectors with agreement, Difference, artifact mask with hover, speed rings, zoom inset, Follow, saturated
  white, no-data grey/black); timeline with clickable dates; Controls tab with the open Display grid; status line with phases; video
  export; frame-audit harness (5.11) and its baselines.
- **Test:** Playwright by clicking (sizes without reload, every chip, every view, back again; zero requests on view switches; console
  clean; phone width); frame audit on every pass × target × size × view; V14–V17, V40, V41.
- **Done:** all checks pass; **discovery test rows 1 and 2: yes** (Barnard's Star swings; ε Indi B the orange-red dot), screenshots in
  the report.

## M6 — The globe

- **Build:** WebGL globe and Mollweide map; HiPS tiles through `/hips` (DSS2, 2MASS, WISE; dust map local), coverage layer, search by
  name (local snapshot of the five targets first, then SIMBAD), pins at the date of the background image, the selected object's path
  1950 → 2050, Open → Lab by coordinates at epoch 2025.9.
- **Test:** e2e: each target found, pinned on the right image date (Barnard's Star inside its ring on DSS2/2MASS/WISE), Open hits the
  warm key (same address as the warm runner); a large zoom step never shows a black globe.
- **Done:** globe → lab for every target in < 2 s warm.

## M7 — Sources, identification, the Object card, archive images

- **Build:** source list product; click selection (S/N ≥ 7 or saturated, 14 px), ring in every pass and view; `identify` and `context`
  with explicit states; Object card (values only, tiers, distance rule, "In the pictures"); archive images job (DSS, 2MASS, WISE, PS1;
  reprojection; dates) and their place on the timeline with marker, trajectory, speed rings, camera on the object, playback.
- **Test:** V18; e2e: click on a star selects it in Picture and both walls at every size; empty sky says so; the selection survives view
  and size changes; Barnard's Star across archive frames (1950 place 13′ south said in status).
- **Done:** every click gives a card in < 1 s warm (name when a catalogue answers, honest state when not).

## M8 — Spectra

- **Build:** PSF photometry engine; live spectrum job (points with a cursor, stamps), Spectrum tab (chart from the first moment, points
  one by one, replay, slider, stamps); catalogue check (synthetic photometry); binned spectrum with controls and upper limits;
  saturated-source rule; change test at matched wavelengths; portrait with saturated map (white core); on − off for movers; thermal
  ratio.
- **Test:** unit tests (injected flux recovered; both PSF formats); V19–V23; e2e on the five targets (saturated ones say so once; WISE
  0855 arrows; ε Indi B peaks).
- **Done:** V19–V23 pass; first points within 3 s on a warm field.

## M9 — Search between passes, pairs, chance

- **Build:** search between passes on the white stacks (sources, halo contrast, saturated zones, tracks 3–120″/yr), Gaia check states,
  blend test, pairs with the real-pair rule, weak rule, **vectorised chance control (< 5 s per field)** and the pair chance estimate;
  Movers row line "between passes"; lead and pair cards; rings drawn only for found tracks.
- **Test:** unit (injected movers found, shuffled data clean); V24–V27; e2e: Barnard's Star by coordinates gets one ring and nothing
  else; RW Aurigae shows the pair, no mover.
- **Done:** V24–V27 pass; every drawn track carries its check state and chance number.

## M10 — The film of one pass and its search

- **Build:** film product (grid rule incl. 6″ at 60′ under 80 MB, visit medians, alignment, motion maps, static and halo masks), film
  view (sky by default, found bodies ringed, Trail, Blink, Show candidates, Motion only, film around a track), candidates, on − off
  verification, orbit linking with recovery, straight-line fallback, scrambled control, SkyBoT check; Movers line "inside a pass".
- **Test:** V28–V30; the R Draconis 60′ unexplained track investigated and reported honestly; frame audit of the film frames; discovery
  test row 3 with the new film aids.
- **Done:** 4 of 4 known bodies on the ecliptic field with 0 false tracks; discovery row 3 answered honestly with what was tried.

## M11 — The search inside a visit (hours)

- **Build:** per-visit detections, star removal, linker with burst consistency, proof rules, companion removal, far-member drop,
  parallax trial distances, known bodies (SkyBoT, Horizons from the spacecraft), permuted-time chance (< 1 %), stack along an
  ephemeris for a named body; the film of one visit; Movers line "inside a visit".
- **Test:** V31, V32 (gate: ≥ 7 of 8 known bodies before it starts by itself; until then it runs on request and says "experimental").
- **Done:** gate passed and the search starts by itself for fields ≤ 10′.

## M12 — Anomaly map and passport

- **Build:** anomaly job (sources ≥ 12 S/N + tracks, all exposures, 24 bins, family of two components, robust threshold, residuals and
  drivers), the button with smooth progress, rings above threshold only, the passport with context.
- **Test:** V33; e2e on ε Indi 20′ (ε Indi B Anomaly 1 with its drivers).
- **Done:** V33 passes.

## M13 — Bodies beyond Neptune

- **Build:** shared visit-difference products (D1–D3, D4 separate); one-pass scan with the hypothesis estimate and automatic nearest
  distance; refinement and photometry; control tracks; the distance slider (WebGL shift-and-add) with "Show the noise"; deep sum with
  plan-and-estimate, two-level scan in worker processes, photometry of 100 + 100 peaks with cached reference measurements; link of
  passes; jump corridor (built and tested before offering); D4 with the Planet Nine prior and the injection test; WISE archive check on
  request; honest wording lines.
- **Test:** V34–V39; discovery test rows 4 and 5.
- **Done:** Eris found blind in 3 of 3 passes and in the deep sum; Sedna the one lead of its deep sum; the candidate at 394 au ruled out
  by the WISE check through the service.

## M14 — Findings, Method page, the card for four kinds of finds

- **Build:** Findings page (test-target cards from live answers and the validation report; leads kept in the browser with
  "Check known bodies" and "Copy report"); one card layout for the four kinds of finds; Method page generated from constants, texts and
  the validation report, with status table and change log.
- **Test:** e2e; a CI test that the Method page's parameters equal the engine's constants and that every version bump has a log entry.
- **Done:** every number on Findings and Method traces to code or a report.

## M15 — Hardening

- **Build:** 60′ tiled pyramid; R2 fallback for immutable parts; load test fixes; observability alerts; security review (inputs,
  limits, headers, admin); backups verified by a restore on a fresh VM; documentation (`README`, `docs/api.md`, operations guide).
- **Test:** load tests (5.11); restore drill (fresh VM warm from R2 in minutes); full frame audit; full e2e matrix on the cloud.
- **Done:** p95 warm open < 2 s with 20 concurrent visitors; restore drill passes; no open security finding.

## M16 — Demo readiness

- **Build:** demo script; a final pass on plain words and status lines; the warm set complete and snapshotted; uptime monitor.
- **Test:** the discovery test (all rows) by a person; a rehearsal clicking through the demo on the production URL from a cold browser.
- **Done:** the demo never waits.

## 6.1 Risks

| Risk | Effect | Mitigation |
|---|---|---|
| 2 OCPU too slow for cold 20′–60′ fields; no A1 capacity | cold fields take many minutes; deployment blocked | measured at the M4 gate; warm set covers the demo; budget fallback VM (15–40 USD/month); compute hot loops in Numba |
| Oracle reclaims or loses the VM | outage | pay-as-you-go account; R2 snapshot; scripted provisioning; restore drill |
| External services down (IRSA, VizieR, NED were down 2026-11-13/14) | catalogue parts and archive images missing | every part says "did not answer"; long caches; mirrors; the pictures and spectra need only S3 |
| IRSA catalogue lag | exposure lists incomplete via IRSA | own index from the bucket; IRSA only for reconciliation and fallback |
| Payload size at 60′ (hundreds of MB) | slow on mobile, egress | Picture first, other parts on demand, tiled pyramid, edge cache |
| A display change looks good on one frame and breaks others | false "discoveries", lost trust | the frame audit is a gate |
| A search's false tracks | misleading leads | chance control per search; checks after the search; leads not discoveries; validation gates (M11 ≥ 7 of 8) |
| Memory spikes (60′ fields, deep sums) | OOM on 12 GB | memory admission; streaming builds; process recycling |
| Version bumps purge the warm set right before a demo | the demo waits | automatic re-warm; `/health` warm line; freeze product versions before the demo; R2 snapshot of the previous warm set |
| Free-tier terms change | cost or loss of service | check on sign-up day; documented fallback |
| Research numbers not reproduced exactly (full files vs the research's cutouts; new pipeline versions) | doubt | each mismatch investigated and explained in the validation report before moving on |

## 6.2 Open questions and decisions

1. **Q1 — cloud account. Open (the user's).** The Oracle Cloud (and Cloudflare, R2) sign-up with the card check is the user's; a
   domain or a Cloudflare-provided hostname is also their choice. Until it is answered, M0–M3 and the parts of M4 that need no VM are
   built; the deployment part of M4 waits.
2. **Q2 — frontend framework. Decided (2026-11-14):** Preact + TypeScript.
3. **Q3 — Sharpen. Decided (2026-11-14):** kept (the user liked it and wanted its faults fixed). Off until the production frame audit
   (5.11) passes on every frame, then **on by default** (2.3.9).
4. **Q4 — demo fields. Decided (2026-11-14):** the ecliptic field, the Eris field and Sedna's fields are demo fields (rows of the
   discovery test), not test targets in the interface; they are part of the warm set.
5. **Q5 — R Draconis 800-exposure cap. Decided (2026-11-14):** even thinning with a status line; revisit after the cloud gate.
6. **Q6 — search inside a visit. Decided (2026-11-14):** fields ≤ 10′; off by default until it finds ≥ 7 of 8 known bodies (M11 gate).
7. **Q7 — public API. Decided (2026-11-14):** internal for now; `/api/v1` kept clean and typed so it can be documented later.
