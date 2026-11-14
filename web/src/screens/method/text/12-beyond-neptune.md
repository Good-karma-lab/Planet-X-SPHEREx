# Bodies beyond Neptune

A body far beyond Neptune is too faint and too slow to show in any single SPHEREx exposure, so we add the exposures along the path such a body would take across the sky: at the right distance its light piles up in one point, at any other distance it smears out. We do this for every distance and every slow motion a bound body can have, in one pass of a field and in all passes together, and we link the finds of different passes into one orbit. We make no claim of a Planet Nine.

## The idea in plain words

SPHEREx rides the Earth round the Sun. A body at a great distance therefore slides across the sky for two reasons: the parallax of our own motion (the farther the body, the smaller the slide) and its own slow orbital motion. Both are known functions of two unknowns, the distance of the body and its motion on the sky. If we guess these two numbers, we know where the body must have been in every visit of a pass, shift every visit by that amount and add them. A real body at the guessed distance lands on the same point in every visit and its light adds up; noise adds up much more slowly. Tried over a grid of guesses, the guess that fits a real body gives a sharp, bright point. A wrong distance puts the visits in the wrong places and the point stays smeared.

That is the distance slider of the Find Planet X page: it shows the sum for the distance and the own motion you set, and the point comes into focus when the slider reaches the body's distance. The distance of a body is therefore read from the picture itself, not from a catalogue.

One pass lasts a few weeks, so the parallax inside it is small and the distance comes out as a wide interval. Half a year later the telescope sees the same place from the other side of the Earth's orbit, and the body has jumped. Adding all the passes along one orbit makes the search deeper, and linking the tracks of the passes into one orbit fixes the distance.

## Products shared by every search

A pixel is larger than a star image, so a bright star never subtracts cleanly: its residual is the same in every visit and would add up along a track, which is why we mask places where the static sky is bright. The static sky is a mean over the other passes because that is less noisy than one pass's median, and each visit difference is clipped at five sigma so that one visit cannot decide a detection.

The one-pass search, the deep sum and the browser's distance slider all start from the same products, made once per field and pass. The detectors are D1 to D3 (0.75 to 2.4 µm, reflected light) by default; the Find Planet X page can also ask for D4 (2.4 to 3.8 µm) alone or for D1 to D4.

```
exposures of the field   split into passes (a gap of more than 60 days or a length of more than 90 days starts a new pass)
                         and visits (one detector, exposures less than 30 minutes apart)
reading                  each exposure is tried three times; the exposures that could not be used are counted and the result says
                         "N of M exposures could not be used and were left out"
cleaning                 sky level = clipped median (3 sigma, 3 rounds); for fields of 10 arcmin and more also the part that depends on
                         wavelength alone (median in 40 wavelength bins); flagged pixels are dropped, except OUTLIER, which the pipeline
                         puts on moving bodies
grid                     3.075 arcsec (half a detector pixel); each pixel is spread over the grid with bilinear weights and a Gaussian of
                         sigma 1.85 arcsec (0.6 grid steps)
visit image              median of the visit's exposures; a visit needs at least 3 exposures and a picture covering more than 20 % of the field
static sky[detector]     clipped (3 sigma) mean of the visits of the same detector in ALL OTHER passes; a detector with fewer than two such
                         visits is not used
difference               visit - static sky, minus its own median level, clipped at +-5 sigma (one visit alone cannot make a detection)
mask                     places where the static sky is above 4 sigma of its robust spread, with a margin that grows with the brightness
                         (2 grid points at 4 sigma, 3 at 15, 4 at 60, 6 at 250, 8 at 1000, 10 at 4000 sigma)
```

The static sky is the sky as it looks when the body is somewhere else, which is why we need at least two passes. The exposures of a pass are the searched ones; the others only make the reference. A field with too many exposures keeps whole visits at an even stride: at most 1400 exposures are read, up to 800 of them in the searched pass, and the result says how many were used.

The positions of the telescope come from the Earth (ERFA ephemeris) plus the spacecraft position in each exposure's file header, so the parallax is the one SPHEREx really had.

## One pass and the distance slider

### The hypotheses

A hypothesis is a heliocentric distance d and an own motion mu on the sky. The default range is 30 to 300 au; the Planet Nine searches use 250 to 2000 au. A body bound to the Sun cannot move faster than the escape speed, so the own motion is limited by:

```
|mu| <= mu_scale * 5016 arcsec/day / d^1.5      (mu_scale = 1 by default)
```

The grid is uniform in 1/d and in mu, so that two neighbouring hypotheses differ by at most 3 arcsec in any visit. For every hypothesis we place the body on the line of sight at distance d, see it from the telescope's position in every visit, and add mu times the time since the middle visit. The count of hypotheses is estimated before the grid is built (within a few per cent) and capped at 10^6, about 13 minutes of work. If the asked nearest distance would exceed the cap, the search raises it in 5 % steps until it fits and says so in one line.

### The sum and the noise ceiling

We divide the sum by its own spread because the static-sky noise is shared between visits, so the formal error is too low. The threshold is 1.05 times the noise ceiling because the noise is not Gaussian. The reach of a blind search is set by that ceiling, the price of scanning thousands of tracks instead of one.

```
sum(x) = Σ w · difference(x + offset) / sqrt(Σ w),     w = 1 / sigma²   (sigma of each visit's difference)
```

The sum is divided by its own robust spread, because the noise of the static sky is shared by the visits and the formal error is too small. A point needs at least 60 % of the total weight to be counted. For every sky point we keep the best S/N over all hypotheses and the hypothesis that gave it.

The same sum made on the negated differences shows how high noise alone reaches over this field: the noise ceiling. Real bodies add light, never subtract it, so nothing in the negated sum is a body.

### Candidates

A candidate is a peak of the best-S/N map that is at least 5 sigma and stands above 1.05 times the highest peak of the negated map. A body leaves a ridge of weaker peaks along its track; a peak whose track passes within 6 grid steps (18 arcsec) of the track of an earlier candidate is dropped. At most 40 candidates are kept. Each is tuned on the unclipped differences, read at fractional positions (place and own motion with the distance kept), because a bright body is clipped in every visit and the grid is only good to 3 arcsec. The distance is reported as an interval: where the S/N, as a function of distance, is within 1 of its best.

### Photometry and control tracks

The 12 strongest candidates get a measurement that does not depend on the shift-and-add: PSF photometry (the same 7 by 7 pixel fit as the spectrum) at the candidate's place in every exposure of the searched pass, minus the median at the same sky place in the five exposures nearest in wavelength of the other passes (same detector, at least three of them). A track needs at least 6 points; its flux is the weighted mean, and its error is scaled up if the points scatter more than their errors say. The same measurement is made on four control tracks one arcminute to the north, south, east and west.

A candidate is confirmed when all of these hold:

- the photometry reaches 5 sigma;
- the flux is positive in every detector that contributes at least 4 points;
- the track is at least 12 arcsec long over the pass (a candidate that moves less is a place that is brighter in this pass, not a body; the result labels it so).

The result lists the control S/N values; a confirmed body should stand alone, with the controls well below it.

### The slider

The browser receives the visit differences (one byte per pixel: 128 plus 25 times the difference in sigma, 0 = no data), the offsets of each visit for 48 distances spaced evenly in the logarithm between the nearest and the farthest distance, the weights, and the highest own motion allowed at each distance. The page shifts and adds the differences for the chosen distance and own motion, divides by the spread of the sum and draws the picture. Three sliders set the distance and the own motion east-west and north-south (as a share of the limit). "Show the noise" does the same on the negated differences. The peaks of the scan are listed, and a click sets the sliders to a peak.

[figure: slider-eris | The sum of one pass of Eris's field at three distances: smeared, in focus at the body's distance, smeared again]

## The deep sum of all passes

The highest peaks of the sum are leftovers of the static sky in one pass, lifted by the noise of the other passes: among millions of orbits the other passes always offer a positive fluctuation. Photometry on the exposures themselves tells them apart, which is why the deep sum has a second stage.

One pass has a noise ceiling. A body below the ceiling of every pass can still rise above the ceiling of the sum of all passes, if the visits of every pass are added along ONE heliocentric orbit. This is the deepest search and the slowest job.

A hypothesis is an orbit: a sky point of the middle pass at its middle visit, a distance d, an own motion mu and zero speed along the line of sight. The orbit is carried to every visit of every pass as a two-body orbit and seen from where the telescope was. Only orbits that stay inside the field of every pass are tried, and a point needs at least 40 % of each pass's weight. The sum is made in two levels so that it is affordable:

```
coarse cell   own motion in steps of 3 arcsec over the longest pass: it fixes the shape of the track inside each pass,
              so the visits of each pass are added once per cell
fine step     inside the cell the own motion is stepped so that the place in the other passes moves by 4.5 arcsec;
              the pass images are shifted to where the orbit puts the body and added
distances     uniform in 1/d, 4.5 arcsec apart at the farthest pass
S/N           sum / sqrt(sum of weights), divided by the sum's own robust spread; the same on the negated data
```

Every pass has a field of its own, because a body at 80 au moves about a degree between passes by parallax. The page guesses the centres of the other passes by carrying the place of one pass on a circular prograde orbit in the ecliptic plane at the geometric-mean distance of the range; a real orbit differs, so the page shows the guessed centres and lets the person change them. At least two passes are needed (at most six), each with at least four visits, and the field is at most 1 degree. The job says how many orbits it will try and how long it will take before it starts, and refuses a plan above 40 million orbits.

After the scan:

- the highest peaks of the sum (at least 6 sigma, at most 15) and the highest peaks of the negated sum (the same rules) are tuned on the visit images; peaks within 30 arcsec of a stronger one are one peak;
- PSF photometry along each orbit in every exposure of all passes, minus the same place in the other passes, is measured for every peak of both kinds (negated fluxes negated);
- the photometry threshold is 1.05 times the best negated peak in photometry, never below 5 sigma;
- a candidate needs a flux above the threshold and positive in every pass;
- one source is one candidate: candidates that share a place within 20 arcsec in any pass count once, and the strongest is kept;
- the confirmed candidates (at most four) get four control tracks each, one arcminute aside.

The photometry of a track re-measures every reference exposure, so those measurements are cached.

## Link of passes and the jump

Half a year apart SPHEREx sees a place from the other side of the Earth's orbit, 2 au away, so a body at distance d jumps by up to about 2 au divided by d: 72 arcmin at 95 au, 14 arcmin at 500 au. The size of the jump is the distance.

**Link.** Each confirmed candidate of a pass gives three points of its track (first, middle and last visit of its pass). One two-body orbit is fitted through the points of a candidate of one pass and a candidate of another: six free numbers (the two end points of the arc, the distance and the speed along the line of sight at the first point), with a penalty on orbits whose kinetic energy exceeds 0.9 of the escape value. Seen from the Earth's centre, the fit must have a misfit of at most 6 arcsec (the default; the page allows 1 to 30) and a closed orbit (0 < e < 1) to count as one body. The orbit then predicts the place in the third pass; a candidate within 2 arcmin of it confirms the link, and the three passes are fitted together. The distance error comes from the covariance of the fit, with a floor of 2.5 arcsec on the position error of a track point.

**The jump, as the page shows it.** For a candidate of one pass, the page draws the line of places it can have in the next pass by distance: 40 points between the nearest and the farthest distance that fit its interval. The body is taken at its place with zero speed along the line of sight and the apparent rate the pass measured, split into parallax and own motion for each distance. The candidates of other passes that lie within a corridor of 5 arcmin of the line are listed, each with its distance on the line and whether its own distance interval contains it. A candidate seen in the next pass is then searched or linked at once.


## The Planet Nine prior

The orbit predicted for Planet Nine by Brown and Batygin (2021) has a semi-major axis of about 380 au (300 to 520 au), a perihelion of 300 au, an inclination of 16 degrees and a mass of about 6.2 Earth masses. Its place on the orbit is unknown, so the search must cover every direction a field can look; what the prior removes is the wrong kinds of motion.

With the prior switched on, the deep sum tries only prograde orbits with an inclination to the ecliptic between imin and imax degrees and a speed between fmin and fmax of the escape speed. The Planet Nine runs use 5 to 30 degrees and 0.4 to 0.9. A prograde orbit of inclination i crosses ecliptic latitude b at an angle psi to the ecliptic that follows from the geometry of the place:

```
cos(psi) = cos(i) / cos(b)       (an orbit does not reach |b| > i at all)
```

so each place on the sky has a fixed direction of own motion for each inclination, and a fixed range of speeds. Cells of own motion outside the allowed angle and speed are not tried.

[figure: p9-orbits | The Solar System with the predicted Planet Nine orbit and the fields searched for it]

## What it can and cannot say

- A run on a known body is called blind when the field was centred on it: the search knew the area but not the place inside it, the distance or the motion.
- A blind search reaches a limited depth. A body fainter than that is not found, and a search that finds nothing does not show that nothing is there. The depth on a field is the measured noise limit of its deep sum: the noise ceiling and the bar of that run, printed with every result.
- A candidate is a lead, not a discovery. Before it counts it needs the link of two or three passes, a check against the known bodies (JPL).
- One pass cannot tell distances apart well: the result is an interval, and at several hundred au the parallax inside a pass is small. The jump between passes and the link are what turn the interval into a distance.
- The deep sum takes the speed along the line of sight as zero; the link of passes fits it as a free number.
- The fields searched are small patches of sky. What SPHEREx gives any candidate found elsewhere is its place in three or more passes, its distance from the jump and a 102-channel spectrum.
