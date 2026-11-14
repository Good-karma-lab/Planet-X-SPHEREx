# From raw data to a video

A raw SPHEREx exposure cannot be blinked: each one shows a star in a different colour, at a different place on the detector, with cosmic-ray hits, saturated cores and the glow of the zodiacal dust. To make a moving picture we read the exposures that cover a place, clean them, add them into one stable picture per pass, colour that picture by wavelength, and compare passes. This part follows one field from the archive file to the picture; the Method parts that come after give every step in detail.

## What one exposure contains

The archive file of one exposure and one detector (a Level-2 file, about 70 MB) holds a 2040 by 2040 pixel image in megajanskys per steradian, a flag word for every pixel (saturation, cosmic-ray hits, hot and dead pixels, ghosts, streaks and others), the variance of every pixel, a model of the zodiacal light, the point-spread function of the detector at several places, and a table that gives the wavelength of every pixel. The wavelength changes along the detector because of the linear variable filter: one exposure is not a picture in one colour but a picture in a band of colours that changes from edge to edge.

[figure: raw-to-video | One raw Level-2 frame, then cleaned, then stacked, then coloured]

## Reading only what a field needs

A field of a few arcminutes is a small part of a 3.5 degree detector frame. We find the exposures that cover it in our index, then read only the pixel rows that the field needs from each file by byte ranges, so that opening a field never downloads whole files. The wavelength of each pixel comes from the file's own table.

## One picture per pass

A place is seen in many exposures within a pass: six detectors, several visits, a different wavelength each time. We put each exposure's pixels on a common grid finer than the detector's own pixels, remove the background of each exposure, mask flagged pixels, and add the exposures by detector and by band. Cosmic-ray hits are removed by comparing each exposure with the others of its pass, so that a hit in one exposure cannot leave a bright dot in the stack. The halos and spikes of bright stars are masked by a contrast rule, and saturated cores are left empty and shown as white, not invented.

The result is one stack per pass and detector and one per named band. Passes are registered to one another on the field's stars, so a star that does not move stays at the same pixel in every pass.

## Colour

A star's colour in the picture is the colour it has in the infrared. Each exposure carries its wavelength at the star, and the spectral colour image assigns each wavelength a hue, so a flat spectrum is white, a red source is red and a cold brown dwarf, bright only beyond 3.8 micrometres, is orange against white stars. The named bands (Star, water, methane, ice, dust, CO2, CO) show the stack in the window of one molecular feature.

## The motion layer

For the film of the visits of a pass we make one picture per visit and subtract the static sky, the median of the same detector's frames, then divide by the scatter. What remains is the change: a moving body shows as a point in a different place in each frame, and the stars do not appear at all. The Difference view does the same between passes and shows the change as a pair of colours.

[figure: film-ecliptic-dec2025 | The ecliptic field of December 2025 as a film of visits, asteroid tracks numbered]

## What a video is

A video of a field is therefore a sequence of cleaned, registered stacks or visit frames, drawn with one display law so that a faint change is not hidden by a brightness jump between frames. The Months scale plays the passes, the Days scale plays the visits of one pass, Blink flips between frames, and Difference shows only what changed.
