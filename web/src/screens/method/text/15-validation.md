# Validation and dated measurements

Every claim on this site is checked against something we did not make: Gaia, JPL, 2MASS, WISE and IRSA. Here is each check with the number we obtain and where the number comes from, followed by the dated measurements of how many movers the search finds, how many exposures we expect a body to appear in, and how well the wavelength of a pixel agrees with IRSA's calibration. In one line: the motions agree with Gaia, 6 of 8 known asteroids brighter than V 19.1 are found blind with no unmatched track, and chance alone makes 0.0 tracks per field.

## The checks and what we obtain

All numbers were obtained on 2026-11-14. A "live" source is an endpoint of the running service (`/api/v1/...`); the others are the dated reports in `docs/validation/` and `docs/PROGRESS.md`.

| Check | Number obtained | Source |
|---|---|---|
| Motion of stars against Gaia DR3 | Barnard's Star 11.37 ± 0.47 ″/yr at 5′ and 10.7 ± 0.8 ″/yr at 20′ (Gaia 10.393, 2.1σ at 5′); ε Indi B 4.59 ± 0.49 ″/yr (Gaia 4.684, 0.2σ); WISE 0855−0714 7.93 ± 0.61 ″/yr (SIMBAD 8.15, −0.4σ) | live `/api/v1/movers` on 5′ fields; WISE 0855 from live `/api/v1/browndwarfs` |
| Known asteroids found blind | 6 of the 8 known bodies brighter than V 19.1 with at least 4 frames in the 60′ ecliptic film of December 2025; 0 of 6 tracks unmatched | live `/api/v1/film/48c5bbb2c5e028b847075c51/tracks` and `/bodies` (JPL Small-Body Identification and Horizons) |
| Find Planet X on known bodies | Eris: three passes linked by one orbit 95.6 ± 0.4 au, third pass confirmed; each pass alone 70–165 au (first pass). Makemake: two passes linked 52.5 ± 0.2 au, not confirmed | live `/api/v1/distant` and `/api/v1/link` runs, 2026-11-15 (keys in Bodies beyond Neptune) |
| Chance controls | 0.0 chance tracks per field between passes (20 trials, five 5′ fields and ε Indi at 10′, 20′, 60′); in the film search 0.0 at 4 or more visits (12 trials) and 1.83 per trial at 3 or more | live `/api/v1/movers` and the film's search result |
| Wavelength, index and engine | wavelength of a pixel: median difference 0.00008–0.00048 µm against IRSA's per-pixel calibration; PSF identical in 12 of 12 detector-release pairs; index of 1,628,230 files; the number of exposures that hold a body equals IRSA's Moving Object Search Tool for 4 of 4 asteroids | `docs/validation/` reports of 2026-11-14 |

## Motions, asteroids and chance

### Stars move the way Gaia says

We run the search between passes on 5′ fields and compare each track with the Gaia DR3 star that has the same motion. Gaia stars moved to each pass date lie within 3″ of the track in every pass, and the Gaia verdict is `same`.

**Result:** Barnard's Star (RA 269.44629°, Dec +4.76792°) moves at 11.37 ± 0.47 ″/yr, position angle 353.1°, against Gaia DR3 10.393 ″/yr, 355.6° (source 4472832130942575872): 2.1σ, 9 %. Source: live `/api/v1/movers`, 5′ field, 2026-11-14.

A 5′ field has a saturated core that pulls the rate up; a 20′ field gives 10.7 ± 0.8 ″/yr against Gaia 10.4 (live service, 2026-11-14).

[figure: pendulum-barnard | Barnard's Star at the three passes, its track and the Gaia positions moved to each date]

**Result:** ε Indi B (RA 331.09645°, Dec −56.80059°) moves at 4.59 ± 0.49 ″/yr, position angle 121.7°, against Gaia DR3 4.684 ″/yr, 121.8° (source 6412596012146801152): 0.2σ; other field sizes give 4.72 (20′), 4.52 (10′) and 4.68 ″/yr (60′). Source: live `/api/v1/movers`, 2026-11-14.

**Result:** WISE 0855−0714 (RA 133.75999°, Dec −7.2422°), a Y dwarf that Gaia does not have, is found in the red bands 3.8–5 µm at 7.93 ± 0.61 ″/yr, position angle 280.8°, against SIMBAD 8.15 ″/yr, 274.7°: −0.4σ in speed, 6° in angle. Source: live `/api/v1/browndwarfs`, 2026-11-14.

On the 60′ field of ε Indi the search publishes 3 tracks: one is explained by Gaia (ε Indi B) and two are unexplained leads, at 6.92 and 3.36 ″/yr. Source: live `/api/v1/movers`, 2026-11-14.

### Known asteroids are found without being told where to look

The 60′ ecliptic film of December 2025 (RA 169.8917°, Dec +4.3514°, 24 visits) is searched blind for movers; afterwards the tracks are matched with JPL's predictions (Small-Body Identification and Horizons). JPL predicts 61 bodies in the film. Eight of them have at least 4 frames inside it and V ≤ 19.1.

| Body | Result |
|---|---|
| (9952) 1991 AK | recovered, 19.3σ |
| (11020) Orwell | recovered, 11.9σ |
| (9078) 1994 PB2 | recovered, 11.6σ |
| (43959) 1997 CB26 | recovered, 12.6σ |
| (4759) Aretta | recovered, 7.1σ |
| (22522) | recovered, 5.7σ |
| (7948) Whitaker | missed (V 18.5, only 4 frames inside the film) |
| (5575) Ryanpark | missed (V 19.1) |

**Result:** 6 of the 8 known bodies brighter than V 19.1 are recovered blind and all 6 tracks are matched to a known body; none of the 17 fainter bodies is recovered, so the limit of this search is about V 19. Source: live `/api/v1/film/48c5bbb2c5e028b847075c51/tracks` and `/bodies`, product `filmsearch`, 2026-11-14, the service team.

[figure: film-ecliptic-dec2025 | the 60′ ecliptic film of December 2025 with the six recovered asteroids and the two missed ones ringed]

### What chance alone makes

**Result:** with the moving sources of every pass put at random places and the linker re-run (20 trials), chance makes 0.0 tracks per field on all five 5′ fields and on ε Indi at 10′, 20′ and 60′. Source: live `/api/v1/movers`, 2026-11-14.

**Result:** in the film search of the 60′ ecliptic field, scrambled candidate places (12 trials) make 1.83 chance tracks per trial with at least 3 visits and 0.0 with at least 4 visits, which is why a track needs at least 4 visits; the straight-line control (40 trials) makes 0.0 at 4 visits. Source: live `/api/v1/film/48c5bbb2c5e028b847075c51/tracks`, 2026-11-14.

## Find Planet X on known bodies

We search Eris and Makemake blind in one pass and link the passes; the distances are compared with the known orbits. The rows with their keys are in Bodies beyond Neptune.

**Result:** Eris, three passes linked by one orbit, 95.6 ± 0.4 au with the third pass confirmed; one pass alone gives only a wide range. Source: live runs on spherex.space, 2026-11-15 (keys 5fe287a1cf7b66ba53baadea, 4c25da646369e21eff3f3df7, cc3ed6461aa1f971864801a8).

**Result:** Makemake, two passes linked, 52.5 ± 0.2 au (not confirmed by a third pass). Source: live runs, 2026-11-15 (keys 54c02b2d2ff66213f303847d, 15340cf077cd7c68b891a29e).

[figure: slider-eris | the distance slider for Eris: one pass cannot fix the distance, the link of passes can]

## Recall of the search between passes

This measurement says how many movers the search finds. We inject point sources into the white stacks of three real 20′ fields (ε Indi B, 4 passes; Barnard's Star and WISE 0855−0714, 3 passes): four sources at a time (FWHM 8.8″, a peak equal to a chosen multiple of the stack's robust noise, straight motion at a random direction from a random place), 20 per field and cell. A source is recovered when a track passes within 3″ of its places in at least 3 passes with a speed within 25 %. Source: `docs/validation/2026-11-14-v-injection.md`, run on 2026-11-14.

Tracks shown to the visitor (a track of only three passes below S/N 15 is weak and not drawn):

| peak S/N | 5″/yr | 10″/yr | 30″/yr | 100″/yr |
|---|---|---|---|---|
| 5 | 0 % | 2 % | 0 % | 0 % |
| 8 | 28 % | 23 % | 18 % | 20 % |
| 12 | 23 % | 30 % | 23 % | 23 % |
| 20 | 38 % | 28 % | 28 % | 28 % |
| 40 | 88 % | 87 % | 87 % | 77 % |

All tracks, weak ones included:

| peak S/N | 5″/yr | 10″/yr | 30″/yr | 100″/yr |
|---|---|---|---|---|
| 5 | 0 % | 2 % | 0 % | 0 % |
| 8 | 28 % | 23 % | 18 % | 23 % |
| 12 | 28 % | 37 % | 28 % | 25 % |
| 20 | 88 % | 77 % | 82 % | 62 % |
| 40 | 88 % | 87 % | 87 % | 77 % |

**Result:** a mover as bright as the field's clear stars (peak S/N 40) is found in about 85 % of cases at any speed from 5 to 100 ″/yr. Source: `docs/validation/2026-11-14-v-injection.md`.

At S/N 20 the mover is found but often only as a weak three-pass track that the page does not draw; below S/N 12 the search misses most movers, because each pass must detect it at 8 robust σ. With the moving sources of each pass at random places the search makes 0.0 tracks per field. The few misses at S/N 40 fall on bright stars and field edges.

## Expected visits against MOST

We count the SPHEREx detector images (planes) that hold a known body, from the exposure index. JPL Horizons gives the positions of four main-belt bodies every 30 minutes (geocentric) as they cross the ecliptic field on 14–20 December 2025, and every exposure of the index whose footprint holds the body at the exposure's mid-time is counted. IRSA's Moving Object Search Tool (MOST) gives the number of planes for the same bodies and dates. MOST is the reference for this check only; we do not call it.

| Body | MOST planes | Index planes |
|---|---|---|
| (9952) 1991 AK | 119 | 119 |
| (43959) 1997 CB26 | 107 | 107 |
| (9078) 1994 PB2 | 118 | 118 |
| (11020) Orwell | 121 | 121 |

**Result:** the counts are equal plane for plane for all four bodies. Source: `docs/validation/2026-11-14-v-most.md`, 2026-11-14.

The film's known-body rows count planes the same way for the whole pass.

## Wavelength of a pixel

We compare the wavelength of every pixel as the fields use it with IRSA's per-pixel calibration. The reference is the `CWAVE` of `dr1/spectral_wcs/cal-swcs-v5-2026-191`, one wavelength per detector pixel. We compare the bicubic spline through the WCS-WAVE nodes of a DR1 science file of the same detector, on every 16th pixel.

| Detector | Science file calibration | Median \|Δλ\| (µm) | 99th percentile (µm) | Maximum (µm) | Maximum / channel width |
|---|---|---|---|---|---|
| D1 | spectral_wcs_1_v2.2.fits | 0.00010 | 0.00055 | 0.00080 | 0.03 |
| D2 | spectral_wcs_2_v2.2.fits | 0.00008 | 0.00034 | 0.00174 | 0.05 |
| D3 | spectral_wcs_3_v2.2.fits | 0.00015 | 0.00386 | 0.00733 | 0.15 |
| D4 | spectral_wcs_4_v2.2.fits | 0.00048 | 0.00308 | 0.00713 | 0.08 |
| D5 | spectral_wcs_5_v3.0.fits | 0.00008 | 0.00069 | 0.01018 | 0.28 |
| D6 | spectral_wcs_6_v3.0.fits | 0.00026 | 0.00122 | 0.00226 | 0.06 |

**Result:** the median difference is 0.00008–0.00048 µm on every detector and the largest is 0.01018 µm, 0.28 of a channel width (D5). Source: `docs/validation/2026-11-14-v1-wavelength.md`, 2026-11-14.

[figure: wall-detectors | the six detectors' pictures of one field side by side, each at its own wavelengths]

## Exposure index and field engine

These checks were made on 2026-11-14 on the real archive and real exposures.

**PSF identity.** The PSF is the same file for every exposure of a detector and release. For DR1 and QR3, comparing the first and last period of each release, the PSF data are byte-identical for all six detectors in both releases (12 of 12 pairs), and DR1 and QR3 share the PSF of every detector (6 of 6). Source: `docs/validation/2026-11-14-m1-level2.md`.

**Exposure index.** The index holds 1,628,230 files, built from IRSA's TAP and reconciled against the bucket (DR1 1,179,406 files, QR3 448,824). The real edge of an exposure bows at most 34.9″ outside IRSA's 4-corner footprint polygon (worst of 60 random files, all detectors); the index adds 60″ to every overlap test. Source: `docs/validation/2026-11-14-m2-index.md`, `docs/PROGRESS.md`.

| Field | Size | Containing the centre | Overlapping |
|---|---|---|---|
| Barnard's Star | 5′ | 358 | 391 |
| Barnard's Star | 20′ | 358 | 443 |
| Barnard's Star | 60′ | 358 | 748 |
| ε Indi B | 5′ | 461 | 493 |
| ε Indi B | 20′ | 461 | 632 |
| ε Indi B | 60′ | 461 | 876 |
| ecliptic field | 5′ | 389 | 475 |
| ecliptic field | 20′ | 389 | 539 |
| ecliptic field | 60′ | 389 | 757 |
| Eris field | 5′ | 356 | 397 |
| Eris field | 20′ | 356 | 544 |
| Eris field | 60′ | 356 | 856 |

Each query takes 39–86 ms. Source: `docs/validation/2026-11-14-m2-index.md`.

**Field engine on real exposures** (7′ fields, all exposures readable). Source: `docs/validation/2026-11-14-m3-field.md`.

| Measurement | Barnard's Star | ε Indi B |
|---|---|---|
| exposures, passes | 402 of 402, 3 passes | 496 of 496, 4 passes |
| white frame detectors | D1–D6 | D2, D3, D5, D6 |
| registration of later passes (scatter of the stars) | 0.14″ and 0.18″ | 0.18″, 0.25″, 0.26″ |
| cosmic-ray pixels per pass | 71, 87, 102 | 382, 333, 165, 544 |
| per-visit pixels per pass | 1918, 461, 1230 | 6070, 4544, 1378, 4324 |
| width of isolated unsaturated stars (FWHM) | 8.8″ (9 stars) | 8.8″ (7 stars) |
| weight lattice (largest relative rms at the detector-pixel period) | 4.6 % | 10.9 % |
| proper motion with the catalogue parallax | 10.08 ″/yr toward 355° (catalogue 10.39, 356°) | 4.58 ″/yr toward 122° (catalogue 4.68, 122°), rms 0.05″ |

**Speed.** Fields open between 2 ms and 1.7 s server side, and a cold 5′ field of Barnard's Star builds on the cloud machine from the bucket within 90 s. Source: `docs/PROGRESS.md`.
