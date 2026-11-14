// The portrait of a source: a cutout around it built from its own spectrum. Each detector (0.75 to 5 µm) adds its picture in the
// colour of its wavelength (blue for the shortest, red for the longest), so the colour of the source is its spectrum. A core
// saturated in most exposures is drawn white, never as a hole.

import { useEffect, useRef } from 'preact/hooks';
import { halfToFloat } from '../lib/sxp';

// the detectors' wavelengths as colours, short to long
const DET_RGB: [number, number, number][] = [
  [0.35, 0.3, 1.0],
  [0.2, 0.6, 1.0],
  [0.2, 0.95, 0.8],
  [0.45, 1.0, 0.3],
  [1.0, 0.75, 0.2],
  [1.0, 0.25, 0.2],
];

export interface PortraitData {
  n: number;
  /** six detector stacks of one pass, float16 bits, 6 × n × n */
  dets: Uint16Array;
  /** saturated share of the pass, 0..255, n × n */
  sat?: Uint8Array;
  stepArcsec: number;
}

export function Portrait({ data, x, y, label }: { data: PortraitData; x: number; y: number; label: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const boxArcsec = 60;
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const { n, dets, sat } = data;
    const half = Math.max(6, Math.round(boxArcsec / data.stepArcsec / 2));
    const side = 2 * half + 1;
    c.width = c.height = side;
    const g = c.getContext('2d')!;
    const img = g.createImageData(side, side);
    const acc = new Float32Array(side * side * 3);
    const satAt = new Uint8Array(side * side);
    let peak = 0;
    const nn = n * n;
    for (let j = 0; j < side; j++)
      for (let i = 0; i < side; i++) {
        const gx = Math.round(x) - half + i;
        const gy = Math.round(y) - half + j;
        if (gx < 0 || gy < 0 || gx >= n || gy >= n) continue;
        const p = gy * n + gx;
        const o = (j * side + i) * 3;
        if (sat && sat[p]! > 127) satAt[j * side + i] = 1;
        for (let d = 0; d < 6; d++) {
          const v = halfToFloat(dets[d * nn + p]!);
          if (!Number.isFinite(v) || v <= 0) continue;
          const c3 = DET_RGB[d]!;
          acc[o] = acc[o]! + v * c3[0];
          acc[o + 1] = acc[o + 1]! + v * c3[1];
          acc[o + 2] = acc[o + 2]! + v * c3[2];
        }
        const l = (acc[o]! + acc[o + 1]! + acc[o + 2]!) / 3;
        if (!satAt[j * side + i] && l > peak) peak = l;
      }
    const k = Math.max(peak, 1e-6) * 0.02;
    const norm = Math.asinh(50);
    for (let q = 0; q < side * side; q++) {
      if (satAt[q]) {
        img.data.set([255, 255, 255, 255], 4 * q);
        continue;
      }
      for (let ch = 0; ch < 3; ch++) img.data[4 * q + ch] = Math.round(255 * Math.min(1, Math.asinh(acc[3 * q + ch]! / k) / norm));
      img.data[4 * q + 3] = 255;
    }
    g.putImageData(img, 0, 0);
  }, [data, x, y]);
  return (
    <div
      class="portrait"
      title="Portrait built from the source's own spectrum: each detector's picture in the colour of its wavelength, added"
    >
      <canvas ref={ref} />
      <div class="pt-key">
        <b>Portrait · {label}</b>
        <div class="pt-bar">
          {DET_RGB.map((c) => (
            <span style={{ flex: 1, background: `rgb(${c.map((v) => Math.round(255 * v)).join(',')})` }} />
          ))}
        </div>
        0.75 µm → 5 µm: each detector in the colour of its wavelength, added. A saturated core is white.
        <br />
        {boxArcsec}″ across, north up, east left.
      </div>
    </div>
  );
}
