# 1. Goal and users

## 1.1 The challenge

NASA Space Apps 2026, challenge "Planet X and SPHEREx":

> Create a public-facing web tool to display images of the sky from the SPHEREx mission, making it easy for anyone to quickly
> see how they change over time. It may help users discover moving objects.

Our reading of it, which every decision in this plan is judged against:

**The pictures and films must let a person find objects nobody told them about. If the program also finds them, that is a bonus.**

## 1.2 The four promises

1. **Anyone, quickly.** A first-time visitor who opens a place on the sky sees a moving picture within seconds, without choosing
   settings and without reading. Plain words in the interface. A quick picture first, the refined one behind it. A test target that
   is part of the demo opens in under 2 seconds, always (the warm set, section 5.6 of the architecture).
2. **Change over time is seen by eye.** The background must stand still so that the only thing that changes is what really changed.
   This is the reason for every cleaning step: a cosmic-ray hit, a streak or a halo spike that blinks is a false "discovery" for the
   viewer. A view that needs the object's name or a marker to be understood fails the goal.
3. **Every time scale has a moving picture.**

   | Time scale | What moves | Moving picture |
   |---|---|---|
   | years | nearby stars, brown dwarfs (arcseconds per year) | the **pendulum** of pass stacks: passes in the order 1→2→3→2 |
   | days | asteroids, comets (arcminutes per day) | the **film of the visits of one pass**, one frame per visit, stars standing still |
   | hours | fast near-Earth bodies (arcminutes per hour) | the **film of one visit**, one frame per exposure instant |
   | beyond Neptune | bodies too faint for any frame | the **distance slider**: exposures added along trial distances; a point comes into focus |

4. **The program helps, honestly.** A track is drawn only when a search found it; each find carries its evidence (frames, numbers,
   the check against known bodies and catalogues, the chance control) and is called a **lead**, never a discovery.

## 1.3 Users

| User | What they want | What it means for the system |
|---|---|---|
| Curious visitor (main user) | "show me the sky changing" from a link or the globe | zero settings, the picture moves at once, plain words, no paragraphs on screen |
| Student, teacher, amateur astronomer | open a known object, see it move, read its spectrum | named test targets, a click on any star gives its card and spectrum |
| Challenge judge | understand in a minute what is real and what was found | the method page, evidence for every find, honest wording, the demo never waits |
| Citizen scientist | look for unknown movers and odd spectra | films, the searches with their chance controls, leads with copyable reports |
| Developer / researcher | reproduce the numbers | open API, versioned products, the validation suite and its published results |

No accounts, no sign-in. Everything public. The address of the lab holds its full state, so any view is shareable as a link.

## 1.4 The five test targets

Exactly the five of the challenge statement; no others appear as test targets in the interface:

| Target | What it tests |
|---|---|
| Barnard's Star | a bright, saturated, fast star (10.4″/yr) in a dense Milky Way field |
| ε Indi B | a faint, cold brown dwarf (4.6″/yr) beside a saturated bright star; orange-red among white stars |
| WISE 0855−0714 | a Y dwarf invisible below 3.8 µm: "not detected" must be said honestly, upper limits drawn |
| RW Aurigae | a young variable in a star-forming region: change in place, pairs of stars, no false mover |
| R Draconis | a Mira near the ecliptic pole: thousands of exposures, saturated, passes cut by the 90-day rule |

## 1.5 The discovery test (run at the end of milestones M5, M10 and M13)

Open each field by **coordinates only** (no name, so nothing is marked) and decide honestly whether a person who knows nothing would
notice the mover within about ten seconds of the picture appearing. A "no" means the milestone is not done: say why and what would
make it a yes.

| Field (coordinates only) | What should catch the eye | In which moving picture |
|---|---|---|
| RA 269.4479, Dec +4.7687, 5′ (Barnard's Star in 2025–2026) | one star swings, everything else stands still (10″ per year) | pendulum |
| RA 331.077, Dec −56.794, 5′ | an orange-red dot that shifts among white stars (ε Indi B) | pendulum with spectral colour |
| RA 169.8917, Dec +4.3514, 60′, pass of 14–20 Dec 2025 | points that hop from visit to visit (asteroids of V 18–19) | film of the visits of the pass |
| RA 27.378, Dec −0.120, 18′, pass of July 2025 | a point that comes into focus as the distance slider nears 95 au (Eris, V 18.7) | sum of the visits along the motion |
| Sedna's three fields | after the deep sum, one lead card that is Sedna (V 20.8), about 83 au | deep sum of three passes |

Known honest result from the research (to be improved, not hidden): in the ecliptic 60′ film a verified asteroid is seen because it is
ringed; unmarked it is a 2–3 px point among ~300 stars. Milestone M10 carries the work that could turn this into a yes (sky behind
the motion layer, a short trail, a blink strip of the body's visits, a narrower film around a found track).

## 1.6 Rules that hold everywhere

These come from the research phase and from the user's own reviews. They are requirements, not preferences.

| Rule | Detail |
|---|---|
| Real data only | Every pixel shown comes from SPHEREx (or a named archive survey, labelled as such). Nothing modelled, filled or interpolated into a hole. No mock numbers presented as real. |
| Saturated core is white | A saturated core is white, never a hole and never black. A place no exposure covers is flat blue-grey (inside the field) or black (outside the coverage of a wall panel). |
| What is marked | An object was asked for: mark only it and its path (one point per pass). Coordinates were given: mark only what a search found. A person's own click may ring the source they chose. No rings on artefacts, no circles on catalogue stars by default. |
| Status only on the picture | Text on or beside the picture is the current status only, one short line: what is shown, what is loading (n of m), what a search found. No sentences about what a view means. Explanations live in tooltips and on the Method page. |
| No explanatory paragraphs in panels | Panels hold values and controls. A long explanation is a tooltip or a Method link. |
| Display panel never collapsed | The often-used Display options are open, high in the Controls tab, a compact grid of one-line switches; the Controls tab fits a 1000×620 window without a screen of scrolling. |
| No layout jumps | Nothing moves when a mode, band, size or tab changes; space for late-arriving content is reserved. |
| Named bands stay primary | Starlight, Water vapor, Methane, Water ice, Dust (PAH), CO₂, CO are the main way to choose a wavelength range; detector numbers and microns are secondary labels; a free range slider is an addition. |
| Display options do what they say | Marker off removes every mark at a source in every view; each overlay switch governs its own layer. |
| Check every frame | A change to the look becomes a default only after an off/on audit on every pass × all five targets × every size (and the loading stage), by numbers and by eye. One frame per field is not a check. |
| Test by clicking | End-to-end tests change size, band, mode and tab with the controls in a real browser, not by reloading: stale caches hide behind fresh loads. |
| Versioned caches | Every product carries its own version; a version bump purges the stale entries and re-warms the warm set automatically. Nobody bumps a version without this. |
| The demo never waits | The warm set is computed before anyone asks, kept warm across restarts and redeploys (snapshot), and a person's request always goes before background work. |
| Ask before removing | A control or view people use is not removed without asking. |
| Leads, not discoveries | Every find says what checked it and what did not; "unexplained" is not "new". |
| English only | Code, comments, documents, commit messages, interface. |
