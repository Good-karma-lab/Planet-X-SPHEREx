# Views: how we show change

We show every field as a blink comparator: the same sky, on the same grid, drawn with one fixed brightness law, so the static background stays put and only what changed between two looks can catch the eye. Every view is computed in your browser by WebGL2 fragment shaders from stacks already on the page (half-precision textures), so switching view, band, pass or display option asks for nothing new. Only the films of the visits and the archive images are products of their own.

## Display law

A blink comparator only works if the brightness scale does not change between the two plates, so we use one law for all passes of a field. Brightness alone must never blink.

```
s = asinh((x − lo) / soft) / asinh((hi − lo) / soft)      x in MJy/sr, s clipped to 0…1
lo = −0.05,  hi = 400,  soft = 0.4                          the defaults; sampling is bilinear
```

- A pixel saturated in more than half of the exposures that make it is drawn white, never as a hole (switch: White cores).
- A pixel with no data inside the field is a flat blue-grey; outside the coverage of a wall panel or of a film frame it is black.
- The Stretch slider changes `soft` (0.05 to 4.5): left shows faint light, right keeps bright stars apart. Brighten lowers `hi` to 40. Trim sets `hi` to the field's own 99.9th or 99.5th percentile of white light (adaptive: 1.5 times the 99.9th), always between 0.8 and 400.
- Whatever the setting, it is the same for every pass and every panel on screen.

## Spectral colour

Each SPHEREx exposure sees one wavelength, so a colour picture has to be built from the six detectors. We give every wavelength its rainbow colour (0.75 µm violet to 5 µm red) and mix the six detector stacks into red, green and blue with a fixed 3 × 6 matrix. A flat spectrum comes out grey, a red dwarf orange-red.

```
lam_rgb(λ):  f_log = (ln λ − ln 0.74) / (ln 5.02 − ln 0.74);  f_lin = (λ − 0.74) / (5.02 − 0.74)
             h = 0.74 − 0.78 · (0.35 · f_log + 0.65 · f_lin);   rgb = hsv(h mod 1, 0.88, 1.0)
R, G, B  = M (3 × 6) · detector stacks     # column d of M = mean lam_rgb over detector d's range; each row sums to 1
```

The detector ranges are D1 0.75–1.11, D2 1.11–1.64, D3 1.64–2.42, D4 2.42–3.82, D5 3.82–4.42 and D6 4.42–5.00 µm. Only the detectors that cover the field in every pass take part (the white-frame detectors), so the colours of the passes stay comparable.

### White balance of each pass

An ordinary star should be white in every pass. We therefore balance each pass on its own field stars: on the peaks of the 30 brightest unsaturated stars (a local maximum over 7 × 7 pixels, more than 10 robust sigma above the background, not within 4 pixels of a saturated or empty pixel), after subtracting the background of each channel,

```
kR = median(G / R),   kB = median(G / B)
```

With fewer than 6 usable stars the pass keeps the balance computed when the field was built, from every unsaturated pixel brighter than 2.5 MJy/sr (the median of G/R and G/B there).

### Colour boost

Colour is saturated by one fixed factor for the whole field, so the same star has the same colour in every pass.

```
s = (law(R · kR), law(G), law(B · kB));   l = mean(s)
colour = mix(l, s, 1 + (2.4 − 1) · fade · faint)
fade  = 1 − smoothstep(0.55, 0.90, l)     # no boost on very bright pixels: the wings of a saturated core
faint = smoothstep(0.04, 0.16, l)         # none at the noise level either: noise keeps its weak natural colour
```

A faint reddish ring can appear around very bright stars, because stars are wider at long wavelengths.

## Named bands

A band is a set of wavelength windows in which a broad spectral feature sits. Half-open windows, lower edge included: an exposure pixel contributes to a band only if its own wavelength lies inside a window. These are our working definitions of broad tracers, not line fluxes. One file (`data/bands.json`) is read by the pictures, the page and this text.

| Band | Windows (µm) | Hue |
|---|---|---|
| Starlight | 0.75–1.10 | 48 |
| Water vapor (H₂O) | 1.30–1.50 · 1.80–2.00 · 2.55–2.90 | 205 |
| Methane (CH₄) | 1.62–1.76 · 3.20–3.50 | 24 |
| Water ice | 2.90–3.20 | 185 |
| Dust (PAH) | 3.20–3.40 | 140 |
| Carbon dioxide (CO₂) | 4.20–4.35 | 275 |
| Carbon monoxide (CO) | 4.55–4.85 | 345 |

With all seven bands on, the picture is the spectral colour picture. With some bands on, each chosen band's stack is drawn in its hue and the bands are blended by screen blending (`1 − Π(1 − l · hue)`); Mono sum adds them into one grey image instead. One band alone shows its stack in its hue. A band picture is continuum plus feature: we never claim a molecule from a band picture.

## Pendulum and walls

### The pendulum of passes

The main view is a blink of the pass stacks. The picture plays the passes in the order 1, 2, …, N, …, 2, then again, so there is no return jump that the eye would read as a separate motion. Each pass is held for 430 ms (530 ms a step at 1×); with Interpolate switched on, the last 100 ms is a crossfade with `t²(3 − 2t)`. Speeds 0.25×, 0.5×, 1×, 2× and 4× scale the clock; Space plays and pauses, the arrow keys step by hand, `[` and `]` change the speed. Because the grid, the display law and the balance are the same in every pass, only a source that moved or varied blinks. In fields wider than 2.5′ a 2′ zoom of the selected object (or of the named test star in the field) sits in the corner, drawn from the same frames.

[figure: pendulum-barnard | Barnard's Star in the pendulum: the background stays still and only the star hops between passes]

### Co-add

Co-add draws all passes as one still picture, the mean of the passes' display colours. Faint sources that do not move stand out; movers smear into a trail.

### Wall of bands

The wall of bands has one panel for each chosen named band, under one blink timer and one display law: what an object is made of, and in which light it is bright. Outside a band's coverage a panel is black.

### Wall of detectors

The wall of detectors has six panels, labelled in words and microns ("very near infrared 0.75–1.11 µm" through "mid infrared 4.42–5.00 µm"), laid out 3 × 2, under one blink timer. A real object jumps in all six at once by the same amount; a cosmic ray, a glint or a detector artifact lives in one. We read the wall by eye, panel against panel.

[figure: wall-detectors | The six detectors side by side: a mover jumps the same way in all of them]

## Difference between passes

Difference is the blink comparator done by arithmetic. For every pixel we ask how significant the change is between a pass b and the first pass a, and we draw only what is both significant and large against the light that is there.

```
D = b − a;   S = smooth part of D (median of valid pixels in 24-px windows on an 8-px grid, bilinear)
z = (D − S) / sqrt( (k·σ)² + (0.05 · max(|a|, |b|))² ),     σ = sqrt(σa² + σb²) from the stacks' weights
k = robust width of (D − S)/σ in the same windows on source-free pixels
    (source = more than 2 robust widths above the 15 × 15 median, dilated by 2 detector pixels)
```

The 5 % floor is not scaled by `k`: it is about flux (point-spread function and calibration differences between passes), not noise. `S` removes the change of the diffuse sky (zodiacal light) and real extended change larger than about 2.5′. We compute `z` for the white frame and for each of the seven bands, and store it as one byte per pixel (128 + 12.7 z, clipped at ±10).

### What is drawn

The picture shows the pass dimmed and grey underneath. A pixel is coloured when `|z| > 4`: magenta for brighter, cyan for fainter, full strength at `|z| = 10`. The first pass is the reference and shows nothing. A second gate removes the small residual rings of static stars (registration, a slightly different point-spread function): the change must be a real share of the brightest light within one and a half detector pixels, in either pass: below 30 % of that peak it is not shown, from 50 % it is shown in full. A moving source changes by about its whole peak; a static star whose profile differs between passes changes by a small part of it. In the picture view the white-frame map is drawn; on the wall of bands each panel uses its own band's map.

A star with a different colour is not by itself a variable, because passes sample different wavelengths; we read a Difference as a place to look, not as a verdict.

## Artifact mask and speed rings

### Artifact mask

The Artifacts switch paints red where the instrument pipeline flagged a pixel, so a streak or a persistence ghost is recognised before it is mistaken for motion. While a field is built we count, per pixel, the share of the exposures (inside their footprints) carrying each flag; flags that reach 2 % of the exposures somewhere in the field are kept. The red opacity is the largest share among the kept flags, zero up to a share of 25 % and full at 75 %. SOURCE (a real source) and OVERFLOW (saturation, drawn white) never redden. The mask is drawn on the picture and in Days, for the pass or visit shown.

### Speed rings

Speed rings answer "which field size do I need": a small field shows what is slow, a large one catches what is fast. Rings of 1, 3, 10, 30, 100 and 300 ″/yr have the radius

```
r = v · Δt        Δt = first to last pass shown
```

They are centred on the selected object, else on the named test star, else on the middle of the picture, and drawn when 14 px < r < 0.72 of the panel width. In Days the rings are 0.3, 1, 3, 10, 30 and 100 ″/h, with Δt from the first to the last visit.

## Sharpen

Sharpen makes faint stars crisper on screen. It is a boost on real pixels, never new data, and one gain multiplies every layer and channel before the display law, so a colour cannot change. It is the same code path in every view, including Days, and it is on by default (a switch).

```
L      = white light of the pass at the pixel
t      = (L − ring mean) / L      ring: 6 taps at half and 6 at one detector pixel, plus the pixel itself (weight 0.2)
gain   = 1 + w_snr · w_sat · w_bright · A · t² / (t + 0.1),     A = 16
w_snr    = smoothstep(3, 8, (L − background) / noise of the pass)
w_sat    = 1 − smoothstep(0.2, 0.6, saturated share on the taps)      # nothing within about one detector pixel of a saturated pixel
w_bright = 1 − smoothstep(0.12, 0.40, law(L))                          # fades out as the stretched value rises
```

## Film of a pass: days

A body that crosses the field in days is in no pass stack: at each place it is in one visit of a few exposures out of many. So the Days scale shows the visits of one pass in time order, drawn by the same renderer as Months (same law, Sharpen, Brighten and Stretch). A visit is a run of exposures of one detector with gaps under 30 minutes; each frame is tinted by the hue of its detector, and a core saturated in a visit is drawn white. Days plays one visit per 700 ms.

[figure: film-ecliptic-dec2025 | The visits of one pass over the ecliptic field: asteroids hop from visit to visit]

### How a frame is made

```
grid:    step = max(3.075″, field / 600), at most 600 px across
input:   every exposure of D1–D3 of the pass, with the pipeline's OUTLIER flag kept (it marks asteroids)
         each exposure is put on the grid and smoothed to a 3.2″ point source
frame:   median of the visit's exposures (a cosmic-ray hit is in one exposure and drops out; a body is in all)
keep:    visits with at least 3 exposures when most visits have them; drop visits covering less than 50 % of the field
align:   frames of one detector are shifted onto their median by cross-correlation (parabola at the peak, at most 2 grid points)
static:  median of the frames of the detector, where at least 3 frames and 30 % of them cover the place
motion:  (frame − static) / robust scatter of the unmasked pixels of that frame, edges trimmed by 2 px
mask:    static > 5 robust sigma above its median, grown by 1 grid point;
         halo of a saturated star (static blob of equivalent radius ≥ 150″) grown by 0.4 × its radius
```

### Motion only

The static background is what a blink comparator keeps still, so the film can drop it altogether. Motion only (Days scale) draws the motion map instead of the sky: dark, with amber only where the change connects, above 3σ, to a place a search found on that visit or where JPL predicts a known body that the visit shows; the amber is full from 8σ. Elsewhere the map stays dark, because the raw map's specks and a bright star's halo are not motion. Known bodies are ringed where JPL predicts them and solid when the motion map shows them there.

## Archive images

Archive images put decades between the looks. They show the same field in older all-sky surveys, on the same grid orientation (north up, east left), blinked before the SPHEREx passes and labelled with survey, band and years, never presented as SPHEREx.

| Survey | Band | Years |
|---|---|---|
| DSS2 | red | 1985–1999 |
| 2MASS | Ks | 1997–2001 |
| AllWISE | W1 | 2010 |

We cut each picture from the survey's HiPS tiles at your field's centre and size (gnomonic projection, bilinear sampling, the tile order that matches the scale), stretched linearly from the 2nd to the 99.7th percentile. Switch Archive images on in Months and the loop plays the three archive pictures, then the passes, at 1.1 s a frame at 1×. The marker, the trajectory (dots with years) and the ring of a named object with a proper motion are placed where it was on each date: its catalogue place moved by its proper motion to the year. When the object lies outside the frame on that date, the status line says how far and in which direction it was from its 2025 place. The Reference switch shows the DSS2 red picture in a corner of the field instead.

[figure: blink-pluto | The blink comparator in its classical form: the same sky on two dates, only the mover changes]
