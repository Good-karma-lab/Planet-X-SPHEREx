import { describe, expect, it } from 'vitest';
import { parseHash } from '../../src/lib/route';
import { shareUrl } from '../../src/screens/cabinet/Share';

describe('the public pages open by their links', () => {
  it('a profile and one of its badges', () => {
    expect(parseHash('#/u/ann-k')).toEqual({ screen: 'u', params: { id: 'ann-k' } });
    expect(parseHash('#/u/ann-k?badge=sharp_eye')).toEqual({ screen: 'u', params: { id: 'ann-k', badge: 'sharp_eye' } });
  });
  it('the story of a finding keeps the finding id, colon and all', () => {
    expect(parseHash('#/findings/user:9a3f/story').params['id']).toBe('user:9a3f/story');
    expect(parseHash('#/findings/film%3A2e26f8ee6415/story').params['id']).toBe('film:2e26f8ee6415/story');
  });
});

describe('a share button opens the network’s own window with our link', () => {
  const link = 'https://www.spherex.space/s/achievement/ann.sharp_eye?c=x';
  it('carries the link and the text, encoded', () => {
    expect(shareUrl('x', link, 'I earned “Sharp eye”')).toBe(
      `https://twitter.com/intent/tweet?text=${encodeURIComponent('I earned “Sharp eye”')}&url=${encodeURIComponent(link)}`,
    );
    expect(shareUrl('linkedin', link, 't')).toBe(`https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(link)}`);
    expect(shareUrl('whatsapp', link, 'Look')).toBe(`https://wa.me/?text=${encodeURIComponent(`Look ${link}`)}`);
    for (const c of ['facebook', 'reddit', 'telegram'] as const) expect(shareUrl(c, link, 't')).toContain(encodeURIComponent(link));
  });
});
