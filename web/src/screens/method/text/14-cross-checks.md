# Cross-checks and exports

We never trust a result of our own alone. Every track is compared with Gaia DR3, every asteroid with JPL's predictions, every spectrum with 2MASS and WISE, and a lead can be followed in other surveys' light curves, missions and archival images. This part describes each of those cross-checks and the files we give out, with the data source of each. The agreement we obtain is in Validation and dated measurements.

## The globe

The globe is the entrance to the survey. Its backgrounds are the dust map, DSS2, 2MASS, WISE and the SPHEREx cube; its coverage layer counts exposures in HEALPix cells of order 3 (768 cells, each about 7.3° across) and puts an exposure in the cell of its centre. A cell is teal where DR1 exposures exist and blue where only QR3 ones do, with opacity on a log scale of the count.

## Gaia DR3 and the release switch

Gaia DR3 is the reference for every motion we measure: a track between passes is checked against Gaia stars moved to each pass date, an identification asks Gaia for the nearest star, and the cross-match returns its sources.

We ask the Gaia archive (ESA, TAP) first. If it does not answer, or its circuit breaker is open, we read the same catalogue from the public Parquet copy in the HATS layout in STScI's AWS bucket `stpubdata` (`gaia/gaia_dr3/public/hats/gaia`), so a check that needs Gaia keeps working. A Gaia `source_id` carries its HEALPix pixel at order 12 in its high bits (`source_id // 2^35`), and the partitions are sorted by `source_id`. A cone therefore needs only the row groups whose `source_id` range overlaps the order-12 pixels of the cone, a few columns of one or two row groups of about 200 000 rows. The pixels are widened by up to 3′ because a source's pixel is the one of its place near J2000: a fast star such as Barnard's Star moves 2.8′ between 2000 and 2016 and sits in a pixel near the cone, not under it. One read runs at a time, so the fallback never crowds our own reads.

The columns we use are `source_id`, `ra`, `dec`, `pmra`, `pmdec`, `parallax`, `parallax_error`, `ruwe`, `phot_g_mean_mag` and `bp_rp`.

| Item | Value |
|---|---|
| a track is explained (`same`) | a Gaia star with a proper motion of at least 2 mas/yr stays within 3″ of the track at every pass date |
| search cone | 6″ plus 0.2 times the track's speed times the years between the first pass and Gaia's epoch, centred on the track moved to Gaia's epoch |
| a static star (`still`) | a Gaia star within 6″ of the track's middle place that moves less than 30 % of the track's rate: the motion is a centroid effect |
| a pair (`still`) | a static Gaia star (less than 30 % of the track's rate) within 4″ of the track's first place and a different one within 4″ of its last place: the light moves between two stars, as when one star of a close pair varies |
| none | no Gaia star near the track |

The Gaia release is one setting. The environment variables `SKY_GAIA_TABLE` (default `gaiadr3.gaia_source`), `SKY_GAIA_EPOCH` (default 2016.0), `SKY_GAIA_NAME` (default "Gaia DR3") and `SKY_GAIA_TAP` name the table, the epoch of its positions, the label shown to the visitor and the archive; a table with the same column names works with no code change. A readiness check (`GET /api/v1/gaia/readiness`) asks the ESA archive whether a new release's schema exists with the columns above and whether a HATS copy is listed, and answers with the three settings to apply; it changes nothing. The Parquet cone search reads the DR3 files only, so a new release is read through the archive until its own Parquet root is checked.

## Known bodies

A track that no star explains may be a known asteroid or comet. We ask JPL, never a copy of ours, and a JPL that does not answer is said to be unavailable, never an empty list.

For a time-lapse (the visits of one pass) we ask JPL Small-Body Identification (`sb_ident`) for the bodies that cross the field during the pass, at the middle of the pass, over the field widened by how far a fast main-belt body (40″/h) moves in half the pass, up to 3°, with V ≤ 20.5 (SPHEREx sees about V 20 in one visit). We keep the bodies whose straight-line track comes within 0.15° of the field and the 200 brightest. For each we ask JPL Horizons for the geocentric place at the time of every frame (SPHEREx is about 7000 km from the Earth's centre, so the parallax of a body 2 au away is about 5″, under a detector pixel). Three tries, then the body is left out. Comets are asked with their nearest apparition's orbit. The interstellar comet 3I/ATLAS, which Small-Body Identification does not list, is always asked of Horizons directly and kept when it is in the field.

A body's expected visits are the frames whose grid holds its predicted place. The time-lapse's own motion map shows a body on a visit when z reaches 4 within two grid pixels of the predicted place. The visits stacked along the track give the body's significance (the sum of z over the square root of the number of visits at the predicted pixels), and the same stack at four places 20 grid pixels aside gives the chance level.

On the globe we use JPL's Small-Body Database (SBDB) for the catalogue, the orbit, H and diameter of an object by name, and Horizons for its geocentric ephemeris, with the SPHEREx spacecraft (`500@-163182`) as an observer where the position from the satellite matters. The crossings of a body's path with SPHEREx exposures are found from a Horizons ephemeris every 3 hours over the mission: exposures within 0.6 days of each other are one visit, and a field of 12′ is used. SBDB pages are kept one day, ephemerides and objects one week, in memory and on disk.

A track found by our own search is matched to a known body only after the search, so a match never steers the search.

## Counting planes like MOST

For the detector images that hold a moving body we count, with Horizons positions every 30 minutes (geocentric), every exposure of our index whose footprint holds the body at the exposure's mid-time, interpolated between the ephemeris points. That is the quantity IRSA's Moving Object Search Tool (MOST) calls planes. The time-lapse's known-body list counts planes this way for every body brighter than V 19.5. For four main-belt bodies on 14–20 December 2025 the counts equal MOST's plane for plane; the table is in Validation and dated measurements.

## Light curves from ZTF and NEOWISE

A source that varies, or a moving object that was seen before SPHEREx, can be followed in other surveys. For any source we fetch two light curves from IRSA and show them as the source's light curves.

| Survey | Data | Rule |
|---|---|---|
| ZTF | optical light curve, 2018 on (IRSA light-curve service) | place moved to 2021.5 by the proper motion; 2″ cone; points with any catalogue flag left out; band, magnitude and error kept |
| NEOWISE | W1 (3.4 µm) and W2 (4.6 µm), 2014–2024 (`neowiser_p1bs_psd`, IRSA TAP) | place moved to 2019.0; 3″ cone; frames of quality 0 left out |

Each part answers `ok`, `none` or `unavailable` (never cached), at most 3000 points per curve, sorted by time. The NEOWISE table is large and its query takes up to a minute, so it has its own long timeout.

## The NEOSSat frame of 3I/ATLAS

3I/ATLAS was seen by a second space agency on the day SPHEREx observed it. We measure a frame of the Canadian Space Agency's NEOSSat from the Canadian Astronomy Data Centre (CADC): `NEOS_SCI_2025348081359_cord.fits`, 14 December 2025 08:13:59 UTC, exposure 15 s. The frame's header pointing is 43.4″ off, so we refit it on 95 Gaia DR3 stars (zero point G = 24.19) before looking for the comet.

**Result:** the comet is found 5.6″ from JPL Horizons' predicted place (RA 167.10477°, Dec +5.18231°), at G = 12.37 with S/N 154. Source: `web/public/neossat/3i-2025-12-14.json`, measured with `tools/neossat_card.py`.

The card is shown on the time-lapse of 3I/ATLAS with the frame ringed at the predicted place, north up and east left.

## The all-sky cube quick look

Before a measured spectrum arrives we show the place's spectrum from the SPHEREx DR1 all-sky spectral cube, as a quick look, never instead of the measured spectrum. The cube is published as HiPS tiles of HEALPix order 5 in the same public bucket (`dr1/hips5_cube/`, about 320 MB per tile): an image extension of 102 planes of 512 × 512 float32 pixels in MJy/sr, a table giving the wavelength of every plane, and extensions of the same shape for the number of exposures (NHITS) and the flags.

We find the tile by HEALPix, the pixel from the tile's WCS, and read that pixel's 102 values as 102 four-byte range requests at offsets computed from the header. Nothing else of the 320 MB file is fetched, and the headers of a tile are read once and kept. A spectrum arrives in a few seconds. The place is taken at the cube's epoch (2025.9) by its proper motion, and the answer is kept per place.

The pixel is 12.9″ across (one HEALPix pixel of order 14, an equal-area projection) and holds the pipeline's background-subtracted mosaic value. The quick look carries three cautions: it is the mosaic's surface brightness of one pixel and not for photometry; stripes at the sky's helium line (1.083 µm), Pa-α, the 3.3 µm PAH band and Br-α come from the sky and not the object; empty channels are real gaps, where the pipeline wrote nothing at that place. A saturated core or a gap of the map gives no value, and we say so.

[figure: spectrum-source | the quick-look spectrum from the all-sky cube replaced by the measured spectrum of the same source]

The same reader serves `GET /api/v1/cube/spectrum` with a choice of flag handling (`bad`, `all`, `none`), per-point flags and the cautions above.

## Cloud catalogue cross-match

`GET /api/v1/xmatch` returns the catalogue sources at a place from cloud Parquet, with no TAP service in the path: AllWISE with the 2MASS J, H and Ks magnitudes it carries (IRSA's HEALPix Parquet copy in the public bucket `nasa-irsa-wise`) and Gaia DR3 from the copy described above. Each catalogue is searched where the source was at its epoch (2010.5 for AllWISE, 2016.0 for Gaia) by the proper motion given, within a radius of 3″ by default (up to 30″), nearest first, at most 10 rows, each part `ok`, `none` or `unavailable`. AllWISE's own 2MASS association gives J, H and Ks without another catalogue.

## Deep references and archival images

`GET /api/v1/reference/deep` finds deep static optical stacks of CADC that cover a place: processing level 3 or higher and at least 300 s of exposure, from CFHT MegaPipe and HST. The search is a CAOM2 TAP query by position, and cutouts (FITS or PNG, up to 300″ across) come from the archive's SODA service, resolved through DataLink; a 60″ cutout takes about ten seconds the first time.

`GET /api/v1/ssois` lists the archival images (CFHT, Subaru, Pan-STARRS, DECam, HST and others) of CADC's Solar System Object Image Search that contain a named small body in a date range, and `/api/v1/ssois/orbit` does the same for an orbit given by elements: precovery and a way to confirm an orbit. Radio footprints (ALMA, VLA and others) are footprint matches, not images of the object, and are left out unless asked for. The lab links every known body of a time-lapse to its SSOIS search.

## Exports and further tools

**ADES export.** `POST /api/v1/export/ades` writes the positions of a mover lead as an MPC ADES file (pipe-separated, version 2022). SPHEREx is a spacecraft with MPC observatory code 288, so the MPC wants the spacecraft's position for every observation; we take it from Horizons (target −163182, geocentric ICRF, km) when asked. The file lists what the MPC's validator would refuse and answers "ready" only when nothing is missing: a tracklet of at least two observations in time order, submitter, observers and measurers named, a mode from the ADES list, the astrometric catalogue named (not `UNK`), positive uncertainties, a band for every magnitude, the spacecraft position. Nothing is sent anywhere.

**Tracked stack.** `GET /api/v1/film/{key}/tracked` shifts and adds the motion maps of a finished time-lapse along a grid of velocities: a body too faint in any one frame adds up along its path as about A·√n. The velocity grid is spaced so that a body smears at most 2 pixels over the window; windows of 6 hours and a speed limit of 20″/h are the defaults, at least 4 frames are needed, and a grid above 300 pixels is binned 2 × 2 first. The answer lists the strongest peaks per window.

**R Draconis regression field.** The deep collections repeat a few fields many times. `GET /api/v1/regression/r-dra` compares a fixed list of about 2000 Level-2 files that IRSA's SIA service returns for R Draconis (RA 248.1676°, Dec +66.7550°, radius 0.05°) with the exposure index and names any file the archive lists and the index lacks.

## Known objects of a frame

For any frame of the lab we list every Gaia DR3 star, SIMBAD object and JPL Solar System body inside it, each carried to the date of every pass on show, so the ring moves with the star:

- **Gaia DR3:** one cone over the frame (half-diagonal + 130″), the stars of G ≤ 19.0 and every star moving at least 100 mas/yr, at most the 20 000 brightest. The place at 2016.0 is carried by proper motion and parallax as seen from the Earth.
- **SIMBAD:** one cone (half-diagonal + 300″), at most 5000 objects, carried the same way from 2000.0. A SIMBAD object within 1.5″ of a Gaia star is that star: the star takes its name and type.
- **JPL:** Small-Body Identification, geocentric, V ≤ 20.5, at each pass date.

An object is listed when it is inside the frame (+6″) at any date. Names and speeds are shown for the fast and the bright ones (at least 200 mas/yr, or magnitude 10 or brighter); the rest get a thin ring. A catalogue that does not answer is said on the picture ("Gaia did not answer"), never shown as an empty field. The answer comes within about a second with what is ready, and a catalogue still being asked is marked pending and answers on the next ask; when a field is first served, its catalogues are asked at once, so the button answers from what is kept.

**The deep match.** To call a moving point "unknown", a fainter list is checked as well, never drawn: Gaia stars of 19 < G ≤ 21, CatWISE2020 sources with their own proper motion (epoch 2015.4), AllWISE (epoch 2010.5) and 2MASS (epoch 1999.5), all carried to each pass date. Sources of these catalogues within 2.5″ of each other are one source, and one within 2.5″ of a drawn object is that object. A point in the picture within 3″ (`DEEP_ARCSEC`) of a deep source at that pass date has a catalogue match; "unknown" is said only when every deep catalogue has answered. A peak that sits in the glow of a bright star is not called unknown either: when the median of the picture on a ring of 7 pixels around the peak (`GLOW_RADIUS`) is more than 4σ above the picture's floor (`GLOW_SIGMA`), the peak is part of a brighter source's light, which may lie outside the frame.

## Checks before submit

A person's marks are checked before anything is submitted. The marks decide which kind of mover it is: **slow** (it moves between passes) when the marks are in different passes, **fast** (it moves within a pass, visit by visit) when every mark carries its own date and all are in one pass. Each check answers pass, warn, fail or no answer; a fail blocks the submit, a warn needs a comment, and no answer is asked again and blocks until it answers.

| Check | Slow mover | Fast mover |
|---|---|---|
| seen more than once | at least 2 passes | at least 3 dated visits (dates closer than 0.02 day are one visit) |
| straight line | the largest distance of a mark from the line through the first and the last mark: ≤ 3.1″ (half a pixel) pass, ≤ 6.2″ warn, more fail; two passes only: warn | places against time fitted at a constant rate: largest residual ≤ 3.1″ pass; a smooth curve (second degree within 3.1″, or the time-lapse search's orbit model within 2″ rms) warn |
| Gaia, SIMBAD | the catalogue's objects carried to each mark's date: one within 4″ of every mark fail, of some marks warn, none pass | the same |
| JPL | Small-Body Identification once per visit of each mark's pass (at most 24 asks, 15.5 s apart): a body's track within 12″ of a mark fail | one ask per dated mark, at that instant |
| edge and bright star | < 20″ from the field's edge, or ≤ 15″ from a star of magnitude ≤ 9 (it saturates SPHEREx): fail; ≤ 60″ (≤ 120″ for magnitude ≤ 6): warn | the same |
| above the bar | the machine's search between passes has a track within 6.2″ of the marks in the same passes: pass; the search ran and has none: warn (seen by eye only); with the square's stored bar named | the time-lapse search has the track at the marks above 5σ for the visits together: pass; below or none: warn |
| NEOWISE | along the path of the marks carried back, the NEOWISE single-exposure sources (3″, half-year epochs): on the path in ≥ 3 epochs pass, standing still at the first mark in ≥ 3 epochs fail, nothing warn | not applicable to a body this fast: pass |

**The NEOWISE stack.** For a distant-body candidate with an orbit, the NEOWISE W1 single exposures of 2014–2024 over a 1° × 0.3° strip are made into epoch images (gaps of 30 days), the static sky is subtracted and masked, and the differences are added along the orbit, with the flux scale from isolated CatWISE stars of W1 13.5–15.8. Expected at ≥ 5σ and measured below the expectation by 3σ: fail (ruled out); measured above 3σ: pass (seen); else warn (not deep enough).

The checks run in the background; JPL takes minutes and NEOWISE up to about 20 minutes. The person declares the submit once; the finding is submitted by itself when every check has answered without a fail and every warning has a comment, and the person is told the result.
