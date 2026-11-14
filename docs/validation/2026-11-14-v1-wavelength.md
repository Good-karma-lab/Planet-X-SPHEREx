# V1 — wavelength of a pixel against the per-pixel calibration (2026-11-14)

Reference: CWAVE of `dr1/spectral_wcs/cal-swcs-v5-2026-191` (one wavelength per detector pixel). Compared: the bicubic spline
through the WCS-WAVE nodes of a DR1 science file of the same detector (what the fields use), on every 16th pixel.

| detector | science file calibration | median |Δλ| (µm) | 99th percentile (µm) | max (µm) | max / channel width |
|---|---|---|---|---|---|
| D1 | spectral_wcs_1_v2.2.fits | 0.00010 | 0.00055 | 0.00080 | 0.03 |
| D2 | spectral_wcs_2_v2.2.fits | 0.00008 | 0.00034 | 0.00174 | 0.05 |
| D3 | spectral_wcs_3_v2.2.fits | 0.00015 | 0.00386 | 0.00733 | 0.15 |
| D4 | spectral_wcs_4_v2.2.fits | 0.00048 | 0.00308 | 0.00713 | 0.08 |
| D5 | spectral_wcs_5_v3.0.fits | 0.00008 | 0.00069 | 0.01018 | 0.28 |
| D6 | spectral_wcs_6_v3.0.fits | 0.00026 | 0.00122 | 0.00226 | 0.06 |
