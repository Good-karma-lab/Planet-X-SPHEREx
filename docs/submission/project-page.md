# Project page: text for every field

For the NASA Space Apps 2026 project page of team Good Karma Lab (Local Event: Barcelona). Every claim below is something the deployed
tool at https://spherex.space does today, and every number is one measured on the live service (the endpoint or the dated report is
given with it). The one field marked **[fill]** is the YouTube link of the final video.

## Project name

**SPHEREx Space**

## One-line summary

A free web tool that plays real SPHEREx infrared exposures of any place on the sky as a moving picture and searches them for movers:
stars, brown dwarfs, asteroids and bodies beyond Neptune.

## Team

Good Karma Lab · Local Event: Barcelona · Aleksandr Ostapenko (team owner), Katherine Karmatskikh (team member)

## Challenge

Planet X and SPHEREx.

## High-level project summary

SPHEREx Space shows what NASA's SPHEREx telescope really saw. Pick any place on a globe of the sky and the lab plays the SPHEREx passes
of that field back and forth like a blink comparator, coloured by SPHEREx's own spectrum: the background stands still, and a nearby
star or a brown dwarf visibly moves. Searches start by themselves: tracks through the passes checked against ESA Gaia with a chance
control, asteroids that hop between the visits of a pass checked against JPL, brown dwarf candidates, red sources and spectral
anomalies. **Find Planet X** adds exposures along trial distances and motions to look for slow bodies beyond Neptune, and recovers
Eris and Makemake from their fields without being told their distance; Sedna comes out as a strong lead. It addresses the challenge directly: any visitor enters
coordinates and sees a series of images of that patch like a short video, in which moving objects stand out, and every find is a lead
with its evidence, never a discovery. It matters because SPHEREx's raw frames show no motion to the eye, while blinking survey images
is how Pluto was found in 1930 and how volunteers have since found thousands of brown dwarfs in WISE images.

## Link to project demo

https://good-karma-lab.github.io/Planet-X-SPHEREx/ : the presentation site (the 30-second screen recording of the live tool, the 7
slides with a Present mode, the PDF and the page of checks), published from the repository's `docs/` once the repository is public
(`docs/PAGES.md`). Video on YouTube: **[fill: the link after Sunday's upload]**.

## Link to final project

https://spherex.space: the running tool, no login. Code: https://github.com/Good-karma-lab/Planet-X-SPHEREx (Apache-2.0).
Presentation: https://good-karma-lab.github.io/Planet-X-SPHEREx/.

## Detailed project description

**The problem.** SPHEREx maps the whole sky in 102 infrared colours from 0.75 to 5 µm and returns to every place about every six
months. Each exposure sees a star at another wavelength, roll angle and pixel grid, so stepping through raw frames shows the flicker of
the spectrum, not the shift of a star.

**How we address the challenge, point by point.**

- *Display SPHEREx images and make change over time easy to see.* For every pass of a field all exposures are cleaned and stacked on
  a grid finer than the 6.15″ detector pixel, white-balanced and coloured by wavelength, then played 1 → 2 → 3 → 2 (the pendulum).
  Barnard's Star swings; the stars around it stand still. A Months | Days switch plays the passes or the visits of one pass.
- *Help users find moving objects.* Searches between passes (years), inside a pass (days) and for bodies beyond Neptune, each with a
  chance control and a check against catalogues.
- *Enter RA and Dec.* The address holds the whole state of a view, so any view is a link; the five test targets of the challenge are
  one click away.
- *Choose the field of view.* Fields of 2′ to 60′; speed rings say which motions each size keeps.
- *Subtract images from different times.* Difference shows each pass against the first as per-pixel significance; Co-add adds the passes.
- *The 102 bands and spectra on the fly.* Named bands (starlight, water vapour, methane, water ice, dust, CO₂, CO) as a blend or side
  by side, a wall of the six detectors, and a spectrum on a click: PSF photometry in every exposure that covers the source, drawn one
  point per exposure as it is measured, checked against 2MASS and WISE.
- *Overlays and metadata.* The Gaia check on every track, archive images (DSS2, 2MASS, AllWISE) before the SPHEREx passes, the SPHEREx
  pipeline flags as an artifact mask, SIMBAD and Gaia names for every clicked source.
- *Easy to update with new releases.* The exposure index follows NASA's public bucket (DR1 and QR3 today).

**What else the tool does.** A globe of the sky with the coverage of SPHEREx and the survey backgrounds; brown dwarf candidates
(moving and red between 3.8 and 5 µm); the spectral anomaly map with a passport for every anomaly; ZTF and NEOWISE light curves of a
clicked source; the NEOSSat frame of the comet 3I/ATLAS next to SPHEREx's film of it; the Planet X page with the predicted Planet
Nine orbit, the fields searched and every search's own measured noise bar.

**How it was checked (live numbers).**

- Movers against Gaia: Barnard's Star 11.37 ± 0.47 ″/yr in a 5′ field and 10.7 ± 0.8 ″/yr in a 20′ field, Gaia DR3 10.4; ε Indi B 4.6 ±
  0.5 ″/yr, Gaia 4.7; WISE 0855−0714, found in the red bands, 7.93 ± 0.61 ″/yr, SIMBAD 8.15 (live `/api/v1/movers` and
  `/api/v1/browndwarfs`, 2026-11-14).
- Asteroids found blind: on the 60′ ecliptic field of December 2025 the film search recovers 6 of the 8 known bodies brighter than V 19.1
  that have at least four frames in the film, with no unmatched track in that field (live film `48c5bbb2c5e028b847075c51`, JPL Small-Body
  Identification and Horizons, 2026-11-14).
- Chance: the same searches with the moving sources at random places make 0.0 tracks per field (20 trials on five 5′ fields and on
  ε Indi at 10′, 20′ and 60′); in the film search 0.0 at four or more visits and 1.83 per trial at three (12 trials), which is why a
  track needs four visits.
- Bodies beyond Neptune: Eris, three passes linked by one orbit, 95.6 ± 0.4 au (JPL 95.5); Makemake, two passes linked, 52.5 ± 0.2 au
  (JPL 52.3, not confirmed by a third pass). One pass alone gives only a wide range of distances (70 to 165 au for Eris's first pass).
  Live runs on spherex.space, 2026-11-15, keys in the Method page.
- Sedna: a blind deep sum of three passes of one field peaks at 83.0 au (JPL 83.0), 0.5 to 0.8 arcseconds from JPL's place in each
  pass; its 8.2σ is above the noise ceiling (8.07σ) and below the bar (8.48σ), so it is a strong lead and not a formal detection (live
  run, 2026-11-15).

The Method page (https://spherex.space/#/method) gives every step, formula and number with its source.

**How it works.** A Python service reads only the image rows a field needs from NASA's public SPHEREx bucket on AWS, using an index of
every Level-2 exposure with its footprint. It cleans (pipeline flags, cosmic rays by a leave-one-out test, streaks and halos; saturated
cores shown white, never as holes), stacks and registers the exposures and serves versioned products; the browser draws them with
WebGL. Searches and spectra run in worker processes on the server and their results are kept, so the next visitor sees them at once.

**Limits we state.** Find Planet X has found no new body; a peak is a lead until other data confirm it, and the depth of each search is
its own measured noise bar on that field. Stars that saturate in most exposures get no SPHEREx spectrum; the motion of such a star in a
small field is pulled by its saturated core. A track between passes needs three passes and a film track four visits.

## Tools, languages and software

Python 3.12 (FastAPI, uvicorn, httpx, NumPy, SciPy, Astropy, PyArrow, SQLite), TypeScript with Preact and WebGL (Vite, three.js for the
globe), pytest, ruff, mypy, vitest and Playwright for checks, Caddy and one Oracle Cloud VM in Madrid for hosting, AI coding assistants
(see below).

## NASA data and resources used

- SPHEREx Level-2 spectral images and calibration (DR1 and QR3), NASA/IPAC IRSA: every SPHEREx pixel shown and measured; the public
  AWS bucket `nasa-irsa-spherex`; the DR1 all-sky spectral cube for the quick-look spectrum and the globe.
- IRSA services and HiPS surveys (DSS2, 2MASS, WISE, IRIS), the 2MASS and AllWISE catalogues (also from IRSA's cloud Parquet copy),
  NEOWISE and ZTF light curves, the SPHEREx 3I/ATLAS exposure table.
- JPL Small-Body Database, Small-Body Identification and Horizons: known bodies in a film, orbits, ephemerides of the spacecraft.
- Resources listed by the challenge that we used: IRSA SPHEREx and its tutorials, Gaia DR3 (through MAST's public copy and ESA's
  archive), the NEOSSat data and WiseView (as the model of the blink).

## Space Agency partner data and other references

- ESA: Gaia DR3 (the check of every track; names, parallaxes and distances; the pointing refit of the NEOSSat frame).
- CSA: NEOSSat frame of 3I/ATLAS, 14 December 2025, through the Canadian Astronomy Data Centre (CADC).
- Other: SIMBAD, VizieR and NED (CDS, IPAC); the SVO filter profile service; the Minor Planet Center (observatory code 288 and the ADES
  format); papers: Batygin & Brown 2016, Brown & Batygin 2021, Trujillo & Sheppard 2014, Crill et al. 2020.

The full list with links is in `data-and-references.md` and on the About page of the tool.

## Use of Super Intelligence (SI)

See `si-disclosure.md`: the code, tests and documentation were written largely by AI coding assistants (Anthropic's Claude Code) under
the team's direction; no picture or video of the tool is AI-generated.
