# What we do and why

We built one tool with three kinds of moving picture, six searches, a spectrum for every source and a map of unusual spectra. Each exists to answer a question a visitor can ask of a patch of sky: what moves here, what is it, is it new, and what would a body beyond Neptune look like if it were here. This part is the map; the Method parts that follow give each answer in detail.

## Three time scales

Things in the sky move at very different speeds, and one picture cannot show all of them.

| What moves                 | Speed                                                    | View                                            | Search                |
| -------------------------- | -------------------------------------------------------- | ----------------------------------------------- | --------------------- |
| nearby stars, brown dwarfs | arcseconds per year                                      | the blink of pass stacks (months)               | movers between passes |
| asteroids, comets          | arcminutes per day                                       | the time-lapse of the visits of one pass (days) | time-lapse search     |
| bodies beyond Neptune      | a few arcseconds per day, a parallax jump between passes | the distance slider                             | Find Planet X         |

## The questions we answer

- **What moves here?** The blink and the time-lapse show it to a person, and the searches mark tracks that no person pointed out.
- **What is it?** Every track is checked against catalogues after the search: Gaia for stars, JPL's Small-Body Identification and Horizons for asteroids and comets, SIMBAD for other objects. A track that no catalogue explains is a lead, never a discovery.
- **Could it be chance?** Every search has a chance control: it runs the same search on data where the true motion has been destroyed and says how many tracks chance alone makes.
- **What colour is it?** Click any source for its spectrum, measured in every exposure that covers it, and for its check against 2MASS and WISE.
- **Is anything odd?** The spectral anomaly map ranks sources whose spectrum differs from an ordinary star of the field, and the scans look for sources that are red between 3.8 and 5 µm, which is the signature of cold brown dwarfs.
- **Is anything beyond Neptune?** Find Planet X adds the exposures of a pass along the motion a body at each trial distance would have; at the right distance a point comes into focus.

## What we offer

- **A blink and a time-lapse** for any place of the sky, with a record of what the visitor looks at in the address, so a view can be shared as a link.
- **Searches for movers** at three speeds: between passes, inside a pass and for bodies beyond Neptune.
- **Spectra** with their checks, brown dwarf candidates, red sources and the anomaly map.
- **Context:** the globe with the coverage of the survey, archive images from 1985 to 2010 on the timeline, and the NEOWISE and ZTF light curves of a source.

## Why our own stacking and fit

We stack and fit with our own code (Astropy, NumPy and SciPy) because standard tools do not fit this data: our stacking keeps detail when a pixel is as large as the star, and our fit reads the detector's own PSF zones and keeps the OUTLIER pixels that mark asteroids.

## Leads, not discoveries

A track or a candidate is drawn only when a search found it, and it carries its evidence: the frames, the numbers, the check against catalogues and the chance level. Where a search cannot reach a body, the page says how deep the search goes. The sections on validation and limits at the end of this page give the measured numbers and what we do not claim.

## Visible in the picture

Every find has a picture made for it: the passes of its place, for a time-lapse track its visits added along its motion against the same visits added in place, for a Planet X peak the sum along its orbit. We say a find is **visible** only when its ring stands out from its own neighbourhood in that picture, not from the whole picture: in a crowded field the noise of the whole picture is small next to the stars and residuals around the ring.

- We take the brightest pixel within 7″ of the ring's centre and measure it against an annulus from 2 to 5 of those radii around it: its median is the local floor, 1.4826 times its median absolute deviation is the local noise (for a sum of residuals, not less than its standard deviation).
- We tile the whole picture with control rings of the same size, all at least 2.5 radii from the ring. The ring must be at least 3σ above its annulus and brighter than the peaks of 95% of the control rings: in a crowded field many places hold as much light, and the ring is not visible there.
- In an added picture (a track's visits added along its motion, a peak's sum along its orbit) the ring's peak must also stand above the floor at least 1.5 times as high as the brightest control ring's: a sum of residuals has bright specks all over, and a ring that holds one like its neighbours is not seen.
- A time-lapse track must also hold at least 1.5σ more when its visits are added along its motion than when they are added in place: light that is as strong standing still is a star's residual, not a body.
- A Planet X peak is visible only in its sum along the orbit, and only when it stands out of that sum the same way: a sum that is a grey wash of 3σ everywhere shows nothing.

A find that is not visible stays in Findings behind "Not visible in the picture" and is not shown first.

## People's answers and score

People look at squares, at machine finds ("Does it really move?"), at red sources and at spectral anomalies.

**Questions a human eye answers better.** Each kind of find has a short series of plain questions, one at a time, each with the find's own pictures. Nothing the machine already knows is asked (numbers, detector places, catalogue answers), and every settled answer changes what the machine does:

- a track: does one dot step along the arrow (no: dropped before the slow checks) · if only in some pictures, tap where it is missing (the track is linked again without them) · a dot or a streak (a streak: dropped);
- a slow mover: does the dot shift while the stars stay put (no: dropped) · one dot or two touching (two: dropped, a blend) · is there a dot further back along the arrow in the old WISE and 2MASS pictures (yes: up the queue, the motion holds over decades);
- a Planet X peak: is there a dot in the ring when the visits are added along the orbit, and not when added as they are (no: dropped) · is it inside a bright star's glow or on its spike (yes: dropped, and counted against the rule; no: up the queue as a candidate);
- a red source: a sharp dot or a fuzzy patch (fuzzy: a galaxy) · there every time (no: an artefact) · on a glow, a spike or the edge (yes: an artefact) · has it moved between the old picture and the new (yes: the brown-dwarf search looks at it);
- an anomaly: does a neighbour star touch it in the odd pictures (yes: those exposures are left out of its spectrum) · do the odd pictures look broken (yes: those exposures are flagged; one flagged by two people is left out of every stack and spectrum).

"Not sure" at any step is "Can't tell", which is never wrong and never paid. A mark put on the picture has a kind too: one that moves starts the checks on its own; one that is in no catalogue asks every catalogue again; two people calling the same machine find an artefact set it aside (it leaves Findings' leads and the Candidates).

**Agreement.** The answers of one find settle by weighted agreement: 3 of agreeing weight, or 5 answers given. Each person weighs 1 until five of their answers were judged, then 0.5 plus the share they got right, between 0.5 and 1.5. Every fifth task of a person in a series is a **control object** whose answer is already known, judged at once and counted in that share: a find the catalogues name (a known asteroid's track should step along the arrow; a known mover should shift), a find the machine set aside itself (a star's residual, a galaxy, a peak in a halo, a blend: no dot of its own, or it does not shift), or a find earlier people settled unanimously, at least three of three. The finds people set aside are never controls, so nobody is judged by other people's verdicts on the same kind of artefact. The settled result is the one the series ends in; when answers differ and none reaches the weight, people do not agree, and nobody is right or wrong. A Findings card shows the result in one line ("People say: cold brown dwarf, 3 of 3").

[figure: people-counts | What people have answered so far, live: every series, and how often an answer goes against a rule of the machine]

**Score.** A quarter of a square looked through for at least 60 s earns 5 points, so a whole square is worth 20 (at most 100 a day); an agreeing answer 10; catching a control mover (a known mover of at least 1″/yr, a mark within 60″ of its place at 2025.9) 25; a submit that passed every check 100; a finding that becomes a candidate 500, confirmed 5000. Levels: Observer from 0, Searcher from 100, Tracker from 500, Hunter from 2500, Discoverer from 10 000 points. The leaderboard ranks a person after 10 settled answers, and the share of searchers holding a badge is shown only from 50 searchers.
