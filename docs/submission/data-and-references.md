# Data used and references — draft

## NASA data (at least one NASA resource is required for global awards)

| Data / service | What the tool uses it for | Link |
|---|---|---|
| SPHEREx Level-2 spectral images (DR1, QR3), NASA/IPAC IRSA, public AWS bucket `nasa-irsa-spherex` | every SPHEREx pixel shown and measured | https://irsa.ipac.caltech.edu/Missions/spherex.html · https://registry.opendata.aws/ (bucket `s3://nasa-irsa-spherex`) |
| SPHEREx calibration files (spectral WCS, ePSF) | wavelength of every pixel, PSF photometry | https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-products |
| IRSA TAP and SIA services (`spherex.plane`, `spherex.artifact`) | the list of exposures that cover a field | https://irsa.ipac.caltech.edu/docs/program_interface/TAP.html |
| 2MASS (Point Source Catalog and images), NASA/IPAC | catalogue check of spectra; archive images | https://irsa.ipac.caltech.edu/Missions/2mass.html |
| WISE / AllWISE / CatWISE / NEOWISE, NASA/IPAC | catalogue check, archive images, archive check of distant-body candidates | https://irsa.ipac.caltech.edu/Missions/wise.html |
| JPL Horizons and Small-Body Database (incl. Small-Body Identification) | ephemerides of known asteroids and comets seen from SPHEREx; known-body checks | https://ssd.jpl.nasa.gov/horizons/ · https://ssd-api.jpl.nasa.gov/doc/sbdb.html |
| IRSA HiPS mirror (DSS2, 2MASS, AllWISE) | globe background | https://irsa.ipac.caltech.edu/data/hips/list |

## Space Agency Partner data

| Agency | Data | Use | Link |
|---|---|---|---|
| ESA | Gaia DR3 | positions and proper motions of stars: checks of every track, star masks, distances | https://www.cosmos.esa.int/web/gaia/dr3 |

*(Partner data use is encouraged. Candidates if the user wants more: CSA NEOSSat via CADC for a courtesy check of a moving lead — not
built.)*

## Other data and services

| Source | Use | Link |
|---|---|---|
| SIMBAD (CDS, Strasbourg) | names, types, catalogue motions | https://simbad.cds.unistra.fr/simbad/ |
| VizieR / AAVSO VSX, NED | context of a lead | https://vizier.cds.unistra.fr/ · https://www.aavso.org/vsx/ · https://ned.ipac.caltech.edu/ |
| IMCCE SkyBoT | known small bodies in a field at a time | https://ssp.imcce.fr/webservices/skybot/ |
| STScI Digitized Sky Survey, Pan-STARRS1 | archive images back to 1950 | https://archive.stsci.edu/cgi-bin/dss_form · https://ps1images.stsci.edu/ |
| SVO Filter Profile Service | 2MASS and WISE filter curves | https://svo2.cab.inta-csic.es/theory/fps/ |

## References

- SPHEREx: Doré, O. et al. 2014, arXiv:1412.4872; Crill, B. et al. 2020, Proc. SPIE 11443, 114430I.
- SPHEREx Explanatory Supplement and data products, NASA/IPAC IRSA (links above).
- Gaia Collaboration, Vallenari, A. et al. 2023, A&A 674, A1 (Gaia DR3).
- Skrutskie, M. F. et al. 2006, AJ 131, 1163 (2MASS). Wright, E. L. et al. 2010, AJ 140, 1868 (WISE). Mainzer, A. et al. 2014, ApJ 792, 30 (NEOWISE).
- Wenger, M. et al. 2000, A&AS 143, 9 (SIMBAD). Berthier, J. et al. 2006, ASP Conf. Ser. 351, 367 (SkyBoT). Giorgini, J. D. et al. 1996, BAAS 28, 1158 (Horizons).
- Lasker, B. M. et al. 1990, AJ 99, 2019 (DSS). Chambers, K. C. et al. 2016, arXiv:1612.05560 (Pan-STARRS1).
- Fortney, J. J. et al. 2016, ApJL 824, L25 (infrared brightness of a cold Planet Nine). Phan, A. et al. 2025 (IRAS/AKARI Planet Nine candidate) **[check the exact citation]**.
- Backyard Worlds / WiseView (inspiration for the blink): https://www.ascl.net/1806.004.
