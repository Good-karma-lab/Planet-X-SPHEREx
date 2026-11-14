# 5. Architecture

## 5.1 Overview

```
                  Browser (static app: TypeScript, WebGL2)
                        │  HTTPS
                        ▼
        ┌──────────── Cloudflare ─────────────┐     R2 bucket
        │ CDN: static files, immutable        │◄──── snapshot of index + warm-set products
        │ product parts · TLS · rate rules ·  │      (restore a VM; serve warm parts if origin down)
        │ Access (admin)                       │
        └───────────────┬─────────────────────┘
                        │ Cloudflare Tunnel (outbound only, no open port)
                        ▼
   ┌──────────────────── one VM (Oracle A1 Ashburn, 2 OCPU / 12 GB / 200 GB) ───────────────────────┐
   │  api (uvicorn, async)  ── jobs table ──  scheduler ──►  worker processes (compute, N = cores)   │
   │     │   ▲  polls / parts                       │             │  engine (numpy, scipy, numba)   │
   │     │   └──────── product store (files + SQLite catalogue, versioned, atomic) ◄───┘           │
   │     ├── reader pool (async HTTP range reads) ──► S3 nasa-irsa-spherex (us-east-1, same region) │
   │     ├── upstream clients (breakers, budgets) ──► IRSA, Gaia, SIMBAD, SkyBoT, JPL, STScI, …     │
   │  indexer (timer, hourly) ──► index shards      warm runner (lowest priority)   snapshotter     │
   └───────────────────────────────────────────────────────────────────────────────────────────────┘
```

One VM runs everything; the design keeps the pieces separable (api, workers, indexer are separate processes talking through the
product store and a small SQLite database), so a second VM for workers can be added without redesign.

## 5.2 Repository layout

```
engine/                 pure algorithms: arrays in, arrays out; no HTTP, no files, no clocks
  spx/                  Level-2 parsing helpers (header chain, flag table, WCS/wave, PSF formats) that work on bytes
  grid.py stack.py clean.py halo.py colour.py bands.py diff.py census.py passes.py
  photometry.py portrait.py sources.py search_passes.py pairs.py film.py orbit.py visit_search.py
  distant.py deepsum.py link.py anomaly.py chance.py thermal.py constants.py
service/
  app.py                HTTP API (Starlette/FastAPI), request validation, rate limits
  jobs/                 job table, scheduler, priorities, budgets, worker protocol
  products/             product store, versions and dependency graph, purge, warm set, snapshots
  reader/               S3 range reader, row-band cache, window cache
  upstream/             one client per external service (breaker, timeout, cache, mirrors)
  workers/              job implementations calling engine/ (field, spectrum, search, film, …)
indexer/                exposure index builder and reader (shards, footprints, reconciliation)
web/                    browser app (Vite + TypeScript): globe, lab, findings, method; WebGL renderer
data/                   bands.json, filter curves, test-target snapshot, method texts
validation/             the validation suite (real data) and its report generator
tests/                  unit and contract tests (Python); web/tests (unit + Playwright e2e + frame audit)
deploy/                 provisioning script, systemd units, cloudflared config, R2 snapshot scripts
docs/                   plan/, PROGRESS.md, decisions/ (ADRs), validation/ (dated reports), api.md
```

## 5.3 Technology choices

| Area | Choice | Why |
|---|---|---|
| Language (server) | Python 3.12 | astropy/numpy/scipy are the base of the method; every algorithm was measured in Python |
| Hot loops | NumPy vectorisation first; Numba (aarch64 supported) for splat/leave-one-out, linkers and the chance controls | the research's slowest parts were Python loops (chance control 2–2000 s per field); target < 5 s |
| HTTP | Starlette (via FastAPI for typed contracts and OpenAPI) on uvicorn, one process, async | many concurrent polls and streams with few threads; contracts documented and tested |
| Compute | a pool of worker **processes** (spawned, recycled after N jobs or above a memory mark) | no GIL contention; memory returned to the OS when a process exits (the research needed a macOS allocator trick); a crash kills one job, not the service |
| Reads | async HTTP client with a bounded connection pool, range GETs; row bands cached as compressed files | link-bound work needs concurrency, not threads |
| State | SQLite (WAL) for jobs, product catalogue, rate buckets, upstream cache index; files for products | survives restarts; one file; no server to run |
| Browser | TypeScript, Vite build, Preact for panels, hand-written WebGL2 renderer, no other runtime dependencies | typed parsers and shaders, small bundle, fast load |
| Tests | pytest + hypothesis (engine), contract tests with a fake S3, Vitest (web), Playwright (Chromium with WebGL) | see 5.11 |
| Packaging | `uv` lock file; one container image (arm64 + amd64) built in CI; also runnable without containers | reproducible on the VM and on a laptop |

## 5.4 Scheduling: a person first

**Priority classes** (strict order; lower classes yield):

| Class | What | Examples |
|---|---|---|
| P0 interactive | a job a person is watching now | the field on screen, a clicked spectrum, identify, context |
| P1 person's background | a job a person started and left, or a search started for their field | abandoned field, the searches after a field, the film, anomaly map, distant scan |
| P2 warm | the warm runner | the 25 + named warm fields and their products |
| P3 maintenance | indexer, snapshots, eviction | daily index update, R2 snapshot |

**Budgets** (all configurable, set by measurement at the cloud gate):

- **CPU:** worker processes = number of cores; P0 always gets a worker (a P2/P3 job is paused at its next checkpoint — every job has
  checkpoints between passes, between exposures in long loops, between hypotheses blocks — and resumes later; paused time does not
  count against a deadline).
- **Reads:** a global S3 concurrency budget (e.g. 64 in flight) split by class: P0 takes what it needs, abandoned jobs share 3, the
  warm runner 1 while any person is active (a person = a request without the background marker in the last 60 s), all when idle.
- **Memory admission:** each job declares an estimate (a 60′ field ~1.1 GB peak, a deep sum ~0.6 GB per process); the scheduler starts
  a job only if the estimate fits under the memory budget (12 GB VM: ~9 GB for workers); otherwise it waits with the phase "waiting for
  memory".
- **Queues:** small jobs (spectrum, identify, archive) never wait behind heavy ones: separate slots (person light 8, heavy 4;
  background light 4, heavy 2). A job beyond the slots **waits** with a visible phase; it is never refused with 429 for being polled.
- **Abandon, not cancel:** a field whose last watcher left keeps running at P1; more than 3 abandoned field jobs → the oldest is
  cancelled; a person who comes back attaches to the running job and sees its progress continue.
- **The background marker** is set only by our own runners (a header signed with a server-side secret), so a client cannot pose as
  background or as a person.

## 5.5 Job model and keys

- A job is `(kind, canonical parameters)`. Canonicalisation rounds coordinates the way the address writes them (RA 0.01 s, Dec 0.1″),
  so the globe, the warm runner and a pasted link produce the same key.
- **Key** = hash of: kind, canonical parameters, the product's own version, the versions of the products it is computed from (declared
  dependency graph), and for exposure-based products the **index cell versions** it read (so a new period changes only the keys of the
  cells it touches, and the answer says which index snapshot it used).
- States: `queued → running ⇄ paused → done | error | cancelled`, plus `abandoned` (running at P1). A job record holds `phase` (words
  shown to the person), `done/total`, timings, the parts ready so far, warnings ("12 of 603 exposures could not be read").
- **Progressive results:** a job publishes parts as they are finished (quick part; picture after each pass; points of a spectrum one by
  one with a cursor). The client polls `GET /jobs/{id}?since=…` every 0.5–1 s; polling is free (not charged to rate limits).
- Errors keep the job record with the reason; a re-run after an error is allowed after 20 s; a partial result (some exposures missing)
  is marked partial and re-run when asked or by the warm runner.
- Deadlines: by size (a 60′ field: minutes); a stalled read never blocks a job (40 s stall → missing).

## 5.6 Products, versions, cache and the warm set

- **Product store:** `store/products/<kind>/<version>/<key[:2]>/<key>/` with `meta.json` and part files; written to a temporary name
  and renamed (atomic) — every write, no exceptions. The SQLite catalogue records key, kind, version, size, created, last access,
  `pinned` (warm set), `complete`.
- **Raw caches** (separate cap): `store/raw/` row bands, `store/win/` field windows, `store/up/` upstream answers (by service). Subfolders
  by hash prefix (never one flat directory of 300 thousand files). Eviction: LRU by bytes per area; pinned products never evicted.
- **Versions:** one constant per product kind (field, sources, search, pairs, film, visit search, spectrum, portrait, anomaly, distant,
  deep, archive, coverage, identify/context); a dependency graph in code (e.g. sources ← field; search ← field; anomaly ← sources,
  search). A CI test fails if a version changes without a change-log entry on the Method page, or if a product's code changed (hash of
  its module and constants) without a version bump.
- **On start:** compare the registered versions with the store; stale versions are purged in the background (whole version folders: one
  `rm` each); the warm runner is triggered whenever a version went up or the warm set is incomplete.
- **Warm set** (declared in `data/warm.yaml`): the five test targets at 2′, 5′, 10′, 20′, 60′ (by coordinates at epoch 2025.9) with every
  part, sources, search, pairs, film, archive images, coverage, the targets' spectra; the 20 by-name addresses; the demo fields (ecliptic
  60′ December 2025 film, Eris 18′ one-pass scan and deep sum, Sedna's fields). `GET /health` shows `warm: n of m` and what is missing.
- **Snapshot:** after the warm set completes (and after each daily index update), the index and the pinned products are synced to R2
  (only new immutable files are uploaded). A new or reclaimed VM restores from R2 before it serves, so it is warm in minutes.

## 5.7 Payloads

- **One binary container format (`SXP1`)** for every array part: magic, header length, JSON header (shape, dtype, layer names, grid:
  centre, step, orientation, pass list), padding to 8 bytes, then the arrays (float16, uint8, float32), little-endian. **One
  orientation everywhere:** row 0 = north, column 0 = east-left as displayed (the research had two conventions; one is enough).
- **Parts per field:** `quick`, `picture` (R, G, B float16 + validity + saturated share, per pass blocks), `dets`, `bands`, `diff`,
  `flags`. The page loads `quick` then `picture`; walls, bands, Difference and the mask only when opened (all at once up to 10′).
- **Size at 60′:** the research parts were 245–490 MB per 60′ field (Picture 33 MB for 3 passes). Production keeps float16 (no
  quantisation of real values) but adds a **tiled pyramid for 60′**: an overview at half resolution (≈ 8 MB) loads first; full-resolution
  tiles of 256² load for the area on screen when zoomed. Brotli/gzip stored next to each part. Every part is immutable once complete.
- **Immutable means complete:** a response is marked `immutable` only when its product can never change; a record that can still be
  completed (a search waiting for a Gaia check) is `no-store` until final (the research served a later-completed record as immutable and
  the browser kept the old one).

## 5.8 API (v1)

All under `/api/v1`. GET creates-or-attaches for idempotent jobs; every job answer is the job view. JSON unless noted.

| Method & path | Returns |
|---|---|
| `GET /health` | ok, version, product versions, index snapshot, queue, workers, memory, disk, warm n/m, breakers |
| `GET /resolve?name=` | SIMBAD/Sesame resolution (test targets from the local snapshot first) |
| `GET /coverage/map?order=` · `GET /coverage?ra&dec&fov` | HEALPix coverage; exposure count of a field |
| `GET /hips/{survey}/{order}/{npix}` | globe tile (proxy, immutable) |
| `GET /fields?ra&dec&fov[&pass…]` | job view with `key`, parts ready, passes, grid, phase |
| `GET /jobs/{id}?since=` | job view (polling; free) |
| `GET /fields/{key}/parts/{part}[?tile=z/x/y]` | `SXP1` binary (immutable when complete) |
| `GET /fields/{key}/sources` | blind source list |
| `GET /fields/{key}/search` | tracks with checks, rejected counts, pairs, weak counts, chance numbers |
| `GET /fields/{key}/film?pass=` · `…/film/data` | film job view; frames (`SXP1`: sky + motion uint8) |
| `GET /fields/{key}/visit-movers` | search inside a visit (≤ 10′) |
| `GET /fields/{key}/anomalies` | anomaly map job (scores, residuals, drivers) |
| `GET /fields/{key}/distant?pass&dmin&dmax&det` · `…/distant/data` | one-pass scan; slider data |
| `GET /deep/plan?…` · `GET /deep?…` | hypothesis count and time estimate; the deep-sum job |
| `GET /spectrum?ra&dec[&pmra&pmdec&plx&epoch][&track=]&since=` | live spectrum job (points with cursor, result, catalogue check, binned, portrait) |
| `GET /spectrum/{key}/stamps?from&to` | stamps (`SXP1` records) |
| `GET /identify?ra&dec&epoch` · `GET /context?ra&dec&epoch` | identification; context parts with states |
| `GET /archive?ra&dec&fov` · `…/archive/{key}/data` | archive-images job; reprojected images |
| `GET /smallbodies/cone?mjd&ra&dec&r` · `GET /smallbodies/ephemeris?target&times` · `GET /smallbodies/{id}` | SkyBoT, Horizons, SBDB (cached, rate-limited) |
| `POST /client-log` | browser errors (rate-limited, no personal data) |
| `/admin/*` | purge, warm, snapshot, metrics — behind Cloudflare Access and a token |

The API contract is documented in `docs/api.md` (generated from the typed models) and tested by contract tests.

## 5.9 The browser app

- **Router and state:** hash routes `#/globe`, `#/lab?…`, `#/findings`, `#/method?sec=`; the lab state lives in one store mirrored to the
  address (replace-state on every change).
- **Renderer (WebGL2):** stacks as `R16F` `TEXTURE_2D_ARRAY` (linear filtering is core in WebGL2; `R32F` would need an extension some
  devices lack); validity and saturated share as `R8`; Difference as `R8` z; one fragment shader with modes (white, colour, band blend,
  wall panels, Difference, artifact mask), the display law, per-pass white balance, the pendulum crossfade, the optional sharpening.
  Overlays (rings, paths, labels, speed rings) on a 2D canvas above, through one label queue. The canvas is always square and centred.
- **Data layer:** part loaders that parse `SXP1` into typed arrays and upload with `texSubImage3D` per pass block as parts arrive; each
  part fetched once per page (a Map of promises); no request on view, band, tab or pass change.
- **Charts:** the spectrum chart is a hand-written canvas component (points appear one by one; replay; slider).
- **Export:** video recorded from the WebGL canvas (`captureStream` + `MediaRecorder`, MP4 where supported, else WebM) with the credit
  line burnt in; the exported file is checked to play in tests.
- **Method page:** generated at build time from `engine/constants.py` (exported to JSON), `data/method/*.md` texts and the latest
  validation report, so parameters and numbers on the page are the code's.

## 5.10 Deployment

- **Target:** Oracle Cloud Always Free Ampere A1 in US East (Ashburn) — 2 OCPU, 12 GB, 200 GB block volume — next to the bucket's
  region; Ubuntu 24.04 aarch64; the account upgraded to pay-as-you-go (no charge within the allowance) to avoid idle reclamation.
- **Provisioning:** `deploy/provision.sh` (idempotent): users, firewall (no inbound except SSH from the admin address), unattended
  security upgrades, `uv`, the app, systemd units `sky-api`, `sky-workers`, `sky-indexer.timer`, `sky-snapshot.timer`, `cloudflared`.
- **Front:** Cloudflare DNS + Tunnel (no open HTTP port on the VM), cache rules: static assets and `/api/v1/fields/*/parts/*` with
  `immutable` cached at the edge; everything else passes through. HTTPS by Cloudflare.
- **Release:** CI builds and tests on every push; a tagged release deploys over SSH (pull, `uv sync`, apply schema updates, restart with drain of 25
  s); a version bump of a product triggers purge + re-warm automatically; the warm state is visible in `/health` before a demo.
- **Disk plan (200 GB):** index 1 GB, products ~20 GB (warm set ~7–10 GB), raw row bands capped at 100 GB, upstream caches 5 GB, logs
  2 GB, free margin.
- **Demo insurance:** R2 holds the index and the warm-set products; the client can fetch an immutable part from the R2 public endpoint
  if the origin does not answer (same paths), so the five targets open even while the VM restarts.
- **Fallback:** if the cloud gate (M4) shows 2 OCPU is too slow for cold fields or A1 capacity is unavailable: a 4-core/16 GB ARM VM
  in or next to us-east-1 (~15–40 USD/month), same scripts.

## 5.11 Testing strategy

| Level | What | Where / when |
|---|---|---|
| Unit (engine) | every algorithm on synthetic exposures: a dithered point source stacks sharper than resample-then-average; an injected single-exposure spike is rejected and a star core is not; streaks of one visit removed, an asteroid in one visit kept; halo rule keeps a source in a halo; z of Difference has width 1 on noise; PSF fit returns the injected flux; orbit model reproduces given ephemerides to 0.01″; the linkers find injected tracks and nothing in shuffled data; property tests on grid/WCS round trips | CI, every push (< 2 min) |
| Contract (service) | API answers and job lifecycles against a fake S3 serving synthetic Level-2 files (both releases, compressed FLAGS); priorities, abandon/attach, purge-and-rewarm on a version bump in a temporary store, polling is free, immutable only when complete | CI, every push |
| Validation (real data) | the table of section 2.7 against the live archive; dated report in `docs/validation/` | on demand, at each milestone end, nightly on the server |
| Web unit | `SXP1` parser, display law, address state, label queue | CI |
| End-to-end (real browser) | Playwright in Chromium with WebGL against a running service (local with a recorded data subset in CI; the real server before a release): open each test target from the globe; change size with every button **without reloading**, every band chip, every view and time mode, then back; click a star → ring in every view and the Object card; spectrum builds point by point; Marker off removes every mark; switching views makes **zero** network requests; console free of errors; failed requests listed; phone width has no horizontal overflow; the exported video plays | CI (subset), before every release (full) |
| Frame audit (visual) | for the five targets × every size × every pass × every view (and the loading stage, and key display options off/on): render each frame offscreen through a test hook, compute metrics — saturated cores white (no dark pixel inside a saturated zone), isolated red/bright cells, mean colour per channel, contour rays around bright stars, no-data grey only outside coverage, FWHM of isolated stars — and compare with approved baselines (perceptual diff); a change to the look needs the whole audit green and a person's look at the contact sheet | before any change of the look becomes default; before releases |
| Discovery test | the protocol of section 1.5, by a person, with screenshots | ends of M5, M10, M13 |
| Load | N simulated visitors on the warm set (p95 open < 2 s), one cold field while 20 visitors poll, abandon storms | M16 and before the demo |

## 5.12 Observability

- Structured JSON logs (journald, rotated): every request (path, status, ms, bytes; client as a salted hash), every job state change
  with phase timings, every upstream call (service, ms, outcome, breaker state).
- `/health` (public, cheap) and `/admin/metrics` (Prometheus text: queue lengths per class, worker use, memory, read throughput, cache
  hit rates per area, upstream outcomes, warm n/m, job durations by kind and size).
- A free external uptime monitor on `/health`; Cloudflare analytics for traffic; browser errors through `/client-log`.
- Alerts (mail from a cron check on the VM): warm set incomplete for more than 1 h, disk > 85 %, memory pressure, a breaker open > 10
  min, index reconciliation failing, validation report regressions.

## 5.13 Security and rate limits

- **No user data:** no accounts, no cookies needed; the leads a person saves stay in their browser until they press a button.
- **No open proxy:** the server fetches only from a fixed allow-list of upstream hosts with fixed URL templates (no URL comes from the
  client); all inputs validated (ranges for RA, Dec, sizes, distances, counts; tracks capped at 600 points).
- **Rate limits** (per client hash, token buckets): job creation (e.g. 60 new jobs per 10 min), light endpoints (300 per 10 min),
  upstream-backed endpoints (identify/context/small bodies: charged only for new places); polling free; per-client concurrency (8
  connections); global daily budgets for upstream requests and bytes out (the research left these disabled for local use: production
  enables them); Cloudflare rate-limiting rule as an outer fence.
- **Headers:** strict CSP (self + the R2 endpoint), no third-party scripts, `X-Content-Type-Options`, `Referrer-Policy`, HSTS via
  Cloudflare.
- **Admin:** behind Cloudflare Access and a bearer token; SSH by key only, from the admin address.
- **Supply chain:** locked dependencies, `pip-audit` and `npm audit` in CI, Dependabot alerts; container image built in CI.
- **Secrets:** R2 credentials and the background-marker secret in a root-only env file on the VM and in CI secrets; never in the repo.

## 5.14 Decisions taken from what the research learned

| Research observation | Production decision |
|---|---|
| one 12.8k-line module with global state | packages with clear boundaries: engine (pure), reader, upstream, jobs, products, api |
| one flat cache directory of ~300 k files, listed in full on every prune | hashed subfolders, SQLite catalogue, eviction by catalogue |
| a few writes not atomic | every write atomic |
| two row orientations in binary formats | one orientation, one container format |
| macOS allocator kept freed memory | compute in recycled worker processes |
| aux jobs as one thread each, up to 60 | bounded worker processes with checkpoints and pause |
| 429 on polling; background could starve a person | polling free; strict priority classes; waiting is a visible phase |
| an "immutable" record later completed | immutable only when complete |
| any client could set the background header | signed marker |
| version bump without purge left stale results; purge without re-warm made the demo wait | versioned keys, automatic purge, automatic re-warm, R2 snapshot |
| HiPS, VSX, NED had no breaker | every upstream behind a breaker |
| budgets disabled for local use | budgets on in production |
| Sharpen was made default after looking at one frame per field | frame audit on every frame before any default |
