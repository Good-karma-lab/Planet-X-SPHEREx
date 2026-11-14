# Data used and references

## NASA data

| Data / service | What the tool uses it for | Link |
|---|---|---|
| SPHEREx Level-2 spectral images (DR1, QR3), NASA/IPAC IRSA, public AWS bucket `nasa-irsa-spherex` | every SPHEREx pixel shown and measured | https://irsa.ipac.caltech.edu/Missions/spherex.html · `s3://nasa-irsa-spherex` |
| SPHEREx calibration: per-pixel spectral WCS (`spectral_wcs`), ePSF in every Level-2 file | the wavelength of every pixel; PSF photometry | https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-products |
| IRSA TAP (`spherex.artifact`, `spherex.plane`) | the index of every Level-2 exposure and its footprint | https://irsa.ipac.caltech.edu/docs/program_interface/TAP.html |
| IRSA HiPS mirror: DSS2, 2MASS, AllWISE, IRIS | globe backgrounds; archive images of a field (DSS2 red, 2MASS Ks, AllWISE W1) | https://irsa.ipac.caltech.edu/data/hips/list |

## Space Agency partner data

| Agency | Data | Use | Link |
|---|---|---|---|
| ESA | Gaia DR3 (Gaia archive TAP) | the check of every track found between seasons; names, parallaxes and distances in the Object card and the anomaly passport | https://www.cosmos.esa.int/web/gaia/dr3 |

## Other data and services

| Source | Use | Link |
|---|---|---|
| SIMBAD (CDS, Strasbourg) | the five test targets and the globe's catalogue of 4,100 objects (dated snapshot); names and types of clicked sources and anomalies | https://simbad.cds.unistra.fr/simbad/ |

## References

- SPHEREx: Doré, O. et al. 2014, arXiv:1412.4872; Crill, B. et al. 2020, Proc. SPIE 11443, 114430I.
- SPHEREx data products and Explanatory Supplement, NASA/IPAC IRSA (links above).
- Gaia Collaboration, Vallenari, A. et al. 2023, A&A 674, A1 (Gaia DR3).
- Skrutskie, M. F. et al. 2006, AJ 131, 1163 (2MASS). Wright, E. L. et al. 2010, AJ 140, 1868 (WISE).
- Lasker, B. M. et al. 1990, AJ 99, 2019 (Digitized Sky Survey). Miville-Deschênes, M.-A. & Lagache, G. 2005, ApJS 157, 302 (IRIS).
- Wenger, M. et al. 2000, A&AS 143, 9 (SIMBAD). Fernique, P. et al. 2015, A&A 578, A114 (HiPS). Górski, K. M. et al. 2005, ApJ 622, 759 (HEALPix).
