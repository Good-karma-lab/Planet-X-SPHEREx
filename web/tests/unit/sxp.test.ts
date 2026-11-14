import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { halfToFloat, parseSxp } from '../../src/lib/sxp';

const fixture = fileURLToPath(new URL('../fixtures/sample.sxp', import.meta.url));

describe('SXP1 parts written by the service', () => {
  it('reads the meta and every array', () => {
    const b = readFileSync(fixture);
    const part = parseSxp(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
    expect(part.meta).toEqual({ pass: 2, note: 'fixture written by service/products/sxp.py' });
    const rgb = part.arrays['rgb']!;
    expect(rgb.dtype).toBe('f2');
    expect(rgb.shape).toEqual([1, 2, 2]);
    const v = Array.from(rgb.data as Uint16Array).map(halfToFloat);
    expect(v[0]).toBe(1);
    expect(Number.isNaN(v[1])).toBe(true);
    expect(v[2]).toBe(0.5);
    expect(v[3]).toBe(-2);
    expect(Array.from(part.arrays['sat']!.data)).toEqual([0, 255, 128, 1]);
    expect(part.arrays['w']!.data[0]).toBeCloseTo(3.25);
  });
  it('refuses other bytes', () => {
    expect(() => parseSxp(new ArrayBuffer(16))).toThrow('not an SXP1 part');
  });
});
