// The layout has three modes (phone, tablet, desktop; src/responsive.css says which windows are which). Every @media rule in the
// stylesheets is written in their terms: a width of its own (760 px, 1100 px…) is how one screen's rules ended up fighting another's.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

// the window conditions a rule may use, each one mode or a named part of one
const MODES: Record<string, string> = {
  '(max-width: 599.98px) and (min-height: 480px)': 'phone, upright',
  '(max-width: 599.98px) and (min-height: 480px) and (max-height: 739.98px)': 'phone, upright and short',
  '(max-height: 479.98px) and (max-width: 1279.98px)': 'phone, on its side',
  '(min-width: 600px) and (max-width: 1279.98px) and (min-height: 480px)': 'tablet',
  '(min-width: 600px) and (max-width: 999.98px) and (min-height: 480px)': 'tablet, one pane (narrow)',
  '(min-width: 1000px) and (max-width: 1279.98px) and (orientation: portrait)': 'tablet, one pane (upright)',
  '(min-width: 1000px) and (max-width: 1279.98px) and (min-height: 480px) and (orientation: landscape)': 'tablet, two panes',
  '(max-width: 1279.98px)': 'phone or tablet',
  '(min-width: 600px) and (min-height: 480px)': 'tablet or desktop',
  '(min-width: 1280px)': 'desktop',
  '(min-width: 1280px) and (max-height: 720px)': 'desktop, short window',
};
// conditions that are not about the window's size
const OTHER = [/^\(prefers-reduced-motion: reduce\)$/, /^\(hover: hover\)$/];

const css = (dir: string): string[] =>
  readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? css(p) : p.endsWith('.css') ? [p] : [];
  });

describe('three layout modes', () => {
  it('every @media rule names a mode, not a width of its own', () => {
    const bad: string[] = [];
    for (const file of css(join(__dirname, '../../src'))) {
      for (const m of readFileSync(file, 'utf8').matchAll(/@media\s+([^{]+)\{/g)) {
        for (const part of m[1]!.trim().split(/\s*,\s*/)) {
          const q = part.replace(/\s+and\s+\(pointer: coarse\)$/, '');
          if (!(q in MODES) && !OTHER.some((r) => r.test(q))) bad.push(`${file.split('/src/')[1]}: ${part}`);
        }
      }
    }
    expect(bad).toEqual([]);
  });
});
