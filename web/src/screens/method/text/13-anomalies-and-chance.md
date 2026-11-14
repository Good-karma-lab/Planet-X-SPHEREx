# Anomalies, identification and chance controls

We ask which sources of a field do not have the spectrum of an ordinary star of that field, we look up what the catalogues say about any place we point at, we pick out red sources and brown dwarf candidates, and we measure how often chance alone would produce the result of every search. A finding is shown with its chance number beside its significance, and a track that no catalogue explains is a lead, never a discovery. In the field of ε Indi B the anomaly map ranks the brown dwarf first with a score of 13.8 against a threshold of 2.6.

## The spectral anomaly map

The map answers one question: which sources of this field look unlike the stars around them. It is a background job on a finished field that starts by itself when the field is built, with smooth progress.

### Which sources, and how we measure them

We take the stationary sources of the search between passes with S/N ≥ 12, saturated ones left out, brightest first, at most 300. We add every track of that search, measured along its own path, because otherwise its spectrum would be truncated and it would look anomalous for a false reason. If only two passes cover the field there are no tracks and every source of the first pass counts.

We measure the PSF flux of every source in every exposure of the field (at most 800; a larger set is thinned evenly). Each exposure is opened once and all sources are measured from it. The noise of a stamp is the robust scatter of the good pixels of that exposure, and a track's pixels flagged OUTLIER (the pipeline sets that flag on asteroid pixels) are kept for moving sources.

```
sources:  stationary, S/N >= 12, not saturated, brightest 300  +  every track (measured along it)
measure:  PSF photometry of every source in every exposure of the field (<= 800 exposures)
bin:      24 bins equally spaced in ln(lambda) from 0.75 to 5.0 um
          per bin: median flux and 1.25 * (1.4826 * MAD) / sqrt(n); a bin needs 2 points
          a source needs 60 points and 20 filled bins
shape:    f / sum(f) over the bins
```

### The ordinary star of the field

Temperature and reddening move a stellar spectrum along a smooth family of shapes. We build that family from the field itself. The median shape of the brighter half of the sources (at least 6 sources) is the field's ordinary star. The first two principal components of the bulk (the half of the sources closest to that median shape) describe how ordinary stars differ from it. Missing bins are filled by interpolation in ln λ for the family only.

### Score and threshold

Each source's shape is fitted by a weighted combination of the median shape and the two components, and the score is the root-mean-square misfit in units of the noise. The noise has a floor of 5 % of the model, which keeps the systematic errors of bright stars from dominating.

```
model   = c0 * med + c1 * pc1 + c2 * pc2         (weighted least squares on the source's own bins, 3 iterations)
score   = sqrt( mean( ((shape - model) / sqrt(err^2 + (0.05 * model)^2))^2 ) )
anomalous : score > median(score) + 6 * 1.4826 * MAD(score)       (the field's own scores)
fewer than 12 sources with a spectrum -> "an ordinary star of the field cannot be defined (12 needed)"
```

[figure: anomaly-map | the spectral anomaly map of the ε Indi field: every source's score against the field's threshold, ε Indi B first]

A blend, a variable star or a bad detector region can also make a spectrum odd, and a field made of one kind of star (a cluster) flags more sources; the passport shows which bins drive each case.

## The anomaly passport

The passport says why a source is anomalous, in plain status form. It gives the score against the field's threshold, the residual of each bin in σ against the ordinary star (drawn on a chart), and the up to three bins that carry the largest shares of the squared residual (each above 5 %), such as "excess at 3.9–4.3 µm (+22σ, 16 % of the misfit)" or "deficit at 0.80 µm (−20σ)". It also says whether the source moves, and what the catalogues know about the place (next sections). Anomalies are numbered by score, "anomaly n", and never called discoveries.

## Identifying a place

Identification says what a catalogue calls a place at the date of our data. It runs for any clicked source and for every anomaly.

We ask SIMBAD in a cone of the match radius (6″, plus 12″ for a source that moves) widened by 30″, move each SIMBAD object by its proper motion to the epoch of the data (MJD 61000, late 2025) and keep the nearest one within 6″. Planets are skipped. We ask Gaia DR3 in a 6″ cone at Gaia's epoch (J2016.0), after moving the source's J2000 place by its proper motion, and take the nearest star. If the Gaia archive does not answer, the same cone is read from the Parquet copy of Gaia DR3 (see Cross-checks and exports).

Each answer is explicit: a name and type, `none` (the catalogue answered and nothing is there), or `unavailable` (it did not answer). An answer with an unavailable part is never kept, so a later request asks again; complete answers are kept in memory.

## Catalogue context

Context says what else is known about a place. We ask seven catalogues in parallel, each behind its own circuit breaker with a 12 s deadline, each answering `ok`, `none` or `unavailable` (with the error). Only `ok` and `none` are kept, for 7 days. The default radius is 6″ at epoch 2025.9.

| Part | Fields |
|---|---|
| SIMBAD (Harvard mirror, then CDS) | name, object type, spectral type, bibliography count and link, proper motion, parallax, magnitude (V, else Gaia G, else 2MASS J); planets left out |
| Gaia DR3 (ESA archive, then the GAVO `dr3lite` mirror) | source id, G, BP−RP, parallax ± error, RUWE, proper motion, variability flag (the mirror has no flag and says so) |
| 2MASS PSC (IRSA) | designation, J, H, Ks, photometric quality |
| AllWISE (IRSA) | designation, W1–W4, cc_flags |
| CatWISE2020 (IRSA) | name, W1, W2, proper motion |
| AAVSO VSX (VizieR `B/vsx/vsx`) | name, type, period |
| NED | name, type, redshift |

Surveys of other epochs are searched where the star was then. We ask Gaia first (SIMBAD if Gaia has nothing) for the proper motion and move the place to each survey's epoch: 1999.5 for 2MASS, 2010.5 for AllWISE, 2015.4 for CatWISE. For fast stars SIMBAD is asked in two cones, one of the radius plus 1″ per year of elapsed time and one of the radius plus 11″ per year for objects above 1″/yr, so that Barnard's Star, 4.5′ from its J2000 place, is found.

### Distance

The Object card shows a distance only from a clean Gaia DR3 parallax: RUWE ≤ 1.4 and parallax ≥ 3 mas. The distance is 1000/parallax parsecs, also given in light-years. Without a clean parallax no distance is shown.

## Brown dwarf candidates

A cold brown dwarf is bright at 3.8–5 µm, faint at 1.1–1.6 µm, and moves several arcseconds a year. We call a source a brown dwarf candidate when it is red, moves between passes, and the catalogues do not explain it. The evidence comes from searches the field already has, so nothing is read from the archive again:

- the red sources (next section), with the motion of their long-wavelength peak from pass to pass; a red source moving less than 500 mas/yr is not called a mover, because that is centroid scatter;
- the tracks of the search between passes: a track standing where a red source stands (within 4″ in the track's first pass) is a second, independent detection of the motion, and its S/N per pass and fit rms are reported.

We check at most the 40 moving red sources with the strongest colour. Gaia DR3 gives the same verdict as for every track of the movers search, with Gaia stars moved to the pass dates; SIMBAD is asked at the source's place at 2025.9 within 8″.

| Gaia verdict | Meaning | Candidate |
|---|---|---|
| `none` | no Gaia star near the track (a cold dwarf is too faint for Gaia) | yes |
| `still` | a Gaia star that hardly moves stands there (the red light may belong to a neighbour) | yes, said so |
| `same` | a Gaia star with that proper motion explains it | no, listed as a known mover |
| `pending` | Gaia did not answer | yes for now, asked again |

A source SIMBAD names is listed as known: a "known brown dwarf" when its type is `BD*`, `Y*`, `L*` or `T*` (a recovered WISE 0855 or ε Indi B), otherwise a "known object". The rest are "unidentified" candidates. Moving red sources that no track of the white stacks covers are added to the movers view as tracks of their own, signal in the red bands.

**Result:** WISE 0855−0714 on a 5′ field is found as a moving red source at 7.93 ± 0.61 ″/yr, position angle 280.8°, in the red bands 3.8–5 µm, against 8.15 ″/yr in SIMBAD. Source: live `/api/v1/browndwarfs`, 2026-11-14, the service team.

## Red sources

Red sources are what is bright at long wavelengths and faint at short ones: cold brown dwarfs, dusty and very red objects. We co-add the detector stacks of all passes by inverse variance and find sources on the long-wavelength image (D5–D6, 3.8–5 µm).

| Step | Rule |
|---|---|
| detect | peaks of the long image smoothed with a Gaussian of 0.8 native pixels, minus a 6-pixel background; at least 6 robust σ; 8 px kept clear of the edge |
| saturated cores | left out (smoothed saturation above 0.3 within one native pixel): the short-wavelength light is lost and the colour would be false |
| measure | pass by pass at that pass's own long-wavelength peak, aperture 0.6 native pixel, background ring 2.5 to 4 aperture radii, in D2 (1.1–1.6 µm) and in the long image |
| colour | flux at 3.8–5 µm over flux at 1.1–1.6 µm, from the reddest pass where the source stands at S/N ≥ 5 in the long image; where D2 shows less than 3σ the colour is a lower limit (long flux over 3σ of D2) |
| keep | combined long-wavelength S/N ≥ 6 |
| red | log colour above the median of the field's own sources (those at S/N ≥ 5 in D2) by 4 robust σ, limited to a factor between 2.5 and 4; or seen in the long image (S/N ≥ 8) and not in D2 with a colour above 3 times the median; needs at least 5 comparison sources |
| moves | the long-wavelength peak shifts by more than half a native pixel between the first and last pass |

A moving dwarf may sit on a background star in some passes and beside it in others, which is why the colour is taken in the pass where the source is cleanest. For a mover we fit a straight line to the peaks of the passes (east and north, mas/yr), give its J2000 place on that line, and report the rms of the peaks about it. A large field rings only its strongest red sources (5 on a 60′ field, 8 on a field above about 18′), and the full list is in the Candidates tab.

## Chance controls

Every search has a control that says how often chance alone makes its result. The control runs the same linker or test on data in which chance is the only thing left.

| Search | Control | Rule |
|---|---|---|
| between passes | the moving sources of every pass are put at uniform random places of the usable field, stationary ones kept, the linker is re-run; 20 trials | the result reports the mean number of chance tracks per field and the share of trials with at least one; a track of only three passes with a minimum S/N below 15 is counted as weak and not drawn |
| pairs that change in place | the pair test on a sample of up to 150 pairs of steady sources (neither component changes), drawn at random | the share that passes, times the number of candidate pairs, is the expected number of false pairs per field, with its 95 % upper limit; with fewer than 10 steady pairs the upper limit is the number of candidates |
| inside a pass (film) | the candidates are moved to random places, times and visits kept, then linked and recovered exactly like the real tracks: 40 trials for straight lines, 12 for orbit tracks; the on − off photometry is repeated 40″ to the side of every eighth candidate | a track is drawn only when it is seen in at least as many visits as the shortest length that chance makes fewer than 0.15 times per field, and a track needs at least 4 visits where a candidate was found |
| Find Planet X | the negated differences are searched like the real ones: their highest peak sets the ceiling of noise, their highest photometry sets the photometric bar | a peak must exceed 1.05 times the best negated peak (at least 5σ); its photometry must exceed the larger of 5σ and 1.05 times the best negated photometry, be positive in every detector with 4 or more exposures, and a track must move at least 12″ over the pass; control tracks are measured 1′ to the north, south, east and west of each confirmed candidate (at most four) and must stay below 5σ |
| spectral anomaly map | the field's own scores set the threshold: median + 6 robust σ | inherent |
| Difference between passes | z is calibrated on empty sky in windows of 24 pixels (median and robust width, sources masked) with a 5 % floor on the relative error, so its width on empty sky is 1 | \|z\| above 4 is coloured, and is not a detection |

**Result:** the search between passes makes 0.0 chance tracks per field in 20 trials on all five 5′ test fields and on ε Indi at 10′, 20′ and 60′. Source: live `/api/v1/movers`, 2026-11-14, the service team.

**Result:** the film search of the 60′ ecliptic field (12 trials, scrambled candidate places) makes 1.83 chance tracks per trial with at least 3 visits and 0.0 with at least 4 visits, which is why a track needs at least 4 visits; the straight-line control (40 trials) makes 0.0 at 4 visits. Source: live `/api/v1/film/48c5bbb2c5e028b847075c51/tracks`, 2026-11-14, the service team.

[figure: film-ecliptic-dec2025 | the 60′ ecliptic film of December 2025 with the chance level beside the recovered asteroid tracks]

Each search carries its chance numbers in its own result, and the Validation part lists what we obtain.
