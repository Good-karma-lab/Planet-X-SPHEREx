import { describe, expect, it } from 'vitest';
import { render } from '../../src/screens/method/md';

describe('method markdown', () => {
  const md = [
    '# Title',
    '',
    'The lead paragraph.',
    '',
    '## First',
    '',
    'Plain text.',
    '',
    '**Result:** a number, one source.',
    '',
    '[figure: pendulum-barnard | Barnard blink]',
    '',
    'Result: plain form.',
  ].join('\n');
  const r = render(md, 'm-01-x');
  it('marks the lead, the results and the figure slots', () => {
    expect(r.html).toContain('<p class="mf-lead">The lead paragraph.</p>');
    expect(r.html).toContain('<p class="mf-result"><span class="mf-result-k">Result</span> a number, one source.</p>');
    expect(r.html).toContain('<p class="mf-result"><span class="mf-result-k">Result</span> plain form.</p>');
    expect(r.html).toContain('data-fig="pendulum-barnard"');
    expect(r.html).toContain('<figcaption>Barnard blink</figcaption>');
  });
  it('keeps plain paragraphs and headings', () => {
    expect(r.html).toContain('<p>Plain text.</p>');
    expect(r.headings.map((h) => h.title)).toEqual(['Title', 'First']);
    expect(r.headings[1]!.id).toBe('m-01-x-first');
  });
  it('a figure without a caption has no figcaption', () => {
    expect(render('[figure: slider-eris]', 'p').html).not.toContain('figcaption');
  });
});

describe('method figures', () => {
  it('a registered video becomes a video element with its poster, an unregistered name stays an empty slot', () => {
    const r = render('[figure: raw-exposures | caption]\n\n[figure: slider-eris]', 'p');
    expect(r.html).toContain('<video class="mf-media" src="/method/raw-exposures.mp4" poster="/method/raw-exposures.jpg"');
    expect(r.html).toContain('data-fig="slider-eris"><\/div>');
  });
  it('every registered file exists on disk', async () => {
    const { FIGURES } = await import('../../src/screens/method/figures');
    const { existsSync } = await import('node:fs');
    for (const f of Object.values(FIGURES)) {
      expect(existsSync(`public${f.src}`)).toBe(true);
      if (f.poster) expect(existsSync(`public${f.poster}`)).toBe(true);
    }
  });
});
