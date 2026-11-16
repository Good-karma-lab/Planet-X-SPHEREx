# product-flow: the integration branch of the product flow (local only, nothing deployed)

Spec: `star-test/docs/PRODUCT_SPEC.md`. Every stream branch is merged here: flow-grid, flow-accounts, flow-findings,
flow-known-checks, flow-globe, flow-lab and flow-mobile. `main` is untouched.

## Start it (two terminals)

From the worktree `/Users/aostapenko/Work/Planet-X-SPHEREx-flow`.

**1. Once: dependencies and the findings snapshot**

```bash
cd /Users/aostapenko/Work/Planet-X-SPHEREx-flow
PYENV_VERSION=3.11.9 uv sync
(cd web && npm ci)
.venv/bin/python tools/findings_snapshot.py --shared ../Planet-X-SPHEREx/store --out .store
```

- `PYENV_VERSION` is set because `.python-version` asks for a 3.12 that this Mac's pyenv lacks; uv uses its own 3.12.
- The snapshot is a private store (`.store`). It links to the shared store's fields, index and raw files, and holds its own
  findings.db with the production finds (about 1,160).

**2. The API on 8790**

```bash
cd /Users/aostapenko/Work/Planet-X-SPHEREx-flow && SKY_STANDBY=1 SKY_AUTH_DEV=1 SKY_STORE=$PWD/.store SKY_CLIPS=$PWD/clips SKY_PRODUCT_DB=$PWD/product.sqlite SKY_WARM=0 SKY_WORKERS=4 .venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8790
```

- `SKY_STANDBY=1` keeps Findings from replacing the snapshot with the few finds of the local result files.
- `SKY_AUTH_DEV=1`: the sign-in link is printed in this log and shown on the page.
- Clips and the product database (users, marks, tasks, squares) live in this worktree, never in the shared store.

**3. The web on 5190**

```bash
cd /Users/aostapenko/Work/Planet-X-SPHEREx-flow/web && SKY_TILES=https://www.spherex.space SKY_API=http://127.0.0.1:8790 npx vite --port 5190 --strictPort
```

`SKY_TILES` proxies `/tiles` (the baked star map, SPHEREx and survey layers: static files of the deployed site, read-only GETs). Without it the globe has no Star map layer and looks unlike production.

Open http://localhost:5190. "Start searching" goes to the globe with the Coverage layer on.

## Checks

`PYENV_VERSION=3.11.9 make lint test` covers ruff, format, mypy, pytest, the web typecheck and the web unit tests.

## The sky grid and the background sweep (stream B1)

- **Grid:** scheme `dec20`.
  - 540 declination bands of 20′; each band is split into equal RA steps of about 20′.
  - 371,272 squares, ids `bbb-cccc`.
  - `/api/grid/meta` gives the scheme's numbers.
  - `/api/grid/coverage` gives every square that is not new, with its ra and dec (and its corners with `?corners=1`).
- **Imported:** the 47 places already searched on the live site, snapshotted in data/searched_seed.json, fill 38 squares.
- **Sweep:** `tools/sweep.py` runs in its own process with a private service instance, one square at a time:
  20′ field → movers between passes → brown dwarfs.
  - Hard memory cap on the whole process tree; it stops with exit code 3. Proven: cap 2,500 MB, tripped at 2,514 MB, clean.
  - The instance is restarted every 10 squares.
  - Order: the Planet Nine band, then the ecliptic, then the rest.
  - The bar is stored per square. `service.grid.bar_at(db, ra, dec)` returns it, for the above_bar check.
  - Never started on production.
- **Measured on this Mac, 4 workers, Planet Nine band:**

  | Run | Squares | Finds | Rate | Peak RSS | Per square |
  |---|---|---|---|---|---|
  | Trial | 5 | 0 | 10.8 squares/h | 3.0 GB | 831 s cold, then 90–335 s |
  | Run 2 | 18 | 0 | 15.95 squares/h | 4.15 GB | 75–589 s, median about 200 s |

  Run 2 shared the Mac with a lab field build.

## Unfinished

- "Check and submit" end to end, with the real slow checks (JPL, NEOWISE about 20 min), has not been walked here.
- A cheaper per-square search is for the user to decide (options below).
- flow-mobile: merged when it reports.
- The local product.sqlite grid rows are trial data: 38 imported squares plus the sweep trial.
