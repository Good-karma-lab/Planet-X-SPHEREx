# SPHEREx Sky

A public web tool that shows real images of the sky from NASA's SPHEREx mission so that anyone can quickly see how they change over
time — and find moving objects nobody told them about. Entry for NASA Space Apps 2026, challenge "Planet X and SPHEREx".

- Years: the **pendulum** of pass stacks shows nearby stars and brown dwarfs shift.
- Days: the **film of the visits of one pass** shows asteroids hop while the stars stand still.
- Hours: the **film of one visit** shows fast near-Earth bodies.
- Beyond Neptune: the **distance slider** adds exposures along trial orbits until a faint body comes into focus.
- A click on any source gives its card and its spectrum, measured live from the exposures.
- Searches find movers, pairs that change in place and spectral anomalies; each find is a lead with its evidence and chance control.

Status: building for the Space Apps deadline (15 Nov 2026). Lab and Globe work on real data; milestones M0–M2 done, M3 validated. The plan is in [`docs/plan/`](docs/plan/README.md); progress in [`docs/PROGRESS.md`](docs/PROGRESS.md).

Data: SPHEREx Level-2 spectral images from NASA/IPAC IRSA (public AWS bucket `nasa-irsa-spherex`); catalogues and archive images from
IRSA, Gaia (ESA), SIMBAD (CDS), IMCCE SkyBoT, JPL and STScI, credited where shown.

## Development

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12), Node 22.

```bash
make setup
```

```bash
make dev
```

`make dev` runs the API on http://127.0.0.1:8780 and the web dev server on http://127.0.0.1:5173. `make lint test e2e` runs the checks
that CI runs (`e2e` builds the web app and drives it in Chromium against the real service on port 8781).

## Licence

Apache License 2.0 (see [LICENSE](LICENSE)). Third-party parts: Phosphor icons (MIT). Data: SPHEREx Level-2 images from NASA/IPAC
IRSA; object catalogue from SIMBAD (CDS); globe backgrounds from the DSS2, 2MASS, AllWISE and IRIS HiPS surveys (CDS, IRSA).
