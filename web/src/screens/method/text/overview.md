# What this page covers

SPHEREx Sky reads real SPHEREx Level-2 exposures from the public archive, builds a picture of any place on the sky from them, and searches those pictures. This page describes every step as the live service does it today, with the parameters and the numbers it measures.

The order of the steps:

- **Data:** the exposures (the first-year data release DR1, then quick release QR3), the index that finds them, the flags, the wavelength of every pixel and the PSF.
- **Cleaning and stacks:** how the exposures of a pass are cleaned and added into one picture per detector and per band, and how the passes are registered to each other.
- **Views:** the single picture, the colour blend and the named bands, the walls of bands and detectors, the Difference between passes, the Months and Days scales, Sharpen, and the archive images.
- **Spectra:** the spectrum of any source in every exposure that covers it, its check against 2MASS and WISE, and the quick look from the all-sky cube.
- **Searches:** movers between passes (checked against Gaia, with the chance level), movers inside a pass (the film), pairs that change in place, red sources, known bodies against the Moving Object Search Tool, and Find Planet X for bodies beyond Neptune.
- **Anomalies, checks and chance:** the spectral anomaly map and how every search says how often chance alone would make its result.
- **Validation:** every check with the number the service obtains, and the dated measurements of recall and of the known-body counts.

A track no catalogue explains is a lead, not a discovery, everywhere on the site. The depth of every search is stated beside it; the search for bodies beyond Neptune reaches about V 20–21 in reflected light and makes no claim of a Planet X.
