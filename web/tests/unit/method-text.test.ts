import { describe, expect, it } from 'vitest';
import { render } from '../../src/screens/method/md';

// every part of the Method page, in file order
const files = import.meta.glob('../../src/screens/method/text/*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
const parts = Object.entries(files)
  .map(([path, text]) => ({ id: path.replace(/^.*\//, '').replace(/\.md$/, ''), text }))
  .sort((a, b) => a.id.localeCompare(b.id));

const BANNED = [/star-test/i, /\bresearch\b/i, /\bported?\b/i, /\bporting\b/i, /\boffline pipeline\b/i, /not built/i, /to be checked/i, /\bTODO\b/, /SkyBoT/, /[А-Яа-я]/];
const FIGURES = new Set(['pendulum-barnard', 'film-ecliptic-dec2025', 'slider-eris', 'spectrum-source', 'wall-detectors', 'anomaly-map', 'raw-to-video', 'blink-pluto', 'p9-orbits', 'depth-vs-wise']);

describe('Method text', () => {
  it('has the sixteen parts in order', () => {
    expect(parts.map((p) => p.id.slice(0, 2))).toEqual(Array.from({ length: 16 }, (_, i) => String(i + 1).padStart(2, '0')));
  });
  for (const { id, text } of parts) {
    describe(id, () => {
      it('starts with a title and a lead before the first section', () => {
        expect(text.startsWith('# ')).toBe(true);
        const r = render(text, id);
        expect(r.html).toContain('class="mf-lead"');
        expect(r.html.indexOf('class="mf-lead"')).toBeLessThan(r.html.indexOf('<h3'));
      });
      it('has short section headings and no plan numbering', () => {
        const r = render(text, id);
        const sections = r.headings.filter((h) => h.level === 2);
        expect(sections.length).toBeGreaterThanOrEqual(2);
        for (const h of sections) {
          expect(h.title.split(/\s+/).length).toBeLessThanOrEqual(7);
          expect(h.title).not.toMatch(/^\d+(\.\d+)+/);
        }
      });
      it('uses no banned words and only known figure names', () => {
        for (const re of BANNED) expect(text).not.toMatch(re);
        for (const m of text.matchAll(/^\[figure:\s*([a-z0-9-]+)/gm)) expect(FIGURES.has(m[1]!)).toBe(true);
      });
      it('closes every code fence and every Result line has a source', () => {
        expect((text.match(/^```/gm) ?? []).length % 2).toBe(0);
        for (const m of text.matchAll(/^\*\*Result:\*\*.*$/gm)) expect(m[0]).toMatch(/Source|live service|docs\//i);
      });
    });
  }
});
