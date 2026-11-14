# Spectra

The SPHEREx Level-2 files contain no ready-made spectrum: each exposure sees an object at one wavelength. We assemble the spectrum ourselves, on a click, for any point of the sky and with no catalogue needed: we measure the object in every exposure that covers it and plot flux against wavelength. For a moving star we measure where it was at the time of each exposure. The spectra of the test stars agree with their 2MASS and WISE photometry to a few per cent.

## PSF photometry of one exposure

For every exposure that covers the place (its footprint, with its margin, holds the place within 20″) we fit a model of one point source to a small stamp of the image. The fit uses the point-spread function (PSF) of the detector zone where the object falls, so the flux is the total flux of the source, not the light inside an aperture, and nothing needs an aperture correction.

```
(ra_t, dec_t) = place at the exposure time        # fixed place, or J2000 place moved by its proper motion to that date
x, y          = pixel position from the exposure's own WCS (with SIP distortion); skipped within 3 px of the detector edge
stamp         = IMAGE[7 × 7 around (x, y)];   var = VARIANCE[same]
ok            = finite and not flagged PHANTOM, REFERENCE, NONFUNC, MISSING_DATA, HOT, COLD, OUTLIER, SNOWBALL or OVERFLOW
                # for a moving body OUTLIER is not bad: the pipeline sets it on asteroids
skip if a core pixel (r < 1.6 px) is OVERFLOW or has no value      → "saturated core"
skip if fewer than 20 usable pixels                                → "no usable pixels"
skip if the PSF has less than 80 % of its weight on usable pixels  → "little of the PSF on good pixels"
psf           = PSF of the nearest detector zone, placed at the sub-pixel position of the object
A, B          = argmin Σ ok · (stamp − A·psf − B)² / var           # linear least squares: flux amplitude and a constant sky
refit up to 6 times, each time dropping pixels with |stamp − A·psf − B| > 6·sqrt(var + (0.1·A·psf)²)
              # the 10 % allowance is for PSF mismatch; if a strong outlier makes every pixel look deviant, only the worst pixel goes
flux_mJy      = A · Ω_pix · 10⁹,   Ω_pix = solid angle of the pixel from the local WCS;   error = formal error of the fit
```

The wavelength of the point is the calibrated wavelength of the pixel. The PSF of a detector zone comes from the files themselves: an oversampled optical PSF per zone (a 101 × 101 cube integrated over the detector pixel by us) in the older release layout, and 441 zones (21 × 21) of 33 × 33 pixels, oversampled by 5 and already pixel-integrated, in the newer. The PSF data of one detector are byte-identical across files of one release, so we load one set per detector.

The background is a constant in the 7 × 7 window; a neighbouring star inside the window spoils that point, and the residual rejection is the only protection. Finally we look at the whole spectrum once: a point more than 5 robust sigma from the median of its 7 neighbours in wavelength (the robust sigma of those residuals over the spectrum) is marked "deviates from its neighbours" and not used.

**Result:** the isolated star 2MASS J17572542+0446559 (J 11.53) gives 357 usable points from 385 exposures, and its spectrum averaged under the 2MASS and WISE filters is within 3 % of the catalogue: J 0.99, H 1.02, Ks 1.03, W1 0.985. Source: docs/PROGRESS.md, catalogue check of the live spectrum.

**Result:** ε Indi B gives 415 usable points from 445 exposures, with ratios J 0.92, H 0.94, Ks 0.98 and W1 1.18. Source: live measurement on spherex.space, 2026-11-14, catalogue check of the spectrum.

Where the detector saturates, the measurement stops: the exposure shows OVERFLOW from about 70–90 MJy/sr in D1–D3 and about 50 in D4. A star that saturates in most exposures has no SPHEREx spectrum.

## Saturated sources

When the core is saturated in more than half of the exposures that cover the place, we say once "Saturated in most exposures: no SPHEREx spectrum" and measure nothing from the wings. Fits to the wings of saturated stars did not agree with their catalogue photometry, so we do not offer them. On such a star the chart shows the catalogue photometry (2MASS J, H, Ks and AllWISE W1, W2) as reference points instead, and the check against catalogues is skipped because no unsaturated points are left to compare. Barnard's Star, RW Aurigae, R Draconis and ε Indi A are examples among the named test stars.

## The live spectrum

A spectrum job publishes each exposure's point the moment it is fitted, so you watch the spectrum grow. Each point carries the index `i` of its exposure in the list of exposures that cover the place. The page asks every 0.35 s for the points it does not hold yet (a cursor, `since = points already held`), draws them coloured by detector, and rings the exposure under study; at the end it fetches the finished list once more, because deviating points are only marked when all points are in. A finished spectrum is kept on disk and served at once.

For every exposure we also keep the pixels of the fit: the 15 × 15 pixels of the file around the source, and three 7 × 7 stamps: pixels used (background removed), flux × PSF, and the residual, with the mask of pixels used. A play button replays the build-up one exposure per 90 ms and a slider scrubs through the exposures; the file pixels, the model and the residual of the exposure at hand are shown beside the chart. The points can be downloaded as a CSV file (exposure index, MJD, detector, wavelength, flux, error, used or not, reason).

[figure: spectrum-source | The spectrum of a star growing point by point, coloured by detector, with the pixels of the fit beside it]

## Check against 2MASS and WISE

To know that the numbers are right we compare the spectrum with catalogue photometry of the same star. We average the used points under the filter curves of 2MASS J, H, Ks and WISE W1, W2 (photon counting: the mean of the flux weighted by `T(λ)/λ`; curves from the [SVO Filter Profile Service](https://svo2.cab.inta-csic.es/theory/fps/)) and compare with `F0 · 10^(−0.4 m)`.

| Band | Zero point F0 (Jy) | Catalogue | Epoch |
|---|---|---|---|
| J | 1594 | 2MASS point-source catalogue | 1999.5 |
| H | 1024 | 2MASS | 1999.5 |
| Ks | 666.7 | 2MASS | 1999.5 |
| W1 | 309.54 | AllWISE | 2010.5 |
| W2 | 171.79 | AllWISE | 2010.5 |

We ask [IRSA](https://irsa.ipac.caltech.edu/) for the catalogue source within 3″ of the place moved to each survey's epoch; if there are several the nearest is used and the page says it is a blend. A band is compared only with photometric quality A or B, and only where the spectrum covers the filter without a hole: the used points are binned at 0.04 µm (median), at least 20 points and 4 bins are needed, and no gap between bins may exceed 0.15 µm. W2 reaches beyond SPHEREx's 5 µm: it is shown, not compared. The page shows for each band the spectrum under the filter, the catalogue flux with its magnitude, and the ratio (flagged when it is more than 25 % from 1). The comparison runs after the points and never delays them. A catalogue magnitude is from 1999 or 2010, so a variable source can differ.

## Binned spectrum and upper limits

Single points of a faint source are noisy and often below zero. We therefore also bin the used points in wavelength: 16 bins equal in ln λ between 0.75 and 5.0 µm (each about 12.6 % wide), at least 3 points in a bin.

```
flux  = inverse-variance mean of the points of the bin;   λ = their weighted mean wavelength
err   = max( 1/sqrt(Σw),  scatter of the points / sqrt(n) )     # the larger of the formal error and the scatter of the points
detected  if flux ≥ 3 · err;   otherwise the bin is drawn as a 3σ upper limit (arrow down from 3 · err)
```

Detected bins are drawn as points with error bars over the individual points, upper limits as arrows. The bin colour is the detector most of its points come from, and the line under the chart says how many exposures lie in each bin and how many bins are above 3σ.

## Portrait

The portrait is the object's own image, coloured by its spectrum: a cutout 60″ across, centred on the object, from the stacks of the pass on view. We take the colour picture of that pass with its white balance, so the portrait has the colours of the main picture; where that picture is not yet on the page the six detector pictures are added in the colour of their detector (0.75 µm blue to 5 µm red, a fixed colour for each). The brightness follows an asinh law scaled to the cutout's peak, and the three channels are scaled together so a bright red dwarf stays red instead of clipping to a white blob. A pixel saturated in most exposures is painted white, never as a hole.

## Spectrum of a moving body

An asteroid or a distant body sits on a different star field in every exposure, so a plain PSF flux would carry the stars behind it. We measure it as on minus off.

```
on   = PSF flux (the same fit as above) at the place where the body is in the exposure; OUTLIER kept
off  = the flux at the same sky place in exposures of the same detector taken when the body was elsewhere:
       the median of the nearest references in wavelength
flux = on − off;   error = on error and off error added in quadrature
```

For a candidate in a visit, `off` is the median of the nine references nearest in wavelength (none farther than 12 % in wavelength; at least 4 are needed) from exposures at least 0.2 days from the visit; the candidate counts as verified when every exposure of its visit gives a positive difference and the mean difference is at least 4σ. For a track across passes, `off` is the median of the five nearest references (at least 3) from the other passes, and the flux of the track is the inverse-variance mean of at least 6 differences, with its error scaled by the larger of 1 and the rms of the normalised residuals; the flux is also given per detector. Positions along the track come from JPL Horizons with the SPHEREx spacecraft as the observer, because the body is seen from the spacecraft, not from the geocentre.

## Quick look from the all-sky cube

While the measured spectrum is still being built the chart already shows a placeholder: the spectrum of the place in the SPHEREx DR1 all-sky spectral cube. The cube is published as HiPS tiles of order 5, each 512 × 512 pixels with 102 wavelength planes. We find the tile and pixel by HEALPix from the place (moved to 2025.9 for a moving star), read the 102 values of that one pixel by byte ranges (102 reads of 4 bytes; nothing else of the 320 MB tile is fetched) and take the wavelength of each plane from the cube's own table.

The placeholder is shown only before any measured point exists, and drawn only from planes that have a value. Its unit is mJy per cube pixel: a cube pixel is 12.9″ across (HEALPix nside 16384, solid angle 3.9 × 10⁻⁹ sr), so 1 MJy/sr is 3.9 mJy per pixel. It is the mosaic's surface brightness of the whole pixel, not a PSF flux of the source, and it never replaces the measured spectrum: it goes as soon as the first point arrives.

**Result:** reading one pixel's spectrum from the cube takes a few seconds. Source: the measuring tool's timing on the public bucket (`dr1/hips5_cube`).
