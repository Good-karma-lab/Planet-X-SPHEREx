# Searches for movers

Things in the sky change in different ways, and each way needs its own search: a star that drifts a few arcseconds a year shows up as a shift between passes, an asteroid that crosses a field in days as a track through the visits of one pass, and a close pair whose brightness ratio changes as a peak that hops. We run these searches by themselves on every field we build, and nothing about any object is given to them. In the 60′ ecliptic field of December 2025 the film search recovers 6 of the 8 known bodies it should see, blind, with no unexplained track.

## Kinds of searches

| What changes | How it looks in SPHEREx data | Our search |
|---|---|---|
| nearby stars, brown dwarfs | arcseconds per year: a shift between passes, the same in every stack | search between passes |
| asteroids, comets | tens of arcseconds per hour: a new place in every visit, in no stack | film search inside a pass |
| bodies beyond Neptune | a few arcseconds per day inside a pass, a jump of tens of arcminutes between passes, too faint for one exposure | exposures added along the motion (next part) |
| a pair or a variable in place | the peak of a blend hops; the flux ratio changes at fixed places | pairs changing in place |

Four rules hold for all of them. A named object only decides the centre of the field. The catalogue check comes after the search, never before. Every search has a chance control: the same candidates are moved to chance places and linked the same way, and what chance makes is reported next to what we found. A pass is a group of exposures of one field less than 60 days apart (at most 90 days); a visit is a run of exposures of one detector with gaps under 30 minutes.

## Sources in a field

The searches work on the white stack of each pass, the picture made of the detectors the field has in common (see the part on stacks). Before anything is linked we list the point sources of every pass.

```
smooth the stack with a Gaussian of FWHM 7″ (a point source), ignoring places without data
local background: median in blocks of max(16 grid points, 60″), smoothed back to full size
noise: 1.4826 × median absolute deviation of (smoothed − background)
source: a local maximum within a box of 3 × FWHM (21″) with smoothed − background > 8 × noise
        place refined by a parabola through the peak and its four neighbours
saturated zone (area of at least half a detector pixel, 6.15″): ONE source at the centre of the zone,
        weighted by the saturated share; peaks within 2 detector pixels of it are not sources
kept: only where every pass has data and at least 20″ inside the field edge; at most 4000 sources,
      saturated first, then by S/N
```

Passes that cover less than half of what the best-covering pass covers (the survey's edge) sit out, so that a place seen by every pass exists. The pair search builds its own list (see Pairs changing in place).

## Search between passes

This is the search for stars that move a few arcseconds a year. It starts by itself when a field has at least three passes, on the white stacks the field already published, so it reads nothing new from the archive.

[figure: pendulum-barnard | Barnard's Star walks north between the three passes while the other stars stay put]

### Linking

```
stationary: a source with a source within 2.5″ in every other pass
tracks:     the first and the last pass fix a velocity (3–120 ″/yr, baseline longer than 0.2 yr);
            the predicted place in each pass between is searched within 2.5″;
            a source there with brightness within a factor 2.5 joins (a saturated source joins only a saturated one)
            a track needs at least 3 passes; the velocity is refitted on the members by least squares;
            tracks through the same sources are merged: the one with more passes, then the smaller scatter, is kept
weak:       a track of exactly 3 passes whose faintest member is below S/N 15: counted, not drawn
```

Each drawn track carries its motion east and north, its scatter about the fitted line, its place at epoch 2025.9 and its S/N in every pass.

### Catalogue check

After the search every track is compared with Gaia DR3 moved to each pass date (Gaia epoch 2016.0). The cone around the track's place at the Gaia epoch has radius 6″ + 0.2 × the track's speed × the time difference. When the Gaia archive does not answer, the same catalogue is read from its public Parquet copy on AWS; when neither answers the track is drawn dashed as "not checked yet" and is asked again after at most a minute.

| State | Rule | Shown |
|---|---|---|
| `same` | a Gaia star with proper motion above 2 mas/yr, moved to each pass date, within 3″ of the track in every pass | drawn, "Gaia: same motion", named |
| `still` | a Gaia star within 6″ of the track's middle place whose proper motion is below 0.3 of the track's rate: the shift is a centroid effect | rejected: counted, not drawn |
| `none` | no Gaia star within 6″ | drawn, "no Gaia motion" (unexplained is not new: it can be a brown dwarf, a distant body or an artefact) |
| `pending` | no catalogue answered | drawn dashed, "not checked yet" |

### Chance control

The moving (non-stationary) sources of every pass are put at uniform random places of the field, the stationary ones stay, and the linker runs again, 20 times per field. The mean number of tracks per field that chance alone makes is reported with the result.

**Result:** the rates we measure agree with Gaia's: 11.37 ± 0.47 ″/yr for Barnard's Star at 5′ against Gaia 10.393 (Source: live service, 2026-11-14).

| Field | Measured motion | Reference | Source |
|---|---|---|---|
| Barnard's Star, 5′ | 11.37 ± 0.47 ″/yr | Gaia 10.393 ″/yr | live service, 2026-11-14 |
| ε Indi B, 5′ | 4.59 ± 0.49 ″/yr | Gaia 4.684 ″/yr | live service, 2026-11-14 |
| Barnard's Star, 20′ | 10.66 ″/yr toward 353° | Gaia: same motion | docs/PROGRESS.md, state of 2026-11-14 afternoon |
| Barnard's Star, 20′ | 10.7 ± 0.8 ″/yr | Gaia 10.4 ″/yr | docs/PROGRESS.md, QA round 6 |

**Result:** chance makes 0.0 tracks per field in 20 trials on all five 5′ test fields and on ε Indi at 10′, 20′ and 60′ (Source: live service, 2026-11-14).

### Depth and limits

We measured the recall by injection (docs/validation/2026-11-14-v-injection.md): four point sources at a time (FWHM 8.8″, peak a multiple of the stack's robust noise, straight motion in a random direction from a random place) were added to the white stacks of three real 20′ fields (ε Indi B with 4 passes; Barnard's Star and WISE 0855-0714 with 3 passes), 20 per field and cell. A track counts as recovered when it passes within 3″ of the places in at least 3 passes with a speed within 25 %. Share recovered among the tracks the page draws:

| Peak S/N | 5 ″/yr | 10 ″/yr | 30 ″/yr | 100 ″/yr |
|---|---|---|---|---|
| 5 | 0 % | 2 % | 0 % | 0 % |
| 8 | 28 % | 23 % | 18 % | 20 % |
| 12 | 23 % | 30 % | 23 % | 23 % |
| 20 | 38 % | 28 % | 28 % | 28 % |
| 40 | 88 % | 87 % | 87 % | 77 % |

A mover as bright as the clear stars of the field (peak S/N 40) is found in about 85 % of cases at any speed. At S/N 20 it is found, but often only as a weak three-pass track, which is counted and not drawn (counting those too, recall at S/N 20 is 62–88 %). Below S/N 12 most movers are missed, because every pass must detect the source at 8 robust sigma. The limits of the method: fewer than three usable passes give no search; speeds below 3 or above 120 ″/yr are not linked; a body seen in two passes only is not a track; a source on a diffraction spike can still link when the roll angle repeats.

## Pairs changing in place

Two stars closer than the telescope can separate look like one elongated blob. If the brightness of one of them changes between passes, the peak of the blob hops and a search that follows peaks sees a mover where nothing moves. We measure the two components separately.

```
sources: peaks of the white stack of each pass smoothed with a Gaussian of 0.7 × the point-source sigma,
         above the median by 8 robust sigma, local maximum within a box of 0.8 × FWHM (5.6″) each way,
         only where every pass has data and 20″ inside the edge, none within 3 detector pixels of a saturated zone;
         the lists of the passes are merged (a source within 2.5″ of a stronger one is the same source);
         at most 4000 sources
candidate pairs: two sources 3.5–12″ apart
places: the two places are tuned on the mean of the passes (Nelder-Mead on the four coordinates); the start is kept when
        the fit does not improve, a component would move more than 1.5 FWHM, or the separation leaves 1.75–18″;
        from then on the places are FIXED for every pass and every detector
fit:    in each pass, two Gaussians of FWHM 10″ at the fixed places and a flat background, linear least squares in a window
        around the pair, with the covariance of the two amplitudes (overlapping profiles are anti-correlated)
real pair only if:
        both components are measured in every pass with flux/error ≥ 10, each reaches ≥ 15 in at least one pass,
        and ln(flux ratio) changes between passes by ≥ 3.5 sigma of its own error (covariance included)
verdict (from the fits to each detector's stack): "steady" (the ratio changes by less than 10 % and the detectors agree)
        | "component varies (same change in every detector)" (spread of the change between detectors ≤ 0.15 in ln)
        | "change depends on the detector" (a variable component with a colour, or a different wavelength mix in the passes)
        | "not enough detectors" (fewer than two detectors have both passes)
```

The card shows the verdict, the flux of each core per pass, the ratio per pass, the change by detector, the SIMBAD and Gaia names of both cores (within 4″ at epoch 2025.9) and crops of the same piece of sky in every pass. At most 12 pairs are listed, strongest first.

### Chance control

A pair is a candidate when either component's own flux changes by at least 2.5 sigma between passes. The pairs that are not candidates are steady; we run the same measurement and the same test on up to 150 of them (a random choice), and the share that passes, times the number of candidates, is the number of pairs the test makes by chance per field. With fewer than 10 steady pairs the control is not run and the upper limit is the number of candidates. The result carries the Poisson 95 % upper limit.

**Result:** on the 60′ fields the test passes none of its steady pairs (ε Indi 42 tested and 0 passed; Barnard's Star 37 and 0; WISE 0855 18 and 0; R Draconis 63 and 0; live service, 2026-11-14).

Depth and limits: a pair closer than 3.5″ or wider than 12″ is not tested; both cores must be strong in every pass (flux/error at least 10), so a faint core is never judged.

## Film search inside a pass

Asteroids and comets move too fast for any stack: in each visit they stand somewhere else. We search the visits of one pass, one frame per visit, for sources that are not in the static sky, link them into tracks with a motion model that knows the Earth's orbit and the telescope's orbit, and keep only tracks that chance does not make. It works on fields up to 60′ across and on the middle pass of the field; nothing about any body is given to it.

### Frames

```
exposures: detectors D1–D3; flagged pixels dropped except OUTLIER (the pipeline sets that flag on asteroids)
film grid: step = max(3.075″, field size / 600) (6″ for 60′)
frame of a visit: every exposure put on the grid and smoothed to a point source (FWHM 3.2″);
                  the frame is the median of the visit's exposures (a cosmic-ray hit is in one exposure and drops out,
                  a body is in all of them); at least 3 exposures per visit (when visits typically have that many),
                  at least half of the field covered
alignment: the frames of one detector are shifted onto their median, at most 2 grid points
static sky: median of the detector's frames at each place (at least 3 frames and 30 % of them)
motion map: z = (frame − static) / robust scatter of the difference
masks: places where the static sky shows a source above 5 sigma (grown by 1 grid point);
       halos of saturated stars (a static blob of equivalent radius ≥ 150″, grown by 0.4 × its radius);
       2 grid points at the edge of each frame's own coverage
```

### Candidates

```
candidates: local maxima (5 × 5 window) of the motion maps at z ≥ 5, where the frame minus static is at least
            0.74 × the static sky above its median (an undersampled star never subtracts cleanly)
```

The 5000 strongest candidates go on to verification.

### Verification

A candidate must show real light at its place in the exposures of its own visit that the same place does not show when the body was elsewhere.

```
on:   PSF photometry at the candidate's place in every exposure of its visit (same detector)
off:  at the same place, the median of the 9 fluxes nearest in wavelength (within 12 %, at least 4 qualify) in exposures of
      the same detector taken at least 0.2 day from the visit, from every pass
flux: mean over the visit of (on − its own off); error from the spread of the differences and the error of off
verified alone:    every exposure positive and the flux at ≥ 4 sigma
linkable:          every exposure positive and ≥ 3 sigma (a track must then reach 5 sigma together, see below)
control:           the same measurement 40″ to the side of every 8th candidate
```

### Linking by an orbit

```
observer: Earth (ERFA epv00) + spacecraft (X_SC, Y_SC, Z_SC of each exposure, mean over the visit)
pairs of linkable candidates of different visits, up to 5 days apart, moving 3–200 ″/h;
hypotheses: distance D uniform in 1/D from 0.4 to 6 au (step 0.04 per au), radial speed −48…+48 km/s (step 4)
Kepler (two-body) orbit through the two places; kept: 0.8 au < a < 8 au and e < 0.6; light time ignored
members: candidates of other visits within 5″ of the prediction under ONE hypothesis
group fit: one visit contributes one member; a member farther than 10″ from the fitted orbit is dropped;
           the misfit of the track must be ≤ 5″; a source belongs to one track (the longer one keeps it)
track: significance = Σ (significance of each member) / √n  must be ≥ 5 sigma
```

Light time is ignored because it shifts a body along its orbit by a nearly constant amount, under 0.3″ of change over days. The straight line is wrong over days: the Earth's motion bends the path of a main-belt asteroid on the sky by 10–25″ in three days, and the telescope's orbit moves the apparent place by about 5″ at 2 au.

Recovery: a track is followed into the visits of its time span (plus half a day) where no candidate was found. At the orbit's predicted place the motion map counts when it reaches z ≥ 3.5 within 1.5 grid points; a body of 6 sigma is often only 4–5 sigma in one visit.

A straight-line link runs beside the orbit link, for bodies the orbit grid does not cover: pairs of the 1000 strongest candidates moving 3–200 ″/h, members within 4″ of the line, rms about the line at most 2″, at least 4 found visits. Such a track is drawn only when it shares no candidate with an orbit track, and is marked not verified.

### Chance control

Chance moves the candidates to chance places, keeping their times and visits, and links and recovers them in exactly the same way. For the orbit link the 3 sigma candidates are mirrored at random and shifted by about 1′ (12 trials); for the straight-line link they are put at uniform random places of the field (40 trials). A track is drawn only when it is seen in at least as many visits (found plus recovered) as the shortest length that chance makes less than 0.15 times per field, and never in fewer than 4 visits (3 found).

### Check against known bodies and distance

A track that lies within 30″ of a known body's predicted place (median over the frames they share, in at least half of the track's points) is that body (the list of known bodies is described in the next section). A track no known body explains is a lead. The distance comes from the bend of the path and is shown as an interval: the range of distances whose fit stays within one sigma of the best (1.5″ per coordinate) over the 0.4–6 au grid. Orbital elements a and e are not shown, because an arc of two to three days does not determine them.

[figure: film-ecliptic-dec2025 | the 60′ ecliptic field in motion-only view, known bodies ringed, December 2025]

The film of the 60′ ecliptic field (RA 169.8917, Dec +4.3514; pass 2025-12-02 to 2025-12-20, MJD 61011.71–61029.77; D1–D3, 24 visits, 178 exposures of which 166 were read) was searched blind. Of the 8 known bodies with at least 4 frames inside the film and V ≤ 19.1 the search recovered 6, each matched to JPL positions within 2.5″, and all 6 drawn tracks are matched to a known body.

**Result:** the film search recovers 6 of 8 known bodies blind with 0 unexplained tracks (live service, /api/v1/film/48c5bbb2c5e028b847075c51/tracks, product `filmsearch` version t4, 2026-11-14 21:04 UTC).

| Body | V | Result |
|---|---|---|
| (9952) 1991 AK | 18.1 | found, 11 visits, 19.3 sigma |
| (43959) 1997 CB26 | 18.4 | found, 12.6 sigma |
| (7948) Whitaker | 18.5 | missed (only 4 frames inside the film) |
| (11020) Orwell | 18.8 | found, 11.9 sigma |
| (9078) 1994 PB2 | 18.9 | found, 11.6 sigma |
| (4759) Aretta | 19.0 | found, 7.1 sigma |
| (5575) Ryanpark | 19.1 | missed |
| (22522) 1998 EF6 | 19.1 | found, 5.7 sigma |

**Result:** the chance control (12 trials) makes 0.0 tracks of at least 4 visits and 1.83 of 3 visits, which is why 4 visits is the shortest length drawn (live service, same product).

**Result:** of the four bodies of the first validation of this field (9952, 9078, 11020, 43959) the search found 4 of 4 blind with 0 false tracks (docs/PROGRESS.md, film search version t4).

### Depth and limits

The faintest body recovered is V 19.1 (5.7 sigma); a body needs at least 4 visits of the pass (3 of them with a candidate verified at 3 sigma), so a body with only 4 frames inside the film can be missed. Orbits are limited to 0.8–8 au semi-major axis and eccentricity below 0.6: a comet on a parabola, or a body beyond 8 au, is not linked by the orbit and can only appear as an unverified straight-line track. Rates are limited to 3–200 ″/h. The time sampling is the survey's: a few visits a day with gaps of days. Very bright stars make halo candidates around them, which the halo mask removes.

## Known bodies in a film

For every film we ask which known asteroids and comets should be in it, so that the page can ring them on each frame and a track can be named.

```
candidates: JPL Small-Body Identification at the middle of the pass, over the field widened by 40 ″/h × half the pass
            (at most 3° half-width); V ≤ 20.5; kept when the straight-line track over the pass comes within 0.15° of the field
            (at most 200 bodies, brightest first); 3I/ATLAS is always asked of Horizons, since the Identification does not list it
positions:  JPL Horizons, geocentric, at the time of every frame (the telescope is 700 km up: 1″ of parallax at 1 au)
expected:   the frames whose grid holds the predicted position
seen:       an expected frame counts as seen when the motion map shows z ≥ 4 within 2 grid points of the predicted place
stack:      Σ z / √N over the expected frames at the predicted pixel; chance = the same sum at four places 20 grid points
            aside (± 20 columns, ± 20 rows), combined as their rms
planes:     the number of SPHEREx detector images (all detectors) that hold the body, from the exposure index
            (counted for bodies up to V 19.5)
```

The plane count uses the ephemeris every 30 minutes, interpolated to each exposure's mid-time and tested against that exposure's footprint, which is how IRSA's Moving Object Search Tool counts planes. For four asteroids crossing the ecliptic field on 14-20 December 2025 the counts are equal plane for plane:

| Body | IRSA MOST planes | Our planes |
|---|---|---|
| (9952) 1991 AK | 119 | 119 |
| (43959) 1997 CB26 | 107 | 107 |
| (9078) 1994 PB2 | 118 | 118 |
| (11020) Orwell | 121 | 121 |

**Result:** the plane counts equal IRSA's for all four bodies (docs/validation/2026-11-14-v-most.md). The stack along a body's track is a detection test of that body, not astrometry of it; a body whose frames number only a few is judged by its few frames.

The next part, Bodies beyond Neptune, describes the searches for bodies too faint and too slow for any of these.
