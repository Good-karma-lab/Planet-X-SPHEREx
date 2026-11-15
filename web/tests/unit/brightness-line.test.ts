import { describe, expect, it } from 'vitest';
import { brightnessLine, type BrightnessResult } from '../../src/screens/brightness-line';

const det = (ratio: number | null, err = 0.001) =>
  ratio == null ? null : { flux_mjy: 1, err_mjy: 0.01, ratio_to_first: ratio, ratio_err: err };
const result = (second: Record<string, ReturnType<typeof det>>, scatter: Record<string, number>, stars = 12): BrightnessResult => ({
  passes: [
    { index: 0, mjd: 1, detectors: {} },
    { index: 1, mjd: 2, detectors: second },
  ],
  control: { stars, scatter },
});

describe('brightnessLine', () => {
  it('says a field without control stars cannot tell a change from scatter', () => {
    const line = brightnessLine('RW Aur', result({ D4: det(0.8), D5: det(0.8, 0) }, {}, 1))!;
    expect(line).toContain('too few control stars');
    expect(line).not.toContain('faded');
  });
  it('names the bands that changed beyond twice the control scatter, with their signs', () => {
    const line = brightnessLine('RW Aur', result({ D1: det(1.1), D4: det(0.78), D6: det(0.6, 0) }, { D1: 0.04, D4: 0.05, D6: 0.09 }))!;
    expect(line).toContain('brightened 0.1 mag at 0.75–1.1 µm');
    expect(line).toContain('faded 0.3 mag at 2.4–3.8 µm, 0.6 mag at 4.4–5.0 µm');
  });
  it('says nothing changed when every change is inside the scatter', () => {
    const line = brightnessLine('R Dra', result({ D2: det(0.85), D5: det(0.77) }, { D2: 0.228, D5: 0.315 }))!;
    expect(line).toContain('did not change beyond the scatter');
  });
  it('treats a measured error of exactly zero as a measurement, not as a missing one', () => {
    const line = brightnessLine('X', result({ D6: det(0.48, 0) }, { D6: 0.05 }))!;
    expect(line).toContain('faded');
  });
  it('is empty with one pass', () => {
    expect(brightnessLine('X', { passes: [{ index: 0, mjd: 1, detectors: {} }], control: { stars: 0, scatter: {} } })).toBeNull();
  });
});
