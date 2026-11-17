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
cd /Users/aostapenko/Work/Planet-X-SPHEREx-flow && SKY_FINDINGS_READONLY=1 SKY_AUTH_DEV=1 SKY_STORE=$PWD/.store SKY_CLIPS=$PWD/clips SKY_PRODUCT_DB=$PWD/product.sqlite SKY_WARM=0 SKY_WORKERS=4 .venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8790
```

- `SKY_FINDINGS_READONLY=1` keeps Findings from replacing the snapshot with the few finds of the local result files, and keeps the
  Find Planet X identifier from asking JPL. Never `SKY_STANDBY=1` here: a standby builds no new field ("the sky service is restarting").
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

## A cheaper per-square search: options, for the user to decide

**Measured stages** (local Mac, 4 workers, 3 new Planet Nine squares, 3 passes each):

| Square | Field built | Movers search | Brown-dwarf search | Quick parts of 2 passes ready | Peak RSS |
|---|---|---|---|---|---|
| 296-0149 | 512 s | 11 s | 0 s | 67 s | 2.3 GB |
| 291-0147 | 582 s | 7 s | 0 s | 67 s | 2.7 GB |
| 293-0151 | 143 s | 7 s | 0 s | 17 s | 4.2 GB |

**What this shows:**
- The field build (downloading and stacking every exposure of every pass) is about 95% of a square's time.
- The movers search reads only the field's "quick" parts (one white image per pass).
- The brown-dwarf pass costs nothing: it reuses the movers result.

**Options:**
1. **As now:** about 16 squares/h per 4-worker instance. The Planet Nine band (31,680 squares) takes about 80 days; the ecliptic
   another 120.
2. **No brown-dwarf pass:** saves about 0 s. Not worth losing those finds.
3. **Movers on quick parts only:** a sweep field job that stops once every pass has its quick part, skipping the detector stacks and
   the differences.
   - Quick parts of 2 of 3 passes were ready at 17–67 s, against 143–582 s for the full build. That suggests 3–5× faster: about
     50–80 squares/h. This is an estimate; it is not built.
   - The cost: a square the machine searched opens cold in the lab, with the full build of 1–2 min when a person takes it.
   - It needs a "quick only" mode in service/workers/fieldjob.py (not B1's file).
4. **More cores:** the rate grows about linearly with workers. The production VM has 12; separate sweep instances need their own
   memory caps.
5. **A smaller area first:** only the densest part of the Planet Nine band (for example, 5° around RA 50°, Dec +8°: about 700
   squares, 1–2 days as now).

Options 3 and 4 combined would take the Planet Nine band in about a week.

## Release to production (prepared, NOT done)

The user's order: the supervisor walks 5190 and accepts it, then product-flow is merged into `main`, deployed, and tested on
production. Nothing below runs before that acceptance.

### a. Deploy steps (VM ubuntu@158.179.217.127, key ~/.ssh/oci_spherex_ed25519)

**0. Freeze.**
- `git -C ../Planet-X-SPHEREx-flow log -1` is the accepted commit, and `make lint test` was green on exactly that commit.
- Note the live release: `curl -s https://www.spherex.space/release.txt`. That revision is the way back.

**1. Back up the production state.** Run on the VM as the sky user. The service keeps running.

```bash
ssh -i ~/.ssh/oci_spherex_ed25519 ubuntu@158.179.217.127 'set -e; d=/var/lib/sky-backup/$(date -u +%Y%m%dT%H%MZ); sudo -u sky mkdir -p $d; for f in findings.db catalogue.sqlite product.sqlite; do [ -f /var/lib/sky/$f ] && sudo -u sky sqlite3 /var/lib/sky/$f ".backup $d/$f"; done; sudo -u sky cp -a /var/lib/sky/fpx_known $d/ 2>/dev/null || true; sudo cp /etc/sky/env $d/env; ls -la $d; df -h /var/lib/sky | tail -1'
```

- The fields, products and index are immutable or rebuildable: R2 holds their snapshot (deploy/snapshot.sh), so they are not copied again.
- product.sqlite does not exist on production yet (first release). Back it up from the second release on.

**2. Merge into main** (local, after acceptance):

```bash
cd /Users/aostapenko/Work/Planet-X-SPHEREx && git checkout main && git merge --no-ff product-flow && make lint test && git push origin main
```

**3. Environment on the VM.** Append to `/etc/sky/env`, see b below. Restart only through push.sh.

**4. Deploy.**

```bash
cd /Users/aostapenko/Work/Planet-X-SPHEREx && ./deploy/push.sh ubuntu@158.179.217.127
```

- push.sh builds the web, rsyncs the code, runs `uv sync` (Pillow is new), and installs the systemd units.
- It restarts the standby (8781) first and checks its health, waits until no job runs, then restarts the main instance (8780).
- Without `--now` it waits up to 20 min for running jobs: zero downtime.

**5. Pages.**
- `docs/` on main is the GitHub Pages site. This release touches only `docs/PRODUCT_FLOW_STATE.md`, which Pages does not link.
  Nothing on Pages changes, so no Pages step.

**6. Smoke test** (list d), then report.

**Way back** (any blocking failure in d):
1. `git -C /Users/aostapenko/Work/Planet-X-SPHEREx checkout <revision from release.txt>`, then `./deploy/push.sh ubuntu@158.179.217.127 --now`.
2. Then `git checkout main` again. On main: `git revert -m 1 <merge commit>` and push, so main matches production.
3. The new tables in product.sqlite and the new files do not hurt the old code: it never opens them.
4. findings.db: the new code writes only new columns and tables. If a restore is still wanted, stop sky-api and copy back the
   backup from step 1.

### b. Production environment (`/etc/sky/env`, added)

```
SKY_PUBLIC_URL=https://www.spherex.space
SKY_PRODUCT_DB=/var/lib/sky/product.sqlite
SKY_CLIPS=/var/lib/sky/clips
```

- **Never** `SKY_AUTH_DEV`: it shows the sign-in link on the page.
- **Never** `SKY_FINDINGS_READONLY`: production's Findings is the writer.
- **Never** `SKY_STANDBY` on the main unit (the standby unit sets it itself).
- SKY_PRODUCT_DB and SKY_CLIPS sit on the VM disk beside the store, inside the backup above. Known objects and checks keep their
  caches beside SKY_PRODUCT_DB: known_field/, checks/, neowise/.
- SKY_CLIPS_PRERENDER stays at its default (on): clips render in the background queue. Watch memory in the first hour
  (`systemctl status sky-api`, MemoryMax 13G).
- **The background sweep is NOT started.** tools/sweep.py runs as its own process, and its memory (3–4 GB per 4-worker instance)
  needs the user's decision on area and machine. The grid shows the 38 imported squares plus what visitors search.
- **JPL:** only the main instance asks JPL (the standby doesn't). JPL allows 20 identifications per 5 minutes for the whole VM.

### c. Sign-in on production

**What a real sign-in needs:**
- **E-mail link:** a mail sender. Today only `LogSender` exists, which writes the link to the API log. Proposed provider:
  **Resend** (an HTTPS API, a free tier of 3,000 mails a month).
  - Secret: `SKY_RESEND_API_KEY` in /etc/sky/env.
  - DNS on spherex.space: the SPF/DKIM records Resend gives, and a sender such as `signin@spherex.space`.
  - Alternatives: Postmark (`SKY_POSTMARK_TOKEN`) or Amazon SES (keys plus DNS). Plugging one in is a small class in
    service/accounts/mail.py.
- **Google:** an OAuth client in Google Cloud Console.
  - Its redirect URI: `https://www.spherex.space/api/auth/google/callback`.
  - Secrets: `SKY_GOOGLE_CLIENT_ID` and `SKY_GOOGLE_CLIENT_SECRET`. With them set, the Google button shows by itself.
- The user will be asked for the provider and the keys.

**Decision until then: "Sign-in opens soon".**
- The Cabinet tab stays. The sign-in panel says "Sign-in opens soon. Until then your marks and tasks are kept in this browser."
- The e-mail field and the Google button are hidden. `POST /api/auth/email` refuses with 503 "sign-in opens soon".
- Everything a guest can do works: take a square, mark, close a task, see Findings, the leaderboard and the globe.
- This is better than hiding the tab: the tab tells a visitor that accounts are coming, and nothing they do is lost.
- **Needed in flow-accounts' files before the release:** the sign-in panel, and /api/me's `auth` saying `{email: false, google: false}`
  when no sender and no Google keys are set. To be built by flow-accounts.
- **Release blocker:** if it isn't in by acceptance, the release waits for it. A public "send me a link" that only writes to a
  log would lose people's mails silently.

### d. Production test list (run right after step 4)

Each test is done in a real browser (Chrome, and the globe also in Firefox) and by curl. Results go to docs/qa as a dated note.

1. **Health:** `/api/v1/health` on both instances answers, role main/standby. `release.txt` is the new revision. No errors in
   `journalctl -u sky-api -n 200` since the restart.
2. **Guest flow:** landing → "Start searching" → globe with Coverage on → click a square → card → "Take this square" → lab with
   the task panel → look → "Nothing here" → the next square opens. /viewed and /done answer 200, and the viewers counter goes up.
3. **Globe tiles:** Star map is the default layer and the tiles load from /tiles (no 404s). The layers switch: SPHEREx, 2MASS,
   WISE, Dust. Turning in Firefox is smooth.
4. **Lab field:**
   - Barnard's Star 5′ opens warm in under 1 s; the time-lapse plays.
   - A new cold field builds (quick picture in about 20 s).
   - Known objects rings appear, with no "no match" on known stars. WISE 0855 5′ and Barnard 20′ show "no match" about 0.
5. **Findings:** the cards show clips (`/api/findings/{id}/clip.webp` answers image/webp), promising first.
   - `#/findings/<id>` opens the finding's own page. The "Leaderboard" link opens `#/leaders`.
6. **Cabinet:**
   - Without sign-in: the panel says "Sign-in opens soon", the e-mail field is not there, and POST /api/auth/email gives 503.
   - When a sender is set: one real sign-in by e-mail (the link arrives and logs in), a mark saved and seen again after a reload,
     sign-out.
7. **Leaderboard:** `#/leaders` loads; `/api/leaders` answers. No e-mail appears anywhere in its JSON.
8. **Share links:** for each kind (a profile `/u/...`, a finding story, a discovery), fetch the share URL as a crawler:
   `curl -A "facebookexternalhit/1.1"` and `-A "Twitterbot"`.
   - It must return HTML with og:title, og:description, og:image (1200×630, an absolute https URL that answers image/*),
     og:url and twitter:card.
   - Hash routes (#/…) are never seen by crawlers, so share links must be server paths that answer those tags.
   - This needs flow-accounts' sharing work. If it isn't in the release, this test is "not in release".
9. **Mobile:** a 375×812 phone. Landing, globe, a square task and the bottom bar, with no sideways scroll.
10. **Grid API:** `/api/grid/meta` (38 imported squares, scheme dec20), `/api/grid/coverage?corners=1`.
11. **Checks:** one "Check and submit" declared by a guest is refused with 401 (submitting needs an account).
    With sign-in on: one real job runs to its end (JPL and NEOWISE take up to about 20 min) and the notification arrives.
