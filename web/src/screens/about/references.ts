/** Everything the site is built on, with a link to each: the challenge, the mission and its data, the archives and catalogues we read, and the papers behind the method. */
export interface Ref {
  name: string;
  /** what we take from it, in a few words */
  note: string;
  url: string;
}

export interface RefGroup {
  title: string;
  refs: Ref[];
}

export const REFERENCES: RefGroup[] = [
  {
    title: 'The challenge',
    refs: [
      {
        name: 'NASA Space Apps Challenge 2026: Planet X and SPHEREx',
        note: 'the task this site answers',
        url: 'https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/',
      },
    ],
  },
  {
    title: 'SPHEREx: the mission and its data',
    refs: [
      { name: 'SPHEREx mission (Caltech)', note: 'the mission and its instrument', url: 'https://spherex.caltech.edu/' },
      { name: 'SPHEREx mission overview (NASA/JPL)', note: 'orbit, survey plan, goals', url: 'https://www.jpl.nasa.gov/press-kits/spherex/mission-overview/' },
      { name: 'SPHEREx at IRSA', note: 'the archive that serves the data', url: 'https://irsa.ipac.caltech.edu/Missions/spherex.html' },
      {
        name: 'SPHEREx data releases: DR1 and QR3',
        note: 'the Level-2 spectral images we read',
        url: 'https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-releases',
      },
      {
        name: 'SPHEREx data access (IRSA documentation)',
        note: 'file layout, S3 bucket, services',
        url: 'https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-access',
      },
      {
        name: 'SPHEREx data products (IRSA documentation)',
        note: 'extensions, flags, calibration files',
        url: 'https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-products',
      },
      { name: 'SPHEREx Explanatory Supplement', note: 'the pipeline and the files', url: 'https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf' },
      { name: 'IRSA tutorials for SPHEREx', note: 'cutouts, PSF, spectra', url: 'https://caltech-ipac.github.io/irsa-tutorials/spherex/' },
      { name: 'SPHEREx all-sky spectral cube (DR1, HiPS)', note: 'the quick-look spectrum and the globe layer', url: 'https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube' },
      { name: 'SPHEREx observations of 3I/ATLAS (IRSA)', note: 'the comet demo', url: 'https://irsa.ipac.caltech.edu/data/SPHEREx/docs/comet3iatlas/index.html' },
      { name: 'IRSA cloud data access', note: 'the public bucket and the Parquet catalogues', url: 'https://irsa.ipac.caltech.edu/cloud_access/' },
    ],
  },
  {
    title: 'Archives and catalogues we read',
    refs: [
      { name: 'Gaia DR3 (ESA)', note: 'positions, proper motions, parallaxes', url: 'https://www.cosmos.esa.int/web/gaia/dr3' },
      { name: 'Gaia Archive (ESA)', note: 'the TAP service', url: 'https://gea.esac.esa.int/archive/' },
      { name: 'Gaia at MAST (STScI)', note: 'the public Parquet copy of DR3', url: 'https://archive.stsci.edu/missions-and-data/gaia' },
      { name: '2MASS (IRSA)', note: 'J, H, Ks photometry', url: 'https://irsa.ipac.caltech.edu/Missions/2mass.html' },
      { name: 'AllWISE (IRSA)', note: 'W1–W4 photometry and the 2MASS association', url: 'https://irsa.ipac.caltech.edu/Missions/wise.html' },
      { name: 'CatWISE2020 (IRSA)', note: 'motions of WISE sources', url: 'https://irsa.ipac.caltech.edu/data/WISE/CatWISE/' },
      { name: 'NEOWISE (IRSA)', note: 'single-exposure light curves and archive images', url: 'https://irsa.ipac.caltech.edu/Missions/wise.html' },
      { name: 'IRAS (IRSA)', note: 'the 1983 far-infrared sky', url: 'https://irsa.ipac.caltech.edu/Missions/iras.html' },
      { name: 'AKARI (IRSA)', note: 'the 2006 far-infrared sky', url: 'https://irsa.ipac.caltech.edu/Missions/akari.html' },
      { name: 'Zwicky Transient Facility (IRSA)', note: 'optical light curves', url: 'https://irsa.ipac.caltech.edu/Missions/ztf.html' },
      { name: 'SIMBAD (CDS)', note: 'names and types of objects', url: 'https://simbad.cds.unistra.fr/' },
      { name: 'VizieR (CDS)', note: 'catalogue cross-checks, variable stars', url: 'https://vizier.cds.unistra.fr/' },
      { name: 'NASA/IPAC Extragalactic Database (NED)', note: 'galaxies near a source', url: 'https://ned.ipac.caltech.edu/' },
      { name: 'JPL Small-Body Database', note: 'orbits of asteroids and comets', url: 'https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html' },
      { name: 'JPL Horizons', note: 'positions of bodies and of the spacecraft', url: 'https://ssd.jpl.nasa.gov/horizons/' },
      { name: 'JPL Small-Body Identification API', note: 'which known bodies lie in a field', url: 'https://ssd-api.jpl.nasa.gov/doc/sb_ident.html' },
      { name: 'Minor Planet Center', note: 'observatory code 288 (SPHEREx) and the ADES format', url: 'https://www.minorplanetcenter.net/' },
      { name: 'HiPS sky surveys at IRSA (DSS2, 2MASS, WISE, IRIS)', note: 'the globe backgrounds and the archive images', url: 'https://irsa.ipac.caltech.edu/data/hips/list' },
      { name: 'Canadian Astronomy Data Centre (CADC)', note: 'deep optical references, NEOSSat frames', url: 'https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/' },
      { name: 'CSA NEOSSat', note: 'the 3I/ATLAS frame', url: 'https://www.asc-csa.gc.ca/eng/satellites/neossat/' },
      { name: 'NEOSSat dataset (CSA)', note: 'the open data of the satellite', url: 'https://data.asc-csa.gc.ca/dataset/neossat' },
      { name: 'WiseView (Backyard Worlds)', note: 'the blink we took as our model', url: 'http://byw.tools/wiseview' },
    ],
  },
  {
    title: 'Papers',
    refs: [
      {
        name: 'Tombaugh, C. W. 1946, The Search for the Ninth Planet, Pluto (Astronomical Society of the Pacific Leaflet 209)',
        note: 'how Pluto was found with a blink comparator',
        url: 'https://ui.adsabs.harvard.edu/abs/1946ASPL....5...73T/abstract',
      },
      {
        name: 'Slipher, V. M. 1930, Planet X: Lowell Observatory Observation Circular of 13 March 1930 (reprinted in JRASC 24, 282)',
        note: 'the announcement of the discovery of Pluto',
        url: 'https://ui.adsabs.harvard.edu/abs/1930JRASC..24..282S/abstract',
      },
      {
        name: 'Trujillo & Sheppard 2014, Nature 507, 471: a Sedna-like body with a perihelion of 80 au',
        note: 'the clustering of distant orbits',
        url: 'https://doi.org/10.1038/nature13156',
      },
      {
        name: 'Batygin & Brown 2016, AJ 151, 22: evidence for a distant giant planet in the solar system',
        note: 'the Planet Nine hypothesis',
        url: 'https://arxiv.org/abs/1601.05438',
      },
      { name: 'Brown & Batygin 2021: the orbit of Planet Nine', note: 'the orbit and sky region our prior follows', url: 'https://arxiv.org/abs/2108.09868' },
      { name: 'Batygin, Adams, Brown & Becker 2019: the Planet Nine hypothesis', note: 'review of the evidence', url: 'https://arxiv.org/abs/1902.10103' },
      { name: 'Meisner et al. 2018: a search for Planet Nine in WISE', note: 'a time-resolved 3.4 µm search', url: 'https://arxiv.org/abs/1712.04950' },
      { name: 'Phan, Goto et al. 2025: a search for Planet Nine with IRAS and AKARI', note: 'the far-infrared search', url: 'https://arxiv.org/abs/2504.17288' },
      { name: 'Crill et al. 2020: SPHEREx, NASA’s near-infrared spectrophotometric all-sky survey', note: 'the instrument paper', url: 'https://arxiv.org/abs/2404.11017' },
      { name: 'Akeson et al.: the SPHEREx image and spectrophotometry processing pipeline', note: 'the Level-2 pipeline', url: 'https://arxiv.org/abs/2511.15823' },
    ],
  },
];
