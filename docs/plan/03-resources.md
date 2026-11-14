# 3. Resources: data sources and external services

Every external dependency, how it is reached, its limits, how its answers are cached and what the system does when it fails. One rule
for all: **a source that did not answer is said, never hidden and never replaced by a guess**; a failed answer is not cached; a
successful answer is cached for the stated time. All calls go through one HTTP client layer (`service/upstream/`) with per-host
timeouts, retries, concurrency limits and a circuit breaker, and a generic User-Agent (no personal data in requests).

## 3.1 Summary

| # | Source | Used for | Access | Cache | When it fails |
|---|---|---|---|---|---|
| 1 | SPHEREx Level-2, public S3 bucket `nasa-irsa-spherex` (AWS us-east-1) | every pixel | HTTPS range GET, no auth | raw row bands (LRU, capped); products (versioned, immutable) | retry once; stall 40 s → missing; breaker; the field states how many exposures are missing |
| 2 | Own exposure index (built from 1) | which exposures cover a field | local shards | permanent, per period | IRSA SIA2 for unindexed periods |
| 3 | IRSA SIA2 / ObsCore TAP (`spherex` schema) | fallback exposure list; daily reconciliation | HTTPS TAP/SIA | 1 day | index only; reconciliation reports "IRSA did not answer" |
| 4 | Gaia DR3 (GAVO `dr3lite` TAP; ESA Gaia archive TAP for RUWE/variability) | star lists for checks, identify, context | ADQL over HTTPS | cells of the sky, 30 days | breaker; track check `pending`; identify falls back to SIMBAD |
| 5 | SIMBAD (CDS TAP and name resolver) | names, types, proper motions, the globe search | ADQL / script over HTTPS | 30 days (ok/none) | "catalogues not reachable"; Gaia alone |
| 6 | 2MASS PSC, AllWISE, CatWISE2020 (IRSA TAP) | catalogue check of spectra, context | ADQL over HTTPS | 30 days | spectrum shown without the comparison; context part `unavailable` |
| 7 | VizieR (AAVSO VSX `B/vsx/vsx`, extensible list) | context | TAP / cone over HTTPS | 7 days | part `unavailable` |
| 8 | NED | context (galaxies) | object-by-position API | 7 days | part `unavailable` |
| 9 | IMCCE SkyBoT | known small bodies in a field at a time | cone search over HTTPS | per (field, epoch, rate window), 30 days | track check "known bodies not asked"; never called "new" |
| 10 | JPL Horizons API | ephemeris of a named body seen from SPHEREx (`500@-163182`) | HTTPS JSON | per (body, times), 30 days | panel says Horizons did not answer; SkyBoT only |
| 11 | JPL SBDB API and Small-Body Identification API | orbit, H, diameter; "who was in the field" | HTTPS JSON | 30 days | card without those rows |
| 12 | HiPS surveys, IRSA mirror (DSS2 colour, 2MASS colour, AllWISE RGB) + SFD dust map | globe backgrounds | HTTPS tiles through our proxy | permanent (immutable tiles) | Allsky image stays; dust map is local |
| 13 | STScI DSS (`dss_search`) | archive plates 1950–1990s | HTTPS FITS cutout | permanent per field | that survey "did not answer" on the timeline |
| 14 | IRSA Finder Chart API | 2MASS J/H/Ks, AllWISE W1/W2 images | HTTPS XML → FITS | permanent per field | same |
| 15 | STScI Pan-STARRS (`ps1filenames`, `fitscut`) | g, i images (stack 2010–2014) | HTTPS FITS | permanent per field | same |
| 16 | NEOWISE single exposures (IRSA IBE / cutouts) | WISE archive check of a D4 candidate | HTTPS FITS cutouts | per candidate, permanent | job reports progress and failures; on request only |
| 17 | SVO Filter Profile Service | J, H, Ks, W1, W2 curves | fetched once, **stored in the repository** | — | — |
| 18 | ERFA ephemerides (bundled with astropy) | Earth position for orbits and parallax | local library | — | — |
| 19 | Cloudflare (DNS, CDN, Tunnel, R2) | front, cache, origin access, warm-set snapshot | — | — | origin down: cached static and immutable products still served; R2 snapshot restores a VM |
| 20 | Oracle Cloud (or fallback VM) | compute, disk | — | — | redeploy + restore from R2 |

## 3.2 SPHEREx Level-2 in the public bucket

- **Where:** `https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/<release>/<period>/…` (QR2, QR3, deep collections, and DR1 since 2026-11-11 — preferred for the periods it covers), part of the AWS
  Open Data program: no account, no charge to read. The nearer to us-east-1 the compute, the faster cold reads (the reason
  for choosing Ashburn).
- **How:** HTTPS GET with `Range: bytes=a-b`; one request per contiguous row band per plane; header chain walked once per file and
  stored in the index (offsets of IMAGE, FLAGS, VARIANCE, ZODI, PSF, WCS-WAVE). Compressed FLAGS (QR3): only the needed tiles.
  Connection pool with keep-alive; concurrency by budget (section 5.4 of the architecture): up to 96 in flight for a person's field on
  the research link, tuned for the cloud in M4.
- **Limits:** S3 has no multi-range GET; request-rate limits are far above our use (thousands per second per prefix). Our budget, not
  S3's, is the limit: a 60′ cold field reads 9–12 MB × 600–700 exposures ≈ 7–9 GB.
- **Caching:** raw row bands per (file, plane, band of 64 rows), compressed, in `cache/raw/` with LRU eviction under a cap (research:
  ~40 GB for the warm set and testing; cloud cap ~100 GB); window results per (file, field box) in `cache/win/`. Raw data never expire
  (files are immutable; a reprocessed file has a new key).
- **Failure handling:** retry once with jitter; a read that stalls 40 s is abandoned and counted; circuit breaker on S3 per host (opens
  after repeated 5xx/timeouts; large reads judged by bytes per second, not by duration). A field with missing exposures says "n of m
  exposures could not be read" on its status line.

## 3.3 The exposure index

Built by our own indexer from the bucket (3.2) and kept on local disk; snapshot to R2 after every daily update. ~0.9 GB for 1.8 M files
(170 periods, 2026-11). A full rebuild reads the first 32 kB of every file (primary + IMAGE header; other offsets derived and validated by
full header walks on a 1 % sample): research measurement 42 GB read in 8.6 h (~52 files/s, 16 connections, back-off on S3 `SlowDown`).
It runs only on a fresh deployment without a snapshot; a new period (~11 k files) takes 4–5 min; the indexer follows hourly. Reconciled daily against
IRSA ObsCore counts per period: a difference is reported in `/api/v1/health` (IRSA lagged by many periods in 2026-11; the bucket is the
truth for reading).

## 3.4 IRSA services

| Service | Endpoint | Used for | Notes |
|---|---|---|---|
| TAP (sync) | `https://irsa.ipac.caltech.edu/TAP/sync` | ObsCore reconciliation (on 2026-11-14 ObsCore lists `spherex_dr1` 945,725, `spherex_dr1_deep` 233,681, `spherex_dr1_cube` 12,288 HiPS-cube tiles, `spherex_qr3` 362,124, `spherex_qr3_deep` 86,700; QR2 is no longer listed: DR1 supersedes it; footprints are 4-corner polygons); 2MASS `fp_psc`, AllWISE `allwise_p3as_psd`, CatWISE | can take 45 s; down on 2026-11-13/14 (502, timeouts); 1 concurrent query per kind, 20 s timeout for interactive parts |
| SIA2 | `https://irsa.ipac.caltech.edu/SIA?COLLECTION=spherex_…` | exposure list for unindexed periods | lags the bucket |
| Finder Chart | `https://irsa.ipac.caltech.edu/applications/finderchart/servlet/api` | 2MASS, WISE images with links | about a minute per field cold |
| IBE / cutouts | `https://irsa.ipac.caltech.edu/ibe/…` | NEOWISE single exposures | ~1 min per cutout; 900 per candidate; on request only, 2 concurrent |
| HiPS mirror | `https://irsa.ipac.caltech.edu/data/hips/CDS/{2MASS/Color, AllWISE/RGB-W4-W2-W1, DSS2/color, …}` | globe backgrounds | CORS open; CDS's own servers (alasky) timed out from the research network, so the IRSA mirror is primary and CDS a secondary |

## 3.5 Catalogues

- **Gaia DR3.** GAVO `dr3lite` TAP (G < 17.5, positions and proper motions) for cell-based star lists (cells of ~0.5° cached 30 days;
  the field's stars are cut from the cells); ESA Gaia archive TAP for RUWE, parallax error and the variability flag in the context card
  (one source per request). Positions moved from epoch 2016.0 with proper motion to the date needed.
- **Mirrors.** Gaia: GAVO `https://dc.g-vo.org/tap/sync` and ESA `https://gea.esac.esa.int/tap-server/tap/sync`; SIMBAD:
  `https://simbad.cds.unistra.fr/simbad/sim-tap/sync` and `https://simbad.harvard.edu/simbad/sim-tap/sync`; Sesame for names. Each
  part tries its mirrors in order before saying "did not answer". Every upstream has a breaker (research left HiPS, VSX and NED unguarded:
  production guards all of them).
- **SIMBAD.** TAP for cone queries with proper motion (two cones for fast movers, 2.6.2) and the name resolver for the globe search; the
  five test targets are also kept as a dated local snapshot so the globe never waits on SIMBAD for them; a SIMBAD answer is cached 30
  days. CDS asks clients to stay below a few queries per second: one concurrent query, a client-side rate limit of 5 per second.
- **2MASS / AllWISE / CatWISE / VSX / NED** for the context card and the spectrum check; each part independent, 12 s deadline,
  `ok`/`none`/`unavailable`.

## 3.6 Small bodies

- **IMCCE SkyBoT** cone search (`https://ssp.imcce.fr/webservices/skybot/api/conesearch.php`): bodies in a cone at an epoch with V and
  rates; geocentric, so positions are moved to the spacecraft viewpoint with the exposure's `X_SC/Y_SC/Z_SC`. Used after a search,
  never fed into it. 20–30 s per request in research: background, cached per (field, epoch).
- **JPL Horizons** (`https://ssd.jpl.nasa.gov/api/horizons.api`): observer `500@-163182` (the SPHEREx spacecraft), `TLIST` of exposure
  MJDs, quantities 1, 3, 9, 19, 20, 24. One request at a time (JPL asks for serial use).
- **JPL SBDB** (`https://ssd-api.jpl.nasa.gov/sbdb.api`, `sbdb_query.api`) for orbit, H, diameter, and the globe's small-body lists;
  **Small-Body Identification** (`sb_ident.api`) for "who was in this field" (20–30 s; background; cached by field and date).

## 3.7 Archive images

DSS (STScI `https://archive.stsci.edu/cgi-bin/dss_search?v=<survey>&r&d&e=J2000&h&w&f=fits`, surveys poss1_red, poss1_blue, quickv,
poss2ukstu_blue, poss2ukstu_red, poss2ukstu_ir), Finder Chart (2MASS, WISE), Pan-STARRS (`ps1filenames.py` + `fitscut.cgi`). Each image
is reprojected onto the field grid and cached permanently per (field, survey, band). Breakers per provider; the timeline says which
survey did not answer.

## 3.8 Cloud resources

| Resource | Plan | Limit that matters |
|---|---|---|
| Oracle Cloud Always Free, Ampere A1, US East (Ashburn) | the server: service + workers + indexer | 2 OCPU + 12 GB RAM (allowance since 2026-06-15; was 4 + 24), 200 GB block storage, 10 TB egress/month; account upgraded to pay-as-you-go (still free within the allowance) to avoid idle reclamation and to get A1 capacity |
| Cloudflare (free plan) | DNS, TLS, CDN for static files and immutable products, Tunnel (`cloudflared`) to the origin (no open inbound port), basic rate-limiting rules, Access for admin endpoints | proxied response size limits (large parts are split into chunks ≤ 64 MB each), 100 s origin timeout (every API answer is short; long work is a job) |
| Cloudflare R2 (free tier) | snapshot of the index and of the warm-set products (~7–10 GB) | 10 GB storage free, no egress fee; beyond 10 GB a few cents per GB-month |
| Domain | optional, ~10 USD/yr; else a Cloudflare-provided hostname | — |
| Uptime check | a free external monitor on `/api/v1/health` | — |
| Budget fallback | 4-core / 16 GB ARM VM in or next to us-east-1 (AWS Graviton spot, a small VPS in Virginia): ~15–40 USD/month | chosen at the cloud gate of M4 if 2 OCPU is too slow or no A1 capacity |

Free tiers changed in 2026; the provider's page is checked on the day of sign-up. The account sign-up and the card check are the
user's.
