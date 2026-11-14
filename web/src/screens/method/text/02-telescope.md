# The telescope

SPHEREx is a NASA infrared space telescope that photographs the whole sky in 102 colours between 0.75 and 5 micrometres, and does it again every six months. Each exposure records one narrow colour at every place on the frame, so a place on the sky is seen in all of its colours only after many exposures over months. This is what makes its data both rich and hard to turn into a picture.

## The mission

SPHEREx launched on a Falcon 9 from Vandenberg on 11 March 2025 (8:10 pm Pacific time) and entered a Sun-synchronous polar orbit at about 650 km altitude, circling the Earth in about 98 minutes. It makes about 14.5 orbits a day and up to about 600 exposures a day. Its photon shields keep the telescope at least 91 degrees from the Sun.

The survey runs for 25 months and maps the whole sky four times; one all-sky map takes about six months. The science goals are cosmic inflation (from the positions of galaxies), the history of star formation in galaxies, and the ices of water, CO2 and other molecules in interstellar clouds and around forming stars.

Sources: [JPL mission overview](https://www.jpl.nasa.gov/press-kits/spherex/mission-overview/), [launch coverage](https://spaceflightnow.com/2025/03/10/live-coverage-spacex-to-launch-nasas-spherex-and-punch-spacecraft-on-falcon-9-rocket-from-vandenberg/), [instrument page](https://spherex.caltech.edu/page/instrument).

## The instrument

The telescope has a 20 cm aperture. Its focal plane holds six infrared detector arrays of 2048 by 2048 pixels (H2RG), in two groups of three; a dichroic mirror sends the shorter wavelengths to one group and the longer ones to the other. We call the arrays D1 to D6, and each records one band of the spectrum. Each array looks at 3.5 by 3.5 degrees of sky and its pixels are 6.15 arcseconds across.

Over each array sits a linear variable filter: a filter whose colour changes steadily along one direction of the detector. A star therefore has a wavelength that depends on where on the array it lands in a given exposure. After an exposure the telescope steps by about 12 arcminutes, the star lands on another part of the filter and is seen in the next colour. An exposure lasts about 113 seconds.

| Detector | Wavelength (µm) | Arm |
|---|---|---|
| D1 | 0.75–1.11 | short |
| D2 | 1.11–1.64 | short |
| D3 | 1.64–2.42 | short |
| D4 | 2.42–3.82 | long |
| D5 | 3.82–4.42 | long |
| D6 | 4.42–5.00 | long |

The six detectors together make 102 spectral channels between 0.75 and 5.0 µm, at a resolving power of about 35 to 41 for D1 to D4 and about 110 to 130 for D5 and D6. Every pointing gives six images at once, one per detector, each at its own range of wavelengths, so a place on the sky is covered by all six detectors over the course of a pass.

Source: Crill et al. 2020 ([arXiv:2404.11017](https://arxiv.org/abs/2404.11017)); pixel size and exposure from the IRSA archive documentation and the pipeline paper ([arXiv:2511.15823](https://arxiv.org/abs/2511.15823)).

## Passes and visits

The telescope observes a patch for a few exposures in a row (a visit of up to four exposures, about ten minutes), then slews by about 60 degrees to another patch. A place on the sky is covered by many such visits spread over a few days, and a few days of coverage is a pass. A pass repeats about every six months because the Sun and the Earth move around the sky. Inside one pass a place is seen in all six bands; between two passes it is seen from the other side of the Earth's orbit.

This is the clock of everything we do. Stars with large proper motions shift between passes, which are months apart. Asteroids shift between visits, which are hours to days apart. Bodies in the outer solar system show a large parallax jump between passes.

## Data releases

The mission publishes its Level-2 spectral images through NASA's IRSA archive, one file per detector per exposure, and mirrors them in a public cloud bucket. Quick releases (QR) came weekly from July 2025; the first data release, DR1, appeared in November 2026 and covers surveys 1 and 2 reprocessed with one pipeline; QR3 continues weekly with surveys 3 and 4. Our exposure index lists every file of DR1 and QR3 (more than 1.6 million Level-2 files) and is updated hourly from the bucket.

Source: [IRSA data releases](https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-releases); the file counts are those of our index (see Data and reading).
