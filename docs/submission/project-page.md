# Project page — draft text for every field

Status: DRAFT for the user to review and paste into the NASA Space Apps 2026 project page. Fields marked **[fill]** need the team's own
information or the final links. Claims are limited to what the tool shows on real SPHEREx data and to numbers that were measured.

## Project name

**SPHEREx Sky** — see the sky change *(alternative names for the user to choose: "Blink SPHEREx", "SPHEREx Time Machine")*

## Team **[fill]**

Team name, members, local event (the submission deadline is 23:59 local time of the team's Local Event).

## Challenge

Planet X and SPHEREx.

## High-level summary

SPHEREx Sky is a free web tool that shows real images of the sky from NASA's SPHEREx mission so that anyone can see, within seconds,
how a patch of sky changes over time — and spot moving objects nobody pointed out to them. You open any place by name or coordinates
and get a moving picture: the clean, stacked images of each SPHEREx pass play back and forth like an old blink comparator, coloured by
SPHEREx's 102 infrared channels, so a nearby star or a brown dwarf visibly jumps while the background stands still. Films of single
passes show asteroids hopping from visit to visit, and a "distance slider" adds exposures along trial orbits until a faint body beyond
Neptune comes into focus. A click on any star gives its live spectrum from the individual exposures. Searches flag what moves or looks
unusual, each as a lead with its evidence and a chance control — never as a "discovery".

## Link to demo **[fill]**

A ≤ 30-second video (English) or ≤ 7 slides including the title slide. Script: `video-script.md`; slides: `slides.md`.

## Link to final project **[fill]**

The public URL of the running tool (no login), and the code repository if public.

## Detailed description

**What it does.** SPHEREx maps the whole sky in 102 colours from 0.75 to 5 µm and looks at every place about twice a year. Its raw
frames do not show motion to the eye: each exposure sees a star at another wavelength, angle and pixel grid, so stepping through them
shows the flicker of the spectrum, not a shift of a third of a pixel. SPHEREx Sky turns the public Level-2 exposures of any field into
pictures in which the only thing that changes is what really changed:

- **Years — the pendulum.** For each pass (a visit of the field half a year apart) every exposure is cleaned and stacked on a grid four
  times finer than the 6.15″ detector pixel. The passes then play 1 → 2 → 3 → 2 with a short crossfade. Barnard's Star swings by
  ~10″ per year; ε Indi B, a cold brown dwarf, is an orange-red dot drifting among white stars.
- **Colour that means something.** Every wavelength gets its rainbow colour and they are added, so a star's colour is its spectrum:
  cold objects glow red because they emit most near 4–5 µm.
- **Walls.** One panel per named band (starlight, water vapour, methane, water ice, dust, CO₂, CO) and one per detector: a real object
  jumps in all six detectors at once; a cosmic-ray hit lives in one.
- **Days — the film of one pass.** One frame per visit, stars standing still: an asteroid hops across the field.
- **Beyond Neptune — the distance slider.** Bodies too faint for any single frame appear when the frames of a pass are added along
  their motion: drag the distance and watch a point come into focus at the right distance (Eris at ~95 au).
- **Spectrum on a click.** For any source the tool fits the point-spread function in every exposure and draws one point per exposure,
  live, in the order SPHEREx took them; it checks itself against 2MASS and WISE catalogue fluxes.
- **Searches, honestly.** Between passes, inside a pass, inside a visit and along trial orbits beyond Neptune; spectral anomalies;
  pairs of stars whose brightness changes in place. Every find is a lead with its frames, numbers, catalogue check (Gaia, SIMBAD,
  known small bodies) and a chance control.

**Why it matters.** SPHEREx data are public but hard to look at. The tool lets students, amateurs and the public explore real data and
make their own finds, and it shows researchers a fast way to check candidates in three or more passes with a full spectrum.

**How it was checked (real data, measured).** Proper motion of Barnard's Star from the stacks agrees with Gaia; a spectrum of an
ordinary star agrees with 2MASS/WISE within 1–3 %; on a field near the ecliptic the in-pass search finds 4 of 4 known asteroids with no
false track; Eris is found blind in single passes and Sedna (V 20.8) in the sum of three passes; a candidate at 394 au was ruled out with
ten years of NEOWISE images. **[the user may trim this list to what the deployed tool shows]**

**How it works.** A Python service reads only the image rows a field needs from NASA's public SPHEREx bucket, cleans (pipeline flags,
cosmic rays by a leave-one-out test, streaks, halos of bright stars; saturated cores shown white, never as holes) and stacks the
exposures, and serves the products; the browser draws them with WebGL. Everything shown is real SPHEREx data; nothing is modelled or
filled in.

**Limits we state.** A single blind search reaches about V 20–21 in reflected light; the predicted Planet Nine is fainter. Leads are not
discoveries until confirmed.

## Space Agency data used (NASA and partners)

See `data-and-references.md` (NASA: SPHEREx Level-2 via IRSA, 2MASS, WISE/NEOWISE, JPL Horizons and Small-Body Database, IRSA
services; ESA: Gaia DR3; others: SIMBAD/CDS, IMCCE SkyBoT, STScI DSS and Pan-STARRS).

## Use of software intelligence (SI / AI)

See `si-disclosure.md` (to be pasted as the SI-use statement).

## References

See `data-and-references.md`.
