// The portrait of a source: a cutout around it built from its own spectrum. Each detector (0.75 to 5 µm) adds its picture in the
// colour of its wavelength (blue for the shortest, red for the longest), so the colour of the source is its spectrum. A core
// saturated in most exposures is drawn white, never as a hole.

import type { ComponentChildren } from 'preact';
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
  /** the picture's own colour stack of the pass (3 × n × n, float16 bits) and its white balance: the portrait has the picture's colours */
  rgb?: Uint16Array;
  wb?: { kr: number; kb: number };
  /** six detector stacks of one pass, float16 bits, 6 × n × n */
  dets: Uint16Array;
  /** saturated share of the pass, 0..255, n × n */
  sat?: Uint8Array;
  stepArcsec: number;
}

export function Portrait({ data, x, y, label, aside }: { data: PortraitData; x: number; y: number; label: string; aside?: ComponentChildren }) {
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
    const rgb = data.rgb;
    for (let j = 0; j < side; j++)
      for (let i = 0; i < side; i++) {
        const gx = Math.round(x) - half + i;
        const gy = Math.round(y) - half + j;
        if (gx < 0 || gy < 0 || gx >= n || gy >= n) continue;
        const p = gy * n + gx;
        const o = (j * side + i) * 3;
        if (sat && sat[p]! > 127) satAt[j * side + i] = 1;
        if (rgb) {
          // the picture's colour: its three channels with the pass's white balance
          const r = halfToFloat(rgb[p]!) * (data.wb?.kr ?? 1);
          const g = halfToFloat(rgb[nn + p]!);
          const b = halfToFloat(rgb[2 * nn + p]!) * (data.wb?.kb ?? 1);
          acc[o] = Number.isFinite(r) ? Math.max(0, r) : 0;
          acc[o + 1] = Number.isFinite(g) ? Math.max(0, g) : 0;
          acc[o + 2] = Number.isFinite(b) ? Math.max(0, b) : 0;
          const l = (acc[o]! + acc[o + 1]! + acc[o + 2]!) / 3;
          if (!satAt[j * side + i] && l > peak) peak = l;
          continue;
        }
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
      // brightness by the asinh law, colour kept: the three channels scaled together, so a bright red dwarf stays red (never a
      // white blob where every channel clips)
      const r = acc[3 * q]!;
      const g2 = acc[3 * q + 1]!;
      const b = acc[3 * q + 2]!;
      const top = Math.max(r, g2, b);
      const scale = top > 0 ? Math.min(1, Math.asinh(top / k) / norm) / top : 0;
      img.data[4 * q] = Math.round(255 * r * scale);
      img.data[4 * q + 1] = Math.round(255 * g2 * scale);
      img.data[4 * q + 2] = Math.round(255 * b * scale);
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
        {boxArcsec}″ · colour = its spectrum
        {aside}
      </div>
    </div>
  );
}
