# Data used and references

## NASA data

| Data / service | What the tool uses it for | Link |
|---|---|---|
| SPHEREx Level-2 spectral images (DR1, QR3), NASA/IPAC IRSA, public AWS bucket `nasa-irsa-spherex` | every SPHEREx pixel shown and measured | https://irsa.ipac.caltech.edu/Missions/spherex.html · `s3://nasa-irsa-spherex` |
| SPHEREx calibration: per-pixel spectral WCS (`spectral_wcs`), ePSF in every Level-2 file | the wavelength of every pixel; PSF photometry | https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-products |
| IRSA TAP (`spherex.artifact`, `spherex.plane`) | the index of every Level-2 exposure and its footprint | https://irsa.ipac.caltech.edu/docs/program_interface/TAP.html |
| SPHEREx DR1 all-sky cube (HiPS, IRSA) | the globe's SPHEREx background (three frames: 0.91, 1.86, 4.50 µm) | https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube |
| IRSA HiPS mirror: DSS2, 2MASS, AllWISE, IRIS | globe backgrounds; archive images of a field (DSS2 red, 2MASS Ks, AllWISE W1) | https://irsa.ipac.caltech.edu/data/hips/list |
| 2MASS Point Source Catalog, AllWISE (IRSA TAP; IRSA's cloud Parquet copy `nasa-irsa-wise` when the service does not answer) | the catalogue check of a spectrum, the reference photometry of a saturated star | https://irsa.ipac.caltech.edu/docs/program_interface/TAP.html |
| NEOWISE single exposures (IRSA TAP), ZTF light curves (IRSA) | light curves of a clicked source | https://irsa.ipac.caltech.edu/Missions/wise.html · https://irsa.ipac.caltech.edu/Missions/ztf.html |
| SPHEREx 3I/ATLAS exposure table (IRSA) | the 3I/ATLAS demo film | https://irsa.ipac.caltech.edu/data/SPHEREx/docs/comet3iatlas/ |
| JPL Small-Body Identification and Horizons | known bodies predicted in a film, their place on every visit | https://ssd-api.jpl.nasa.gov/doc/sb_ident.html · https://ssd.jpl.nasa.gov/horizons/ |

## Space Agency partner data

| Agency | Data | Use | Link |
|---|---|---|---|
| ESA | Gaia DR3 (Gaia archive TAP; the public Parquet copy at STScI/MAST on AWS when the archive does not answer) | the check of every track found between seasons; names, parallaxes and distances in the Object card and the anomaly passport; the pointing refit of the NEOSSat frame | https://www.cosmos.esa.int/web/gaia/dr3 |
| CSA | NEOSSat frame of 3I/ATLAS, 14 Dec 2025 (Canadian Astronomy Data Centre) | a second space agency's view of the comet SPHEREx saw that day | https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/neossat/ |

## Other data and services

| Source | Use | Link |
|---|---|---|
| SVO Filter Profile Service | 2MASS and WISE filter curves | https://svo2.cab.inta-csic.es/theory/fps/ |
| SIMBAD, VizieR (CDS, Strasbourg), NED (IPAC) | the five test targets; names and types of clicked sources and anomalies; the catalogue context of a place | https://simbad.cds.unistra.fr/simbad/ |
| Minor Planet Center | observatory code 288 (SPHEREx) and the ADES format of the exported positions | https://www.minorplanetcenter.net/ |
| IRAS and AKARI (IRSA) | the far-infrared field of the Planet Nine search of Phan et al. 2025 | https://irsa.ipac.caltech.edu/Missions/iras.html · https://irsa.ipac.caltech.edu/Missions/akari.html |

## References

- SPHEREx: Doré, O. et al. 2014, arXiv:1412.4872; Crill, B. et al. 2020, Proc. SPIE 11443, 114430I.
- SPHEREx data products and Explanatory Supplement, NASA/IPAC IRSA (links above).
- Gaia Collaboration, Vallenari, A. et al. 2023, A&A 674, A1 (Gaia DR3).
- Skrutskie, M. F. et al. 2006, AJ 131, 1163 (2MASS). Wright, E. L. et al. 2010, AJ 140, 1868 (WISE).
- Lasker, B. M. et al. 1990, AJ 99, 2019 (Digitized Sky Survey). Miville-Deschênes, M.-A. & Lagache, G. 2005, ApJS 157, 302 (IRIS).
- Mainzer, A. et al. 2014, ApJ 792, 30 (NEOWISE). Bellm, E. C. et al. 2019, PASP 131, 018002 (ZTF). Laurin, D. et al. 2008, Proc. SPIE 7106 (NEOSSat).
- Wenger, M. et al. 2000, A&AS 143, 9 (SIMBAD). Fernique, P. et al. 2015, A&A 578, A114 (HiPS). Górski, K. M. et al. 2005, ApJ 622, 759 (HEALPix).
