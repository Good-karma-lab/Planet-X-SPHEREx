# Project page — text for every field

For the user to review and paste into the NASA Space Apps 2026 project page. Every claim below is something the deployed tool at
https://spherex.space shows on real SPHEREx data, or a number this system measured. Fields marked **[fill]** need the team's input.

## Project name

**SPHEREx Sky** — see the sky change

## Team

Good Karma Lab · Local Event: Barcelona **[fill: members]**

## Challenge

Planet X and SPHEREx.

## High-level summary

SPHEREx Sky is a free web tool that turns NASA SPHEREx's public infrared exposures into pictures in which you can see the sky change.
Pick any place on a globe of the sky and the lab plays the SPHEREx seasons of that field back and forth like a blink comparator,
coloured by SPHEREx's own spectrum, so a nearby star or a cold brown dwarf visibly moves while the background stands still. A click on
any source measures its spectrum live from the individual exposures. Built-in searches find what moves between seasons (checked against
Gaia afterwards, with a chance control) and which sources have a spectrum unlike the ordinary stars of the field, each with a passport
that says why. Everything shown is computed from real SPHEREx Level-2 data.

## Link to demo **[fill: the uploaded video or slides]**

A 30-second screen recording of the live tool (`docs/submission/demo/`), or the 7 slides (`slides.md`).

## Link to final project

https://spherex.space — the running tool, no login. Code: https://github.com/Good-karma-lab/Planet-X-SPHEREx (Apache-2.0).

## Detailed description

**The problem.** SPHEREx maps the whole sky in 102 infrared colours from 0.75 to 5 µm and returns to every place about every six
months. Its raw frames do not show motion to the eye: each exposure sees a star at another wavelength, roll angle and pixel grid, so
stepping through them shows the flicker of the spectrum, not a star's shift of a fraction of a pixel.

**What SPHEREx Sky does.**

- **Globe.** The whole sky over SPHEREx's own DR1 all-sky map, DSS2, 2MASS, AllWISE or IRAS dust maps, the coverage of 1.63 million SPHEREx exposures, a catalogue of
  4,100 brown dwarfs, fast stars and variable stars, and the five test targets of the challenge. Aim anywhere and open the field.
- **The lab: seasons as a pendulum.** For every pass (season) all exposures of the field are cleaned and stacked on a grid four times
  finer than the 6.15″ detector pixel, then played 1 → 2 → 3 → 2. Barnard's Star swings by 10″ a year; ε Indi B drifts among white stars.
- **Colour that means something.** Every wavelength gets its rainbow colour, so a star's colour is its spectrum; each season is
  white-balanced on its own field stars. Named bands (starlight, water vapour, methane, water ice, dust, CO₂, CO) can be blended or
  shown side by side; a wall of the six detectors shows that a real object moves in all of them at once.
- **Difference and co-add.** Each season against the first as per-pixel significance (brighter magenta, fainter cyan), or all seasons
  added for the faintest sources.
- **Archive images.** The same field in DSS2 (1985–1999), 2MASS (1997–2001) and AllWISE (2010), blinked before the SPHEREx seasons, with
  the test target ringed where it was then: decades of motion in one loop.
- **The film of one season.** The visits of one pass, aligned, with the static sky removed: asteroids hop from visit to visit. JPL
  predicts which known asteroids and comets cross the field; each is ringed on every visit, solid where the film's own motion map sees
  it, and its visits stacked along JPL's track show how strongly it stands out (z 9 for a V 19.7 asteroid seen in only 5 of 11
  single visits). The number of SPHEREx detector images holding each body equals IRSA's Moving Object Search Tool (119, 107, 118, 121).
- **3I/ATLAS, seen by two space agencies.** A film centred on the interstellar comet on 14 Dec 2025, and the same day's frame from the
  Canadian Space Agency's NEOSSat (CADC): its pointing refitted on 95 Gaia stars, the comet found 5.6″ from JPL's place at G ≈ 12.4.
- **Spectrum on a click.** The tool fits the point-spread function of the detector in every exposure that covers the source and draws
  one point per exposure as it is measured, coloured by detector; a moving star is measured where it was on each date. Any clicked
  source is named from SIMBAD and Gaia DR3, with a distance when the parallax is good. The points download as CSV.
- **Red sources.** The flux at 3.8–5 µm against 1.1–1.6 µm, measured season by season: the two brown dwarfs among the test targets,
  WISE 0855−0714 and ε Indi B, stand out as red and moving.
- **Search between seasons.** Sources of every season, a stationary test, straight tracks through three or more seasons at 3–120″ per
  year; every track checked against Gaia DR3 at the season dates ("same motion", a centroid effect that is rejected, or "no Gaia
  motion"); a chance control re-runs the search with the moving sources at random places.
- **Light curves and catalogues.** A clicked source's ZTF and NEOWISE light curves (2014–2024) beside the SPHEREx years, and its
  spectrum averaged under 2MASS and WISE filters against the catalogue magnitudes.
- **Spectral anomalies.** One button measures every source of the field in every exposure, builds the field's own ordinary star, and
  flags sources whose spectrum does not fit; each anomaly gets a passport: its score against the field's threshold, the wavelengths that
  drive the misfit, its motion, and what SIMBAD and Gaia know. "Anomaly", never "discovery".

**How it was checked (real data, measured by this system).**

- Barnard's Star: the search between seasons finds one track in its 20′ field, 10.66″/yr toward 353°, and Gaia DR3 confirms the same
  motion (10.39″/yr). Measured on the stacks with its parallax: 10.08″/yr toward 355°.
- ε Indi B: one track through four seasons, 4.6–4.7″/yr toward 122–125°, the same motion as Gaia DR3. In its 20′ field the anomaly search
  ranks it first by far (score 13.8 against a threshold of 2.6; the next source 4.6), driven by an excess at 3.9–4.3 µm (+40σ): the
  signature of a cold T dwarf.
- An ordinary star (2MASS J17572542+0446559): 357 of 385 exposures give a point; averaged under the 2MASS and WISE filters its
  spectrum gives 0.99 (J), 1.02 (H), 1.03 (Ks) and 0.985 (W1) of the catalogue fluxes.
- Chance: re-running the search with the moving sources at random places gives 0.0 tracks per field in 20 trials.
- On a 60′ field near the ecliptic the anomaly search ranks a Seyfert 2 galaxy (LEDA 1266968) first among 93 spectra: galaxies do not
  look like stars.

**How it works.** A Python service reads only the image rows a field needs from NASA's public SPHEREx bucket on AWS, using an index of
every Level-2 exposure with its footprint; it cleans (pipeline flags, cosmic rays by a leave-one-out test, halos of bright stars;
saturated cores shown white, never as holes) and stacks the exposures, and serves versioned products; the browser draws them with
WebGL. Searches and spectra run on the server; results are kept so the next visitor sees them at once.

**Limits we state.** Stars saturated in most exposures (brighter than about J = 10.5) get no SPHEREx spectrum. A track needs three
seasons; shifts smaller than about 5″ between the first and last season are not caught. A lead is not a discovery until confirmed.

## Space Agency data used (NASA and partners)

See `data-and-references.md`: NASA SPHEREx Level-2 data and calibration through NASA/IPAC IRSA (public AWS bucket), IRSA services and
HiPS mirror (DSS2, 2MASS, AllWISE, IRIS); ESA Gaia DR3; SIMBAD (CDS).

## Use of Super Intelligence (SI)

See `si-disclosure.md`.

## References

See `data-and-references.md`.
