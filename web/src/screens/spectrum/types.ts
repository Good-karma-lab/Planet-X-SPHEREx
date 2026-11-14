export interface Source {
  ra: number;
  dec: number;
  /** a catalogue object: its name and proper motion (mas/yr, J2000 place) */
  name?: string;
  pmra?: number;
  pmdec?: number;
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
