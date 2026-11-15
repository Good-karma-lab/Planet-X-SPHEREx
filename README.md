# SPHEREx Space

**Live: https://spherex.space** (no login)

SPHEREx Space is a free web tool that shows real images of the sky from NASA's SPHEREx mission so that anyone can see how they change
over time, and that helps find moving objects nobody pointed out. It is our entry for the NASA Space Apps Challenge 2026, challenge
"Planet X and SPHEREx".

SPHEREx maps the whole sky in 102 infrared colours between 0.75 and 5 µm and returns to every place about every six months. One raw
exposure sees each star at another wavelength, pixel grid and roll angle, so raw frames do not show motion. We read the public
Level-2 exposures, clean and stack them into one stable picture per pass, colour the picture by wavelength, and compare passes.

## What you can do

- **Globe.** Pick a place on the sky, or one of the five test targets of the challenge (Barnard's Star, ε Indi B, WISE 0855−0714,
  RW Aurigae, R Draconis), and open it in the lab.
- **Lab.** The passes of a field played as a pendulum (1 → 2 → 3 → 2) so a moving star swings while the background stands still;
  spectral colour, named bands, a wall of the six detectors, Difference and Co-add between passes, the archive images that precede
  SPHEREx, and a Days scale that plays the visits of one pass so asteroids hop from visit to visit. Every setting is in the address, so
  every view is a link.
- **Spectrum on a click.** PSF photometry of the source in every exposure that covers it, one point per exposure, with a check against
  2MASS and WISE and the identification from SIMBAD and Gaia DR3.
- **Searches.** Movers between passes (checked against Gaia, with a chance control), the film search for asteroids inside a pass
  (checked against JPL), brown dwarf candidates, red sources, spectral anomalies, and **Find Planet X**: bodies beyond Neptune found
  by adding exposures along trial distances and motions, in one pass or summed over all of them. A track no catalogue explains is a
  lead, not a discovery.
- **Method.** https://spherex.space/#/method describes every step with its parameters and the numbers we measure.

## How it works

```
web/       Preact + TypeScript, WebGL (Vite): globe, lab, searches, Planet X, Method, About
service/   FastAPI service: job scheduler with a person-first priority gate, worker processes, versioned product store,
           readers of the public SPHEREx bucket by byte ranges, upstream clients (IRSA, ESA Gaia, JPL, SIMBAD, CADC, ZTF, NEOWISE)
engine/    the numerical code, free of I/O: background and masks, the splat stack, cosmic-ray and streak tests, registration,
           colour, difference, film, movers, anomalies, PSF photometry, the distant-body search and orbit link
indexer/   the exposure index: every Level-2 file with its footprint, kept current from the bucket
validation/ checks of the engine on real SPHEREx exposures; their dated reports are in docs/validation/
deploy/    provisioning and release scripts for one VM behind Caddy
docs/      the plan, the method specifications, progress, the submission texts
```

The service reads only the image rows a field needs from NASA's public SPHEREx bucket (`s3://nasa-irsa-spherex`, region us-east-1),
cleans and stacks the exposures, and serves versioned products; the browser draws them with WebGL. Searches and spectra run on the
server and their results are kept, so the next visitor sees them at once. The Method page is the specification of the algorithms.

## Run it

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12) and Node 22.

```bash
make setup
```

The service needs the exposure index, which is built from the public bucket (it takes a while the first time and can be resumed):

```bash
uv run python -m indexer.build bootstrap
```

```bash
make dev
```

`make dev` runs the API on http://127.0.0.1:8780 and the web dev server on http://127.0.0.1:5173. The store (index and products)
is the `store/` directory, or `SKY_STORE`. `make lint test e2e` runs the checks that CI runs (`e2e` builds the web app and drives it
in Chromium against the real service). Deployment on a single VM is described in [`deploy/README.md`](deploy/README.md).

## Data and credits

- **NASA:** SPHEREx Level-2 spectral images and calibration (DR1 and QR3), NASA/JPL-Caltech, served by NASA/IPAC Infrared Science
  Archive (IRSA); IRSA services and HiPS surveys (DSS2, 2MASS, WISE, IRIS), the 2MASS and AllWISE catalogues, NEOWISE and ZTF
  light curves; JPL Small-Body Database, Small-Body Identification and Horizons.
- **Partner agencies:** ESA Gaia DR3 (positions, motions, parallaxes); CSA NEOSSat frames of 3I/ATLAS through the Canadian
  Astronomy Data Centre (CADC).
- **Other:** SIMBAD, VizieR and NED (CDS and IPAC); the SVO filter profile service. Full list with links and references:
  [`docs/submission/data-and-references.md`](docs/submission/data-and-references.md).
- Not affiliated with or endorsed by NASA.

## AI assistance

The code, the tests and the documentation were written largely by AI coding assistants (Anthropic's Claude Code) working under the
direction of the team, who set the goals, chose between designs and reviewed every result. No picture, figure or video of the tool is
AI-generated: they are computed by this code from real exposures. See
[`docs/submission/si-disclosure.md`](docs/submission/si-disclosure.md).

## Team

**Good Karma Lab**, NASA Space Apps Challenge 2026, Barcelona Local Event.

- Aleksandr Ostapenko, team owner: [LinkedIn](https://www.linkedin.com/in/alexanderostapenko/)
- Katherine Karmatskikh, team member: [LinkedIn](https://www.linkedin.com/in/katherinekarmatskikh/)

## Licence

Apache License 2.0 (see [LICENSE](LICENSE)). Third-party parts: Phosphor icons (MIT).
