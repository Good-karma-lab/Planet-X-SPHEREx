# Space Apps project form: ready to paste

The NASA Space Apps 2026 project page (My Team → Project → Edit). Sources: Project Submission Guide
(https://www.spaceappschallenge.org/resources/project-submission-guide/, read on 2026-11-15). Submission closes Sunday 15 November 2026,
23:59 local time of the Barcelona Local Event; the local judging deadline is earlier (see `README.md`).

**Limits.** The guide gives no word or character limit for any text field. The form itself needs a login, so its own limits could not be
checked: the word and character counts below are measured on the texts, so you can see at once whether a field refuses them. Uploads on
the page are images only (JPG, PNG, GIF, up to 10 MB); video and documents go in as public links with no login. Save often; only the
last save is reviewed.

**Proof-read.** Every number in the texts below was checked on 2026-11-15 against the live Method page (the deployed bundle with the consistency fixes): Gaia motions 11.37 ± 0.47 and 10.7 ± 0.8 (Barnard), 4.6 ± 0.5 (ε Indi B, 4.59 ± 0.49 in the Method), 7.93 ± 0.61 (WISE 0855, SIMBAD 8.15), 6 of 8 blind asteroids, chance 0.0 and 1.83, Eris 95.6 ± 0.4 au, Makemake 52.5 ± 0.2 au, Sedna 83.0 au with 8.2σ between 8.07σ and 8.48σ.

---

## 1. Project name (2 words, 13 characters)

SPHEREx Space

## 2. High-level project summary (204 words, 1171 characters)

SPHEREx Space turns NASA's SPHEREx all-sky survey into films anyone can watch. In 1930 Pluto was found by flipping between two photographs and seeing one dot move; SPHEREx photographs the whole sky every six months in 102 colours, but its raw frames cannot be blinked: one small patch has hundreds of exposures, each in another colour, roll and pixel grid. SPHEREx Space indexes all 1,628,230 public SPHEREx files and keeps the index current every day. Pick any place on a globe of the sky, by dragging, by name or by coordinates, and the lab reads that patch's exposures, cleans and stacks each pass, splits it by spectrum and merges it back into one film: the background stands still, and a nearby star or a brown dwarf visibly moves. It detects motion itself (Barnard's Star 10.7 ± 0.8″/yr, ESA Gaia 10.4), finds asteroids inside a pass (checked with NASA JPL), and gives every object a passport with its spectrum measured live in every exposure. **Find Planet X** adds up exposures along trial orbits: without being told where to look it recovers Eris at 95.6 au (JPL 95.5) and Makemake, with Sedna as a strong lead. No Planet X is claimed. Free, no account, open source.

## 3. Link to project demo

https://good-karma-lab.github.io/Planet-X-SPHEREx/

The presentation site: the 30-second video (also on YouTube, linked from the page) and the 7 slides, with a PDF. Live once the repository is public and Pages is on (`docs/PAGES.md`). If Pages is not up in time, paste the YouTube link of the 30-second video here instead. Test the link in a private window.

## 4. Link to final project

https://www.spherex.space

(The tool runs without login. The code is https://github.com/Good-karma-lab/Planet-X-SPHEREx under Apache-2.0.)

## 5. Detailed project description (958 words, 5392 characters)

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
  Live runs on www.spherex.space, 2026-11-15, keys in the Method page.
- Sedna: a blind deep sum of three passes of one field peaks at 83.0 au (JPL 83.0), 0.5 to 0.8 arcseconds from JPL's place in each pass; its 8.2σ is above the noise ceiling (8.07σ) and below the bar (8.48σ), so it is a strong lead and not a formal detection (live run, 2026-11-15).

The Method page (https://www.spherex.space/#/method) gives every step, formula and number with its source.

**How it works.** A Python service reads only the image rows a field needs from NASA's public SPHEREx bucket on AWS, using an index of
every Level-2 exposure with its footprint. It cleans (pipeline flags, cosmic rays by a leave-one-out test, streaks and halos; saturated
cores shown white, never as holes), stacks and registers the exposures and serves versioned products; the browser draws them with
WebGL. Searches and spectra run in worker processes on the server and their results are kept, so the next visitor sees them at once.

**How it stays current.** Our index holds every SPHEREx Level-2 file with its footprint, 1,628,230 today (DR1 1,179,406 and QR3 448,824, equal to the bucket listing in every period). Every day at 06:00 it re-reads the last five days from IRSA and reconciles the counts with the bucket; a field's address carries a digest of its exposures, so new data re-make only the fields they touch.

**Limits we state.** Find Planet X has found no new body; a peak is a lead until other data confirm it, and the depth of each search is
its own measured noise bar on that field. Stars that saturate in most exposures get no SPHEREx spectrum; the motion of such a star in a
small field is pulled by its saturated core. A track between passes needs three passes and a film track four visits.

## 6. Use of Super Intelligence (SI)

(The guide says this answer is for data collection and is not seen by the judges; judges see watermarks, descriptive text and metadata
inside the project.) 167 words, 1031 characters

This project was built with AI coding assistants (Anthropic's Claude Code agents) working under the direction of our team. People on
the team set the goals and the rules, chose between designs, reviewed every result by looking at the pictures and clicking through the
tool, and decided what to keep. The agents wrote most of the code (the Python service and analysis, the web interface), the
documentation and the method write-up, ran the validation scripts and drafted these submission texts; a human reviewed and edited them.

The images and videos the tool shows are **not AI-generated**: they are computed deterministically by our code from real NASA SPHEREx
exposures (and archive images from DSS2, 2MASS and AllWISE, credited on screen). No generative image or video model was used for
any picture, figure or video of the submission. No NASA logo or insignia appears in any generated content.

AI-written code and text are acknowledged here and in the repository (commit messages carry a co-author line for the AI assistant).

## 7. NASA data (118 words, 769 characters)

Required: at least one NASA data or resource for a Global Award.

- SPHEREx Level-2 spectral images and calibration (DR1 and QR3), NASA/IPAC IRSA: every SPHEREx pixel shown and measured; the public
  AWS bucket `nasa-irsa-spherex`; the DR1 all-sky spectral cube for the quick-look spectrum and the globe.
- IRSA services and HiPS surveys (DSS2, 2MASS, WISE, IRIS), the 2MASS and AllWISE catalogues (also from IRSA's cloud Parquet copy),
  NEOWISE and ZTF light curves, the SPHEREx 3I/ATLAS exposure table.
- JPL Small-Body Database, Small-Body Identification and Horizons: known bodies in a film, orbits, ephemerides of the spacecraft.
- Resources listed by the challenge that we used: IRSA SPHEREx and its tutorials, Gaia DR3 (through MAST's public copy and ESA's
  archive), the NEOSSat data and WiseView (as the model of the blink).

## 8. Space Agency partner data and other references (93 words, 556 characters)

- ESA: Gaia DR3 (the check of every track; names, parallaxes and distances; the pointing refit of the NEOSSat frame).
- CSA: NEOSSat frame of 3I/ATLAS, 14 December 2025, through the Canadian Astronomy Data Centre (CADC).
- Other: SIMBAD, VizieR and NED (CDS, IPAC); the SVO filter profile service; the Minor Planet Center (observatory code 288 and the ADES
  format); papers: Batygin & Brown 2016, Brown & Batygin 2021, Trujillo & Sheppard 2014, Crill et al. 2020.

The full list with links is in `data-and-references.md` and on the About page of the tool.

Tools and software used to build it:

Python 3.12 (FastAPI, uvicorn, httpx, NumPy, SciPy, Astropy, PyArrow, SQLite), TypeScript with Preact and WebGL (Vite, three.js for the
globe), pytest, ruff, mypy, vitest and Playwright for checks, Caddy and one Oracle Cloud VM in Madrid for hosting, AI coding assistants
(see below).

---

## Team and checkboxes

- Team: Good Karma Lab · Local Event: Barcelona. Members to confirm on the Members tab: Aleksandr Ostapenko (team owner), Katherine
  Karmatskikh (team member). Nobody can be added after the deadline.
- Challenge: Planet X and SPHEREx.
- Tick the terms and conditions box and "Confirm all work is original" yourself.
- Optional images for the page (JPG or PNG, up to 10 MB each): the poster frame of the video and screenshots from `docs/submission/demo/img/` and the figures of the Method page.

## Before pressing Submit

- [ ] The demo link and the final project link open in a private window without login.
- [ ] The repository is public if the README or the text links to it.
- [ ] No NASA logo, flag or mission identifier in any generated picture or the video.
- [ ] Texts match the live tool: the Method page (https://www.spherex.space/#/method) holds every number with its source.
- [ ] Press Submit before the Barcelona local deadline, and again check that the status says submitted.
