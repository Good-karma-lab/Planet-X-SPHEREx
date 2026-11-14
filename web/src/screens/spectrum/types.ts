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
