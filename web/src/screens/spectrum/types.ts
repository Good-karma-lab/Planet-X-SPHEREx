export interface Source {
  ra: number;
  dec: number;
  /** the service's id of the find this is (its spectrum leaves out what people left out for it) */
  finding?: string;
  /** a catalogue object: its name and proper motion (mas/yr, J2000 place) */
  name?: string;
  pmra?: number;
  pmdec?: number;
  /** where the motion comes from: a catalogue (the default), or the lab's own search (a mover track, a red source, an anomaly) */
  pmKind?: 'catalogue' | 'measured';
  /** the uncertainty of a measured motion, mas/yr */
  pmErr?: number;
  /** a moving body (an asteroid track of the film search): its brightness changes with its distance from the Sun and the Earth and with
   *  its phase, so it has no variable-star verdict; JPL's predicted V and what each visit shows are given instead */
  moving?: { V?: number | null; visits: { n: number; z: number; how: 'found' | 'recovered' }[] };
}

export interface SpecPoint {
  i: number;
  mjd: number;
  det: number;
  lam: number;
  ok: boolean;
  why: string;
  flux?: number;
  err?: number;
}

/** The pixels behind one point of a spectrum (GET /api/v1/spectrum/stamps). Arrays are [row][column]; null = no value. */
export interface Stamp {
  i: number;
  /** 0 nothing read, 1 file pixels only, 2 fitted */
  status: 0 | 1 | 2;
  raw?: (number | null)[][];
  sky?: number;
  flux?: number;
  data?: (number | null)[][];
  /** flux × PSF + sky */
  model?: (number | null)[][];
  resid?: (number | null)[][];
  used?: number[][];
}

export interface CatRow {
  band: string;
  synth_mjy?: number | null;
  cat_mjy?: number | null;
  mag?: number | null;
  ratio?: number | null;
  note?: string;
}
