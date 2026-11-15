# The challenge

NASA Space Apps 2026 asks for a public web tool that shows images of the sky from the SPHEREx mission so that anyone can see, at once, how a patch of sky changes over time, and that may help people find moving objects. We built that tool, and this page describes everything it does: the telescope, the data, every step from a raw exposure to a moving picture, every search, and how well each one works.

## What the challenge asks

The challenge "Planet X and SPHEREx" is in the Advanced category of the 2026 International Space Apps Challenge. Its task is to create a public-facing web tool that displays SPHEREx images of the sky and makes it easy to see how they change over time. The user enters the coordinates of a patch of sky and gets a series of images of it, like a short video, in which moving objects stand out. Comets, asteroids, stars, brown dwarfs and even new planets reveal themselves by shifting position between images.

The page names five places to try: Barnard's Star, ε Indi B, WISE 0855−0714 and the variable stars RW Aurigae and R Draconis. Its optional considerations include showing the 102 spectral bands in a useful way, spectra made on the fly, choosing the field of view, subtracting images from different times, overlays from Gaia or WISE, coordinates in the address so a view can be embedded, and metadata such as contamination flags.

Source: [challenge page](https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/), last published 13 November 2026.

## Where the optional items are

- **The 102 bands:** the named bands and the walls of bands and detectors.
- **Spectra on the fly:** a click on any source measures its spectrum in every exposure that covers it.
- **Adding or subtracting images from different times:** the Difference view between passes.
- **Field of view:** sizes from 2′ to 60′ and the speed rings that say which motions each size keeps.
- **Gaia and WISE overlays:** the Gaia check of every track and the archive images (DSS2, 2MASS and AllWISE W1) on the timeline.
- **Coordinates in the address:** the address holds the whole state of a view, so a view is a link.
- **Contamination flags:** the artifact mask and the flags under the pointer.
- **New data releases:** the exposure index follows the public bucket, so a new release appears without a change of the tool.

## How we read it

We read the task as a test of one thing: a person who knows nothing about the field must be able to open a place and see an object that nobody pointed out. If the program can also find such objects, that is a bonus. Four promises follow from this and every step on this page serves one of them.

- **Anyone, quickly.** A first-time visitor sees a moving picture within seconds, without choosing settings and without reading.
- **Change is seen by eye.** The background stands still, so the only thing that changes is what really changed. This is the reason for every cleaning step: a cosmic-ray hit, a streak or a halo spike that blinks would be a false discovery for the viewer.
- **Every time scale has a moving picture.** Years, days and the faintest bodies each have their own view (see What we do and why).
- **The program helps, honestly.** A track is drawn only when a search found it, it carries its evidence, and it is called a lead, never a discovery.

## How this page is organised

The first six parts tell the story: the challenge, the telescope, how Pluto was found, how Planet Nine has been searched for, what a video made from raw data is, and what we do and why. The next parts are the method itself, with formulas, parameters and numbers: data, cleaning and stacks, views, spectra, searches for movers, bodies beyond Neptune, anomalies and chance controls, and cross-checks. The last parts give the measured results with their sources and say plainly what we claim and what we do not.
