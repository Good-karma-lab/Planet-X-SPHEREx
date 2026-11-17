// The aim: the place and field size the globe points at and the Lab opens. One per page, shared by the screens.

export interface AimState {
  ra: number;
  dec: number;
  fov: number;
}

type Listener = (a: AimState, source: string) => void;

const listeners = new Set<Listener>();
// Barnard's Star, where SIMBAD places it (J2000)
let current: AimState = { ra: 269.452077, dec: 4.693365, fov: 10 }; // the lab's field: people look 10′ at a time

export const aim = {
  get: (): AimState => current,
  set(next: Partial<AimState>, source: string): void {
    current = { ...current, ...next };
    for (const f of listeners) f(current, source);
  },
  on(f: Listener): () => void {
    listeners.add(f);
    return () => listeners.delete(f);
  },
};
