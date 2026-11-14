# 2.4 Spectra

Level-2 data contain no ready-made spectrum: each exposure sees an object at one wavelength. The spectrum is assembled by measuring the
object in every exposure and plotting flux against wavelength. "On the fly" means: on a click, for any point of the sky, with no
catalogue needed; for a moving object, measured where it was at the time of each exposure.

## 2.4.1 PSF photometry of one exposure

```
(ra_t, dec_t) = object position at the exposure time     # fixed place; or proper motion + parallax; or a track interpolated in time
x, y          = WCS.all_world2pix(ra_t, dec_t)
stamp         = IMAGE[7×7 around (x, y)];  var = VARIANCE[same]   # the photometry window is read with VARIANCE (~100″ box)
ok            = finite & !(flags & BAD)        # for a moving body OUTLIER is NOT bad
skip if a core pixel (r < 1.6 px) has OVERFLOW or no value      → point "saturated core"
skip if fewer than 20 usable pixels                             → "no usable pixels"
psf           = PSF of the detector zone, shifted to the sub-pixel position, pixel-integrated
A, B          = argmin Σ ok·(stamp − A·psf − B)² / var           # linear 2×2
repeat ≤ 3:   ok &= |stamp − A·psf − B| < 6·sqrt(var + (0.1·A·psf)²);  solve again
drop if the PSF stands on < 80 % good pixels                    → "little of the PSF on good pixels"
flux_mJy      = A · Ω_pix(local WCS) · 1e9;  error = formal error of the fit
spectrum:     drop a point > 5 robust σ from the median of its 7 neighbours in wavelength → "deviates from its neighbours"
```

Nothing is aperture-corrected: the PSF fit measures total flux. The background is a constant in the 7×7 window (a neighbour inside the
window spoils a point; the residual rejection is the only protection).

**Validation:** the star 2MASS J17572542+0446559 (RA 269.3559, Dec +4.7822, J 11.53): 347 points from 358 exposures
(2 overflow, 8 bad core pixels, 1 out of line), scatter 1.1–2.6 % in D1–D4 and 3.4–3.9 % in D5–D6, detector joins within 0.95–1.02;
the service gave 362 usable of 375 with catalogue ratios J 0.979, H 0.992, Ks 1.028, W1 0.979. Ten isolated stars J 10.8–12.9: 40
comparisons, median 0.98, scatter 3 %, worst 7 %. Five faint stars J 14.1–15.2: noise 0.05–0.15 mJy per point, 14 of 20 within 10 %.
ε Indi B: 414–421 points; ratios 0.91–0.92 (J), 0.93–0.94 (H), 0.98–0.99 (Ks), 1.18–1.20 (W1); peaks near 1.3, 1.6, 2.1 and 4 µm.

**Bright limit:** OVERFLOW from ~70–90 MJy/sr in D1–D3, ~50 in D4. J 9.4 loses 194 of 348 exposures, J 10.8 loses 12 of 345;
reliable fainter than J ≈ 10.5.

## 2.4.2 Saturated sources: no SPHEREx spectrum

When the core is saturated in more than half of the exposures, the system says once "Saturated in every exposure: no SPHEREx spectrum"
and shows only catalogue points. Three ways to measure from the wings were tried and failed validation against 2MASS (PSF fit to the
wings 0.68–1.54 of catalogue; flagged near-core pixels biased by −13 % to +36 %; unflagged halo profile 0.70–1.16, and 1.10/1.69/1.81 in
J/H/Ks for Barnard's Star; the wings-only fit on unsaturated stars with the core masked: 0.80 ± 0.35). It is not built. Affected test
targets: Barnard's Star, RW Aurigae, R Draconis, ε Indi A.

## 2.4.3 The live spectrum (as it is measured)

A spectrum job publishes each exposure's point the moment it is fitted, in observation order (index `i` = position in the time-ordered
list of exposures that cover the place). The page polls with a cursor (`since = points already held`) and draws points one by one at a
steady rate even when they arrive in bursts, each coloured by its detector, the newest circled. For the current exposure the job also
publishes the stamps of the fit: the file's raw 21×21 pixels around the source (no value = white), and three 7×7 stamps (pixels −
background, flux × PSF, residual) with the used-pixel mask. Afterwards a play button replays the build-up and a slider scrubs through the
exposures. Measured timing: first points 0.8–1.9 s after the click on a warm field; last point 3.3–15 s; cached 0 s.

## 2.4.4 Check against catalogues (synthetic photometry)

The cleaned spectrum averaged under the filter curves of 2MASS J, H, Ks and WISE W1, W2 (photon counting; curves from the SVO Filter
Profile Service, stored in the repository) and compared with `F0 · 10^(−0.4 m)` (zero points 1594, 1024, 666.7 Jy; 309.5, 171.8 Jy).
Catalogue magnitudes from IRSA (2MASS PSC, AllWISE) at the position moved to each survey's epoch; a band is used only with quality A or
B; a neighbour of similar brightness within 8″ makes it a blend (no check); a band the spectrum does not cover without a hole is left out
(W2 extends to 5.3 µm beyond SPHEREx: shown, not compared). The comparison arrives after the points (it can take 15–45 s when IRSA is
slow) and never delays them.

## 2.4.5 Binned spectrum, controls and upper limits (faint sources)

For every position spectrum: bins in ln λ with 12 % steps; in each bin the mean flux and its statistical error; the same method at 6
control places on a ring of 40″ around the source in the same exposures; `net = flux − median(control means)`,
`sigma = hypot(err, scatter of the controls)`, detected when `net > 3σ`, else a 3σ upper limit. Drawn as points with error bars and
downward arrows. Validation: WISE 0855−0714 detected at 4.0–4.5 µm (0.40 mJy, 8σ) and 4.5–5.1 µm (0.48 mJy, 11σ; W2 0.47 mJy), upper
limits elsewhere.

## 2.4.6 Does it change? (matched wavelengths)

Exposures of different passes sample different wavelengths, so only exposures in the same resolution element are compared:

```
bins: cut at detector edges (1.11, 1.64, 2.42, 3.82, 4.42 µm); inside each detector ln-spaced bins of width λ/R
      (R = 41 D1–D3, 35 D4, 110 D5, 130 D6), keyed by the exposure's own detector
per bin and pass: inverse-variance mean flux and error
pair of passes: earliest and latest sharing ≥ 3 bins
z_i = (F_b − F_a)/sqrt(σa² + σb² + (0.03·sqrt(Fa² + Fb²))²)        # 3 % = assumed relative-calibration floor
χ² = Σ z_i², dof = number of bins; ratio error scaled by sqrt(χ²/dof) when > 1
change stated only if Wilson–Hilferty normal equivalent of χ² > 4 AND |weighted mean ratio − 1| > 10 % AND flux > 3σ in both passes
```

Otherwise "no change found" with the ratio and error, or "not detected" / "not tested: the target is not detected in the pictures"
(WISE 0855). Limits: flat spectrum assumed inside a bin; exposures of one field mix pipeline versions (v20, v24, v27) without
cross-calibration; "no change found" means no change larger than the stated error.

## 2.4.7 Portrait (the object's own image, coloured by its spectrum)

56 layers from 0.76 to 4.95 µm equally spaced in ln λ; layer L takes every exposure with weight `exp(−(ln λ − ln λ_L)²/(2·0.05²))`
(weight < 0.03 ignored); each exposure deposited on a 64×64 grid of 1.5375″ (98″ wide) **co-moving** (the object, at its place in that
exposure, at the centre, so a star is not smeared by its proper motion); sky = clipped median of the window; bad, overflow and empty
pixels left out; sums smoothed once with σ = 0.42 native pixel. Thumbnail = the layers added with the rainbow colour of their wavelength,
normalised per pixel over the layers that have a value (a flat spectrum is white). The product carries a saturated-share map on the same
grid (`255 × share of exposures in which the pixel is saturated`); the page paints it **white**, never a hole. Until the portrait
arrives the page cuts an image from the picture stacks it already holds.

## 2.4.8 Spectrum of a moving body: on − off

`on` = PSF flux where the body was at each exposure (track positions interpolated in time; OUTLIER kept); `off` = median of the 9 fluxes
nearest in wavelength (within 12 %) at the same sky place in exposures of the same detector taken at least 2 days (film candidates:
0.2 d) before or after (at most 240, spread evenly in time); fewer than 4 references → no value. A track longer than 10′ is measured
without off-exposures. Subtracting `off` is mandatory in crowded fields (Corbin in D3: 0.45 mJy without, 0.33 with). Validation: (4008)
Corbin D2 0.36 ± 0.01, D3 0.33 ± 0.04, D5 0.14 ± 0.03, D6 0.14 ± 0.06 mJy; (15101) 2000 AY150 D2 0.26 ± 0.02, D5 0.48 ± 0.07; control
60–90″ aside −0.00 ± 0.01 mJy. Observer for ephemerides is the spacecraft (`500@-163182` in Horizons): the difference from geocentric is
1.1–2.6″ for these asteroids.

## 2.4.9 Thermal signature (one line in a mover's card)

`ratio = mean flux 3.82–4.42 µm (D5) / mean flux 1.10–1.63 µm (D2)` of the cleaned on − off spectrum (≥ 4 points in each), error from
`max(std, median err)/sqrt(n)`; shown with the ratio of sunlight reflected by a grey surface (5772 K blackbody: 0.229). Words: "reflects
sunlight" (≈ 0.23), "shines by its own heat" (clearly above). D2 and D5 expose at the same time, so a rotating body's brightness change
does not enter. Validation: field stars median 0.23 (Barnard 345 stars, ε Indi 101); Corbin 0.39 ± 0.06; 2000 AY150 1.85 ± 0.30; ε Indi B
2.08 ± 0.13; 45 Eugenia 0.74–0.81 ± 0.03–0.13. Limit: the ratio mixes heat, red reflectance and own emission; it sorts, it does not
identify.
