# 2. Methods

Every algorithm the system uses, with its parameters, the numbers that validate it and its known limits. The algorithms were worked
out and measured in a research phase on real SPHEREx Level-2 exposures (November 2026); the numbers quoted as "research" are the
acceptance targets of the production build. Where the production design differs from what was measured, the difference is stated.

| File | Contents |
|---|---|
| [2.1 Data and reading](2.1-data-and-reading.md) | Level-2 files, HTTP range reads, flags, wavelength of a pixel, PSF, the exposure index, visits and passes |
| [2.2 Cleaning and stacks](2.2-cleaning-and-stacks.md) | background, masks, super-resolution stacks, cosmic rays, streaks, halos, saturation, registration, white frame, quick frame, wide fields, streaming build |
| [2.3 Views](2.3-views.md) | pendulum, spectral colour, named bands, walls, Difference, artifact mask, speed rings, display law and sharpening, films, archive images |
| [2.4 Spectra](2.4-spectra.md) | PSF photometry, the live spectrum, catalogue check, binned spectrum and upper limits, change test, portrait, on − off, thermal signature |
| [2.5 Searches](2.5-searches.md) | sources of a field, between passes, pairs changing in place, inside a pass (film search), inside a visit, bodies beyond Neptune, deep sum, link and jump, WISE archive check |
| [2.6 Anomalies, identification and chance](2.6-anomalies-checks-chance.md) | anomaly map, catalogue identification and context, chance controls of every search |
| [2.7 Validation targets](2.7-validation.md) | one table of every number the running system must reproduce |

## Conventions

- **Units.** Pixel values in MJy/sr (Level-2 IMAGE); fluxes in mJy; wavelengths in µm; angles in arcseconds (″) or arcminutes (′);
  rates in ″/yr (between passes), ″/h (inside a pass or visit); times in MJD (UTC) and decimal years.
- **Detector pixel** = 6.15″. **Grid** = the north-up tangent-plane grid of a field (step 1.5375″ up to 20′, 3.075″ at 60′).
- **σ** of a quantity is its robust width (1.4826 × MAD) unless said otherwise.
- **Exposure** = one detector's Level-2 file (the mission's "spectral image"). SPHEREx exposes six detectors at once in three pairs
  that see the same sky at the same time (D1/D4, D2/D5, D3/D6), so a count of exposures is a count of detector images.
- **Visit** = consecutive exposures of one detector less than 0.5 h apart. **Pass** = a cluster of exposures separated from the next
  by more than 60 days, cut at 90 days (our grouping, not the mission's survey numbering).
- **Lead** = a find of a search with its evidence. Never "discovery".
- Every algorithm lives in the `engine` package as pure functions on arrays (no HTTP, no files); the service only reads, caches and
  schedules. Parameters are named constants in one module per algorithm and are listed on the public Method page, which is
  generated from the same constants (section 5.9 of the architecture), so the page cannot drift from the code.
