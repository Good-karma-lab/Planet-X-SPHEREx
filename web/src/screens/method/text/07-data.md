# Data and reading

We work from the public SPHEREx Level-2 exposures of the NASA/IPAC archive. We keep our own index of every exposure, read from each file only the rows a field needs, and carry along the full flag word, the sky position and the wavelength of every pixel. Result: 1,628,230 exposures indexed (DR1 and QR3), and a cold 7′ field is read completely, without a missing file, in 2–16 s of reading.

## Level-2 exposures

SPHEREx has six H2RG detectors behind linear variable filters. Each pixel sees one wavelength, and the wavelength changes slowly across the detector; 102 spectral channels together cover 0.75–5.0 µm. Every exposure is one detector image of 2040 × 2040 pixels of 6.15″, and each pointing gives six of them, one per detector, each at its own range of wavelengths.

| Detector | Wavelength (µm) | R = λ/Δλ (nominal) | Arm |
|---|---|---|---|
| D1 | 0.75–1.11 | 41 | short |
| D2 | 1.11–1.64 | 41 | short |
| D3 | 1.64–2.42 | 41 | short |
| D4 | 2.42–3.82 | 35 | long |
| D5 | 3.82–4.42 | 110 | long |
| D6 | 4.42–5.00 | 130 | long |

Source: detector wavelength ranges from the [SPHEREx instrument page](https://spherex.caltech.edu/page/instrument) and the flag and band tables of the files we read.

Every file is a FITS file with several extensions. The files we read (DR1 and QR3 use the same layout, pipeline version v27) hold these extensions, and we always find their byte positions by walking the chain of headers, because header lengths differ from file to file:

| Extension | Content | How it is stored |
|---|---|---|
| PRIMARY | no data | header only |
| IMAGE | float32, MJy/sr | plain image; the data start at byte 28,800 in the files we checked |
| FLAGS | int32 flag word per pixel | tile-compressed (RICE_1, block size 32, one tile per row), about 7 MB |
| VARIANCE | float32 | plain image, right after the compressed FLAGS table (its rows plus its heap, padded to 2,880 bytes) |
| ZODI | float32 | plain image |
| EPSF | the PSF of the detector | binary table of zones, 3.9 MB |
| WCS-WAVE | wavelength lookup table | small binary table at the end |

Three float32 planes of 2040 × 2040 are 16.6 MB each. Together with FLAGS and the PSF a file is about 70 MB.

Header keywords we use: `MJD-AVG` (time of the exposure), `DETECTOR`, the celestial WCS (tangent projection with SIP distortion; its alternate descriptions are dropped before parsing), the wavelength WCS, `X_SC`, `Y_SC`, `Z_SC` (spacecraft position, km, geocentric), the flag-bit table (`MSKN` and `MSKM` cards) and, for the PSF, the zone-centre columns `XCENTER` and `YCENTER`.

Source: layout walked on real files, docs/PROGRESS.md (milestone on Level-2 access) and docs/validation/2026-11-14-m1-level2.md.

## Releases and counts

We read the first-year data release DR1 (`dr1/level2/` in the public bucket `nasa-irsa-spherex` of AWS us-east-1, periods 2025W17_4B to 2026W20_1A, the whole first survey year reprocessed with one pipeline) and the quick release QR3 for the periods after it. IRSA advises exactly these two for new work. DR1 and QR3 follow each other in time without overlap or gap, so every exposure comes from one release only and the exposures of one field never mix pipeline versions within the first year. Files of the earlier QR2 release have another layout and a different PSF format; we do not read them.

| Release | Files in the bucket | Of which deep-field files | Time span (MJD) |
|---|---|---|---|
| DR1 | 1,179,406 | 233,681 | 60789.75–61174.49 |
| QR3 | 448,824 | 86,700 | 61174.51–61318.0 |
| Both | 1,628,230 | 320,381 | |

**Result:** the bucket holds 1,179,406 DR1 and 448,824 QR3 files, and our index holds all 1,628,230 of them (Source: docs/PROGRESS.md, milestone on the exposure index).

The bucket also holds an all-sky HiPS spectral cube of DR1 (`dr1/hips5_cube/`, order 5, about 321 MB per tile) and the mission's per-pixel wavelength calibration (`dr1/spectral_wcs/`). The cube is the SPHEREx layer of the globe and the source of the quick-look spectrum; the calibration is used for the wavelength of every pixel (see Wavelength of a pixel).

**A cold field's first frame.** While a field is built from its exposures (2 to 60 seconds when nothing of it is ready), `GET /api/v1/fields/quick` gives a first frame from the same cube within a second or two: three of its planes (4.5, 2.8 and 1.3 µm as red, green and blue), resampled by nearest pixel onto the field's own grid (north up, east left, the same n × n pixels as the field that follows) at 6.45″, half a cube pixel. Only the rows of the cube tile the field spans are read, by byte range (about 0.4 MB for a 10′ field, 4.5 MB for 60′). Each plane is stretched by asinh between the sky's median and its 99.7th percentile; where the cube has no value (a saturated core, a gap) the frame is transparent. It is the survey's mosaic, 12.9″ pixels and every visit added, so it shows the place, never motion, and it is replaced by the field as soon as the first pass is in.

## Reading only what a field needs

A field of 2′ to 60′ is a small part of a 2040 × 2040 detector image, and S3 serves one byte range per request. The unit of reading is therefore a band of whole image rows, and we never download a whole file.

### Finding the rows

- The first time we meet a file, we ask its size and read its headers in steps of 32 kB until each header's END card is found. The size of each data unit gives the start of the next header. A walk takes 7 sequential range reads (Source: docs/PROGRESS.md, milestone on Level-2 access). The offsets and header bytes are cached on disk, so a file is walked once.
- For a field we project the outline of the field (nine points along each edge) into the detector pixels of the exposure. The rows and columns that cover it, plus a margin of 3 pixels, form the window. Only the rows of the window are read, for IMAGE and FLAGS. The pixel values of the picture come from IMAGE and FLAGS alone; VARIANCE is read only for spectra.
- Rows are fetched and stored as bands of 64 rows per file and plane (compressed `npz` on disk). A band is 64 × 2040 × 4 bytes, 0.52 MB, per uncompressed plane. Neighbouring fields and later products find the bands already on disk, and neighbouring missing bands are fetched in one request.
- The compressed FLAGS are decoded tile by tile: we read the descriptors of the tiles that cover the rows, then exactly the heap bytes of those tiles.

### Reading reliably

- Every request has one retry with a pause of 0.3–0.5 s. A read that stalls for 40 s is dropped. At most 64 reads are in flight at once, and while one detector of a pass is being stacked the exposures of the next detector are already being read.
- An exposure that cannot be read is counted and left out; the field states how many exposures could not be read. A circuit breaker for the archive host opens after 12 failures within a minute or 8 in a row, refuses requests for 30 s, and doubles that pause after each failed probe.

**Result:** the field of Barnard's Star (7′) read 402 of 402 exposures and the field of ε Indi B (7′) 496 of 496, with none unreadable; a cold build spent 2 s and 16 s on reading respectively (Source: docs/validation/2026-11-14-m3-field.md).

### What one exposure gives

For one exposure inside a field we obtain the native pixel values, the full flag word (every bit of the int32), the sky position of every native pixel (through the SIP WCS), the wavelength of every pixel, the time, the detector, the spacecraft position and the file identity (release and pipeline folder). Nothing is resampled at this stage, because resampling before stacking blurs.

## Flags

Every pixel has a flag word. Bit numbers differ between releases, so we read the bit table from each file's own header (the `MSKN` and `MSKM` cards); a built-in table of 24 flag names is used only when a file has none, and a disagreement with it is reported, with the file's table winning. Masks are always defined by flag name, never by bit number.

We act on the flags in four ways:

| Treatment | Flags (bit) | Why |
|---|---|---|
| Masked in every stack of the fixed sky | PHANTOM (4), REFERENCE (5), NONFUNC (6), MISSING_DATA (9), HOT (10), COLD (11), OUTLIER (19), SNOWBALL (27) | dead, lost and outlier pixels |
| Masked only farther than 6 pixels from a bright core | TRANSIENT (0), OVERFLOW (1), SUR_ERROR (2), PERSIST (17) | around a bright star these are set almost always; masking them everywhere leaves a hole where the star is |
| Saturated | OVERFLOW (1), and any pixel with no value that no bad-pixel flag explains | the pixel goes into no stack and is filled with nothing; it feeds the saturated share, which is drawn white |
| Kept and counted | SOURCE, HALO, CROSSTALK, STREAK, GHOST, GHOST_FPA, GHOST_EXT, DICHROIC, PHANMISS, NONLINEAR, BLOOM | the pipeline's own source mask is not an error; the rest are counted in the flag census |

FULLSAMPLE belongs to no group and is ignored.

- OUTLIER is set by the SPHEREx pipeline on asteroid pixels. We mask it for the fixed sky and keep it for every search for moving objects and for the photometry of a moving body.
- The flag census gives, per flag name, the share of exposures (inside their footprints) that carry the flag at each grid pixel. Names set in at least 2 % of the exposures somewhere in the field are published, in four display groups: saturation (OVERFLOW, NONLINEAR, BLOOM), source (SOURCE), transient (TRANSIENT, PERSIST, OUTLIER, SNOWBALL, HALO, CROSSTALK, STREAK, GHOST, GHOST_FPA, GHOST_EXT) and bad pixel (SUR_ERROR, PHANTOM, REFERENCE, NONFUNC, DICHROIC, MISSING_DATA, HOT, COLD, PHANMISS).

Source: the flag tables of the Level-2 files we read (docs/PROGRESS.md, milestone on Level-2 access).

## Wavelength of a pixel

Each exposure measures one wavelength per sky position, and the wavelength is what lets us build the named bands and the wavelength-dependent background. IRSA documents the WCS-WAVE table of a file as an approximation for visualisation and the calibration files `spectral_wcs` (CWAVE and CBAND: one wavelength and one bandwidth per detector pixel) as the reference.

We therefore read CWAVE per pixel from `dr1/spectral_wcs/cal-swcs-v5-2026-191`: two 16 MB planes per detector, read once and kept on disk and in memory. Only when a map cannot be read do we fall back to a bicubic spline through the nodes of the file's own WCS-WAVE table (9 × 9 nodes in D1, 11 × 11 in D5, 13 × 20 in D4, about 255 pixels apart). A bilinear lookup between nodes would miss by up to 0.02 µm, because the filter's wavelength is curved between nodes. In D5 and D6 a table on the older v2.2 calibration, recognised by its first node, is lifted by 0.0064 µm, which is exactly how much higher the v3.0 tables are.

| Detector | Median abs. difference spline − CWAVE (µm) | 99th percentile (µm) | Maximum (µm) | Maximum / channel width |
|---|---|---|---|---|
| D1 | 0.00010 | 0.00055 | 0.00080 | 0.03 |
| D2 | 0.00008 | 0.00034 | 0.00174 | 0.05 |
| D3 | 0.00015 | 0.00386 | 0.00733 | 0.15 |
| D4 | 0.00048 | 0.00308 | 0.00713 | 0.08 |
| D5 | 0.00008 | 0.00069 | 0.01018 | 0.28 |
| D6 | 0.00026 | 0.00122 | 0.00226 | 0.06 |

**Result:** the fallback spline agrees with CWAVE to a median of 0.0001–0.0005 µm, and its worst pixel is 0.010 µm off (D5, 0.28 of a channel), which is why the fields read CWAVE (Source: docs/validation/2026-11-14-v1-wavelength.md, every 16th pixel of a DR1 file per detector).

## PSF

The PSF is needed to fit fluxes of point sources. In DR1 and QR3 files it is the EPSF table: zones of 33 × 33 samples at five times the detector sampling, already integrated over the detector pixel. A source is fitted with the zone whose centre (columns `XCENTER` and `YCENTER`) is nearest to its detector position. Detectors D1, D2, D4, D5 and D6 have 441 zones (21 × 21) and D3 has 451 (11 × 41); the selection works for any number of zones.

- The PSF data of two files of one detector and release are byte-identical, even months apart and reduced by different pipeline versions. We therefore read a PSF once per detector, file layout and PSF data size, keep it in memory and on disk, and share it among all files of that family.
- IRSA documents an error in the PSF cubes of the first two quick releases (planes stored in another order than the header says, up to ±5 % in photometry; see the [IRSA note](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/psfhdrerr.html)). We avoid it by reading DR1 and QR3 only.
- The tables are cut close to the star, so we never fit a flux to the wings of a saturated star.

**Result:** the PSF data were byte-identical in 18 of 18 checks (D1–D6 in QR2, QR3 and DR1, first against last period of each), and QR3 and DR1 share the same EPSF for every detector (Source: docs/validation/2026-11-14-m1-level2.md).

## The exposure index

We keep our own index of the archive. We need it to find, in milliseconds, every exposure whose footprint overlaps a field, and it must never depend on a catalogue that can be incomplete: on 2026-11-12 IRSA's ObsCore listed 192,961 QR3 files where the bucket already held 361,305.

- **What it holds per file:** S3 key, release, period, pipeline folder, sequence and group numbers, detector, start and end time (MJD), size, MD5, the centre and radius of the footprint and the footprint polygon.
- **How it is built:** one query per day of the survey to the IRSA TAP service (the tables `spherex.artifact` and `spherex.plane` give path, size, checksum, detector, time span and the four-corner sky polygon of every file). Files that IRSA does not list are found by listing the bucket by period: for each of them we read only the first 64 kB of the file, compute a 12-vertex footprint (corners and two points on each edge through the SIP WCS), and add the file marked as read from headers. Rows are stored in SQLite with an R*-tree over the bounding boxes of the footprints.
- **How it is queried:** the field's circumscribed circle (radius half the diagonal of the square field) selects candidates through the R*-tree, and an exact polygon-and-circle test on the sphere keeps those whose footprint overlaps the circle. We keep exposures that overlap the field, not only those that contain its centre; RA 0/360 and the poles are handled.
- **The margin:** the footprint polygon has four corners, but the detector edge bows outward by the SIP distortion. Every overlap test adds a margin of 60″.
- **How it stays fresh:** every day at 06:00 UTC a job re-reads the last five days from IRSA (a day IRSA does not answer is left for the next run), indexes new files, and reconciles the number of files per period with a listing of the bucket. A field's address contains a digest of the keys of its overlapping exposures, so a new period changes the address only of the fields it touches.

**Result:** the real detector edge lies at most 34.9″ outside IRSA's four-corner polygon (60 random files, all detectors; D1 34.6″ to D6 34.9″), so the 60″ margin is enough (Source: docs/validation/2026-11-14-m2-index.md).

**Result:** the counts per period in the index equal the bucket listing in every period, 1,179,406 DR1 and 448,824 QR3 files (Source: docs/PROGRESS.md, milestone on the exposure index).

Exposures that overlap the test fields, from the index of 2026-11-14:

| Field | Size | Containing the centre | Overlapping | DR1 + QR3 | Query (ms) |
|---|---|---|---|---|---|
| Barnard's Star | 5′ | 358 | 391 | 261 + 130 | 48 |
| Barnard's Star | 60′ | 358 | 748 | 517 + 231 | 85 |
| ε Indi B | 5′ | 461 | 493 | 373 + 120 | 49 |
| ε Indi B | 60′ | 461 | 876 | 679 + 197 | 86 |
| Ecliptic field | 5′ | 389 | 475 | 387 + 88 | 39 |
| Ecliptic field | 60′ | 389 | 757 | 614 + 143 | 66 |
| Eris field | 5′ | 356 | 397 | 264 + 133 | 48 |
| Eris field | 60′ | 356 | 856 | 548 + 308 | 71 |

**Result:** a query takes 39–86 ms for every test field at every size from 5′ to 60′ (Source: docs/validation/2026-11-14-m2-index.md).

## Visits and passes

A product that compares the sky at different times needs a clear rule for what belongs together. We group exposures in two ways, both from the exposure times.

- **Visit:** consecutive exposures of one detector with gaps under 0.5 h. Near the ecliptic a visit is a group of about four exposures two minutes apart; far from it, singles or groups of two to four.
- **Pass:** exposures separated by more than 60 days start a new pass, and a pass longer than 90 days is cut, so that a field observed without a break (near the ecliptic poles) still gives several frames. Any number of passes is supported.
- **Passes without a common detector:** near the ecliptic poles the focal plane turns over the field, so each season may see the field with other detectors. If no detector covers the field in every pass, we build the largest set of passes that share at least one detector (then the most shared detectors, then the most exposures) and number those passes again in time order.
- **A detector in a pass:** a detector with fewer than 4 exposures in a pass counts as absent in that pass for the white frame (see Saturation, white frame and colour).
- **Names:** every product names a pass by its time span and middle MJD, never by an index alone, so two products that split passes differently cannot disagree about "pass 2".

**Result:** Barnard's Star (7′) has 3 passes, MJD 60902.9–60949.3, 61110.1–61165.1 and 61268.6–61305.6, and ε Indi B (7′) has 4 passes, from MJD 60789.9 to 61315.5 (Source: docs/validation/2026-11-14-m3-field.md).
