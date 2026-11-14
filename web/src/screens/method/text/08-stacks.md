# Cleaning and stacks

Every moving picture on this site rests on one product: for every pass of a field, one clean stack per detector and per named band, on a common north-up grid, with the saturated share and the weight. Raw frames do not show motion, because each exposure sees the target at another wavelength, angle and pixel grid, so stepping through raw frames shows the blink of the spectrum and the jitter of pixels instead of a shift of a third of a pixel. We clean each exposure, deposit its pixels onto a finer grid, reject what only one exposure sees, and register the passes to each other. Result: Barnard's Star moves 10.08 ″/yr toward 355° and ε Indi B 4.58 ″/yr toward 122° in the stacks of three and four passes.

## Background and masks

Before an exposure enters a stack we remove its background and mask what cannot be trusted. We touch nothing else: there is no flat-fielding, no background model and no PSF matching beyond what the SPHEREx pipeline did.

### Background

- **Per exposure:** we subtract the clipped median of the usable pixels of the window: the median, then three rounds of a 3σ clip (σ is 1.4826 times the median absolute deviation) and a new median.
- **By wavelength, from 10′ up:** in fields of 10′ and larger we also subtract what depends on wavelength alone. The usable pixels are divided into 40 bins of equal count by their own wavelength, and the clipped median of each bin is subtracted (a bin needs at least 10 pixels, and the exposure at least 400). The helium airglow line at 1.083 µm is a stripe across D1 that this step removes.
- **ZODI:** the zodiacal-light plane of the file is not used for pictures; the two subtractions above remove the smooth sky.
- **Noise and weight:** the single-pixel noise σ of an exposure is 1.4826 times the median absolute deviation of its background-subtracted usable pixels. Its weight in every stack is 1/σ².

Source: constants in the code of the cleaning step (`engine/clean.py`).

### Masks

A pixel is usable when it has a value, is not saturated, carries no always-masked flag, and carries no far-masked flag unless it lies within 6 pixels of a bright core (the flag lists are in the Flags section of Data and reading):

```
usable = finite(value) & !saturated & !(flags & BAD_ALWAYS) & !((flags & BAD_FAR) & (distance to nearest bright core > 6 px))
saturated = (flags & OVERFLOW) | (no value and no bad-pixel flag explains it)
```

The distance is to the nearest saturated pixel of the exposure, so every bright star in the field is protected, not only the one at the centre. A pixel outside an exposure's footprint is "no data". For searches for moving objects the OUTLIER flag is not masked.

## The splat stack

The 6.15″ pixel is larger than a star image, and the exposures of a pass are dithered at random. So each native pixel deposits its value onto a finer grid, and we normalise over all exposures at once. Interpolating each exposure first and averaging afterwards blurs the stars.

[figure: raw-to-video | Raw exposures of one field, then the stack of one pass, then the passes as a video]

The grid is a north-up tangent plane centred on the field, with east to the left. Its step is 1.5375″ (a quarter of a detector pixel) up to 20.5′ and 3.075″ above.

```
for exp in exposures of (pass, detector):
    u, v = sky_to_grid(position of each usable pixel through the SIP WCS)
    w = 1 / sigma(exp)^2
    deposit w * value bilinearly on the 4 grid points around (u, v) into NUM; w into DEN
NUM, DEN = gauss(NUM, sigma_k), gauss(DEN, sigma_k)      # one smoothing of the sums, same Gaussian, cut at 3 sigma
stack = NUM / DEN;  weight = DEN
```

The kernel width depends on how many exposures the stack has:

| Stack | Kernel σ | Why |
|---|---|---|
| 16 or more exposures | 0.35 detector pixel | the sharpest kernel without a lattice: white-frame FWHM of isolated stars 10.7″ → 9.2″ |
| fewer than 16 exposures | 0.50 detector pixel | a narrower kernel leaves a lattice at the detector-pixel pitch in thin stacks, which would blink between passes |

A named-band stack uses the same deposit, but a pixel goes into a band only when its own wavelength lies in one of the band's windows, and the kernel is chosen by the number of exposures that contribute to the band, counted over all detectors. The seven bands (windows are half-open, in µm):

| Band | Windows |
|---|---|
| Starlight | 0.75–1.10 |
| Water vapor (H₂O) | 1.30–1.50, 1.80–2.00, 2.55–2.90 |
| Methane (CH₄) | 1.62–1.76, 3.20–3.50 |
| Water ice | 2.90–3.20 |
| Dust (PAH) | 3.20–3.40 |
| Carbon dioxide (CO₂) | 4.20–4.35 |
| Carbon monoxide (CO) | 4.55–4.85 |

Source: the band table of the service (`data/bands.json`).

## Cosmic-ray test

A cosmic-ray hit is present in one exposure and nothing is at that sky place in the others. Hits are small (137 of 160 traces cover one pixel), unflagged and of any brightness (up to 600 MJy/sr in wide fields). They are sharper than a star in D4–D6 but not in D1–D3, and more frequent in the long-wavelength detectors (of 195 rejected pixels, D4 35, D5 40, D6 117). The usual image-based detector L.A.Cosmic (astroscrappy) was tested and rejected: with a pixel this large it flags 32 thousand star pixels per exposure in D1–D3.

Our test is a leave-one-out comparison. For every exposure of a (pass, detector) we compute the stack of all the other exposures and ask, pixel by pixel, whether this exposure alone sees something the others do not:

```
others = (NUM - deposit(exp)) / (DEN - weight(exp))      # the stack WITHOUT this exposure, only where the others cover the place
o      = others at the pixel's place;   pk = maximum of others within one native pixel
sigma  = 1.4826 * MAD(exp.img[usable])
hit    = (val - max(o, 0) > 8*sigma) & (val > 5*max(pk, 0))    # brighter than anything the others see
hit   |= (o - val > 8*sigma + 5*abs(o))                        # or a dip
keep_source = (o > 5*sigma) & (val < 20*pk)                    # a source is never rejected as a hit
reject = hit & ~keep_source
remove the rejected pixels' deposits from NUM and DEN at once
```

Because the rejected deposits leave the sums at once, the exposures tested after it see a stack without the hit, and a hit shielded by a neighbouring hit is still found in one pass over the exposures. The comparison is always with the stack without the exposure itself, so a bright trace cannot raise the stack it is compared with.

**Result:** in the 7′ field of Barnard's Star 71, 87 and 102 pixels were rejected in the three passes (D6 most) and in the 7′ field of ε Indi B 382, 333, 165 and 544 in the four passes (D5 and D6 most) (Source: docs/validation/2026-11-14-m3-field.md).

## Streaks and halos

### Streaks of one visit

Some D5 and D6 visits of ε Indi B carry a faint streak (0.1–0.2 MJy/sr, 3–4 pixels wide, more than 25 long) along the row toward ε Indi A that the pipeline does not flag. The leave-one-out test does not see it, because all exposures of a visit carry it. So, in every (pass, detector) with at least 3 visits, we compare each visit with a reference made of the other visits and drop regions that only one visit has:

```
img[v] = splat(visit v), weight 1;   ref = percentile(img, 30, over visits)
d = gauss(img[v] - ref, 1 px) - median;   r = gauss(ref, 1 px) - median          # px = one detector pixel
core = grow(r > 5*noise(r), 1 px)                       # source cores are not checked
odd  = ~core & (|d| > 2*max(r, 0))                      # a wing changes with wavelength, not by a factor of three
drop = regions of (odd & |d| > 3*noise(d)) with max |d| > 5*noise(d) and an area of at least 16 detector pixels
```

The test runs only in fields of 12′ or less, because above that a streak is fainter than the noise in a white frame.

**Result:** in the 7′ field of Barnard's Star the visit test dropped 1,918, 461 and 1,230 pixels in the three passes, and in the 7′ field of ε Indi B 6,070, 4,544, 1,378 and 4,324 pixels in the four passes (Source: docs/validation/2026-11-14-m3-field.md).

### Halos of bright stars

The diffraction spikes and rings of a bright star turn with the roll angle between passes, and they imitate sources and motion. We deal with them where the stacks are used, in four steps:

- A saturated zone is a connected region where the saturated share (see Saturation, white frame and colour) exceeds 0.5. A zone of at least half a detector pixel is one source, placed at the centre of the zone weighted by the saturated share, however wide its flat top.
- A peak of the smoothed stack within 2 detector pixels of such a zone is not a source of its own.
- Every other source must stand 8 median absolute deviations above a local background, the median in blocks of 60″ smoothed back to the full size (the search between passes).
- In a film of one pass, a static blob of equivalent radius of at least 150″ is treated as a halo, and its mask is grown by 0.4 times its radius.

A hard radius mask around bright stars would fail, because ε Indi B lies in the halo of ε Indi A at 1°; these rules look at the shape of the light, not at a distance.

## Saturation, white frame and colour

### Saturated share

The saturated share of a grid pixel is the weight of saturated pixels over all weight, for the detectors of the white frame only:

```
sat = sum_w_sat / (sum_w + sum_w_sat)        # over the detectors of the white frame; sat > 0.5 is drawn white
```

Computed over all detectors, a detector with only 3–4 exposures would turn isolated spots white far from any star. The saturated weight is smoothed with the kernel of a thick stack (0.35 detector pixel).

### White frame

The white frame of a pass is the mean of the detector stacks over CDET, the detectors that have at least 4 exposures in every pass. Otherwise the wavelength mix would change between passes and the whole field would breathe. A pixel has a value only where every CDET detector has one.

[figure: wall-detectors | The six detector stacks of one pass side by side, with the detectors of the white frame]

**Result:** the white frame of Barnard's Star comes from D1–D6 and that of ε Indi B from D2, D3, D5 and D6 (Source: docs/validation/2026-11-14-m3-field.md).

### Spectral colour

The colour of a pixel is the colour of its wavelengths. We build the red, green and blue channels from the CDET detector stacks with a 3 × 6 matrix, so colour needs no new stacking. Column d of the matrix is the mean rainbow colour over the wavelength range of detector d (32 samples; detectors not in CDET get 0), and every row sums to 1 over the CDET detectors, so a flat spectrum is grey. The rainbow runs from violet at 0.75 µm to red at 5 µm:

```
f_log = (ln λ - ln 0.74) / (ln 5.02 - ln 0.74);   f_lin = (λ - 0.74) / (5.02 - 0.74)
hue   = 0.74 - 0.78 * (0.35*f_log + 0.65*f_lin);   rgb = hsv(hue mod 1, 0.88, 1.0)
```

For every pass we measure a white balance on field stars: kR is the median of G/R and kB the median of G/B over the pixels with G above 2.5 MJy/sr that are not saturated, so a typical star is grey. The Views section of this page says how the picture uses it.

## Registration

The passes are months apart, and the pointing of SPHEREx is good but not perfect. We register every pass on pass 0 with the field stars of the two white frames.

1. In a white frame we take the local maxima of the stack smoothed with 1 grid pixel that stand 10 noise levels above the median, in a window sized to a star of 9″ FWHM, and skip any whose window holds a saturated share above 0.05.
2. Each star centre is refined with an iterative Gaussian-weighted centroid, which converges to the centre of a symmetric profile (a box centroid would not).
3. Every star of the current pass is matched to the nearest star of pass 0 within 2″. At least 3 matches are needed; the shift is the median offset, and the scatter is 1.4826 times the median length of the residuals after the shift.
4. The shift is applied as a cubic-spline shift of the smoothed NUM and DEN of every layer and of the saturated weight. They are band-limited by the smoothing, so this equals depositing at shifted positions.

**Result:** in Barnard's Star the shifts of passes 2 and 3 are (+0.05″, +0.02″) from 24 stars and (−0.00″, +0.01″) from 24 stars, with scatter 0.14″ and 0.18″; in ε Indi B the shifts of passes 2–4 are at most 0.10″ in either axis, from 13–16 stars, with scatter 0.18–0.26″ (Source: docs/validation/2026-11-14-m3-field.md).

## Field sizes and grid rule

A field is one of five sizes. The grid step is 1.5375″ up to 20′ and 3.075″ at 60′, and the grid side is a multiple of 4 grid pixels, that is a whole number of detector pixels.

| Field | Grid step | Grid size | Visit test | Wavelength background |
|---|---|---|---|---|
| 2′ | 1.5375″ | 80 | yes | no |
| 5′ | 1.5375″ | 196 | yes | no |
| 10′ | 1.5375″ | 392 | yes | yes |
| 20′ | 1.5375″ | 784 | no | yes |
| 60′ | 3.075″ | 1172 | no | yes |

- **At most 800 exposures per field.** If more overlap, we thin the set evenly in time and keep every pass and every detector represented; the status line says so.
- **Wide stacks are for display, not astrometry:** the shift of a star measured with a crude centroid on a 60′ stack is biased, so motion is measured on the narrow frames.
Source: grid rule in the code of the field grid (`grid_for_fov`).

## Streaming build and quick frame

We never hold all exposures of a field in memory, and a person should see a picture long before the last exposure is read.

1. **Quick frame first.** For each pass and detector we take a few exposures spread evenly in time: 4 up to 10′, 3 up to 20′ and 2 at 60′. We stack only white light with the cosmic-ray test, with a wider kernel because the stack is thin: 0.5 detector pixel with 4 exposures, 0.7 with 3, 1.0 with 2. The quick frame is shown white, without colour, until the full frame arrives, because a quick colour changes static stars between passes.
2. **Per pass, per detector.** The exposures of one detector are read concurrently while the previous detector is being stacked. One detector group at a time is held in memory. For that group we run the cosmic-ray test, the visit test (fields of 12′ or less), and the final sums of the detector, of the seven bands, of the saturated weight and of the flag census.
3. **End of a pass.** The detector layers are smoothed, the band sums are smoothed with their own kernel, the pass is registered on pass 0, and its parts are published: the picture (red, green, blue as float16 with the saturated share), the six detector stacks with their model noise 1/√Σw, the band stacks and the flag census. The next pass is read while this one is published. After all passes the Difference between passes is computed (see Views).

A field is admitted to the job queue with a memory estimate of 400 MB plus 132 float32 layers of its grid, about 1.1 GB at 60′.

**Result:** a cold build of the 7′ field of Barnard's Star took 30 s (reading 2 s, computing 28 s) and of ε Indi B 49 s (reading 16 s, computing 32 s); a field already built opens in 2 ms to 1.7 s on the server (Source: docs/validation/2026-11-14-m3-field.md and docs/PROGRESS.md, state of 2026-11-14).

## Validation of the stacks

We validate the stacks on real exposures of the two test fields where the answer is known from catalogues: Barnard's Star and ε Indi B, each at 7′ over all passes. The report is `docs/validation/2026-11-14-m3-field.md`.

[figure: pendulum-barnard | Barnard's Star in the white frame of the three passes, with its catalogue path]

| Check | Barnard's Star | ε Indi B |
|---|---|---|
| Field and exposures | 7′, 402 exposures, 3 passes | 7′, 496 exposures, 4 passes |
| White frame | D1–D6 | D2, D3, D5, D6 |
| Motion measured in the stacks | 10.08 ″/yr toward 355° | 4.58 ″/yr toward 122° |
| Catalogue (SIMBAD place, proper motion and parallax) | 10.39 ″/yr toward 356°, parallax 547.0 mas | 4.68 ″/yr toward 122°, parallax 270.7 mas |
| Rms of the places about the fit | 0.49″ | 0.05″ |
| Target place per pass against the catalogue | within 0.6″ | within 0.6″ |
| FWHM of isolated unsaturated stars, white frame, pass 1 | 8.8″ (9 stars) | 8.8″ (7 stars) |
| Weight lattice, largest relative rms at the detector-pixel period in a detector stack | 4.6 % | 10.9 % |

**Result:** the stacks reproduce the proper motion of ε Indi B to 0.10 ″/yr (4.58 against 4.68) and of Barnard's Star to 0.31 ″/yr, 3 % low (10.08 against 10.39), with the direction within 1° (Source: docs/validation/2026-11-14-m3-field.md).

**Result:** isolated unsaturated stars have a FWHM of 8.8″ in the white frame in both fields (Source: docs/validation/2026-11-14-m3-field.md).

The weight lattice is the relative rms of a stack's weight in the band of spatial frequencies around one cycle per detector pixel (0.8–1.25 of that frequency), measured in the central half of the field and taken as the largest over all detector stacks of all passes. It is highest in the thin stacks, which have the fewest exposures, and that is why a wider kernel is used there.

