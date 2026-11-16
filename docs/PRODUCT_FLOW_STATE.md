# product-flow: state at 18:10 CET, 16 Nov 2026 (integration branch, local only, nothing deployed)

All six streams are merged: flow-grid, flow-accounts, flow-findings, flow-known-checks, flow-globe and flow-lab.
`make lint test` was green at 69fa493: 341 passed, 7 skipped; web 145 passed. The newer stream heads merged after it were not
re-run in full.

## Run it locally
The API runs on 8790 against the snapshot store `.store` (1168 production finds, from tools/findings_snapshot.py), with SKY_STANDBY=1,
SKY_AUTH_DEV=1, SKY_CLIPS=./clips and SKY_PRODUCT_DB=./product.sqlite. The web dev server runs on 5190.

## Unfinished
- **Sweep:**
  - measured locally only: about 17 squares/h warm on 4 workers, peak 3–3.6 GB;
  - the memory cap is proven;
  - instance recycling every 10 squares is committed but not yet run;
  - never started on production.
- **Rate:** the Planet Nine band (31,680 squares) is about 80 days of one instance. The whole sky needs more machines or a lighter
  per-square search.
- **above_bar (B2):** it should read `service.grid.bar_at(db, ra, dec)`; not wired yet.
- **Not clicked end to end:** "Check and submit", closing a task, notifications.
- **Data:** the product.sqlite grid rows are local trial data (38 imported squares plus the sweep trial).
