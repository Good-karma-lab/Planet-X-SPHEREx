// HEALPix in the nested scheme (Górski et al. 2005): the pixel of a sky position, the corners of a pixel, and the place of a
// position inside a tile of an all-sky survey (HiPS).

const D2R = Math.PI / 180;
const JRLL = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4];
const JPLL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7];

function spread(v: number, bits: number): number {
  let r = 0;
  for (let b = 0; b < bits; b++) r += ((v >> b) & 1) * 2 ** (2 * b);
  return r;
}

/** Face and integer face coordinates of a position at an order (up to 13, so that pixel numbers stay exact doubles). */
export function faceXY(order: number, ra: number, dec: number): { face: number; ix: number; iy: number } {
  const nside = 2 ** order;
  const z = Math.sin(dec * D2R);
  const za = Math.abs(z);
  const tt = ((((ra % 360) + 360) % 360) / 90) % 4;
  if (za <= 2 / 3) {
    const t1 = nside * (0.5 + tt);
    const t2 = nside * z * 0.75;
    const jp = Math.floor(t1 - t2);
    const jm = Math.floor(t1 + t2);
    const ifp = Math.floor(jp / nside);
    const ifm = Math.floor(jm / nside);
    const face = ifp === ifm ? (ifp % 4) + 4 : ifp < ifm ? ifp % 4 : (ifm % 4) + 8;
    return { face, ix: jm % nside, iy: nside - (jp % nside) - 1 };
  }
  const ntt = Math.min(Math.floor(tt), 3);
  const tp = tt - ntt;
  const tmp = nside * Math.sqrt(3 * (1 - za));
  const jp = Math.min(Math.floor(tp * tmp), nside - 1);
  const jm = Math.min(Math.floor((1 - tp) * tmp), nside - 1);
  return z >= 0 ? { face: ntt, ix: nside - jm - 1, iy: nside - jp - 1 } : { face: ntt + 8, ix: jp, iy: jm };
}

export function ang2pix(order: number, ra: number, dec: number): number {
  const { face, ix, iy } = faceXY(order, ra, dec);
  return face * 4 ** order + spread(ix, order) + 2 * spread(iy, order);
}

/** Sky position of a point given by continuous face coordinates x, y in [0, 1]. */
function facePoint(face: number, x: number, y: number): [number, number] {
  let jr = JRLL[face]! - x - y;
  let z: number;
  let nr: number;
  if (jr < 1) {
    nr = jr;
    z = 1 - (jr * jr) / 3;
  } else if (jr > 3) {
    jr = 4 - jr;
    nr = jr;
    z = (jr * jr) / 3 - 1;
  } else {
    nr = 1;
    z = ((2 - jr) * 2) / 3;
  }
  let t = JPLL[face]! * nr + x - y;
  if (t < 0) t += 8;
  if (t >= 8) t -= 8;
  const phi = nr < 1e-15 ? 0 : (t * Math.PI) / 4 / nr;
  return [((phi / D2R) % 360 + 360) % 360, Math.asin(Math.max(-1, Math.min(1, z))) / D2R];
}

/** The outline of a pixel: `steps` points per edge, as [ra, dec] pairs. */
export function outline(order: number, pix: number, steps = 3): [number, number][] {
  const nside = 2 ** order;
  const per = 4 ** order;
  const face = Math.floor(pix / per);
  let local = pix % per;
  let ix = 0;
  let iy = 0;
  for (let b = 0; b < order; b++) {
    ix += (local % 2) * 2 ** b;
    local = Math.floor(local / 2);
    iy += (local % 2) * 2 ** b;
    local = Math.floor(local / 2);
  }
  const pts: [number, number][] = [];
  const edge = (x0: number, y0: number, x1: number, y1: number) => {
    for (let s = 0; s < steps; s++) pts.push(facePoint(face, (x0 + ((x1 - x0) * s) / steps) / nside, (y0 + ((y1 - y0) * s) / steps) / nside));
  };
  edge(ix, iy, ix + 1, iy);
  edge(ix + 1, iy, ix + 1, iy + 1);
  edge(ix + 1, iy + 1, ix, iy + 1);
  edge(ix, iy + 1, ix, iy);
  return pts;
}
