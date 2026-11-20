import { describe, expect, it } from 'vitest';
import { restHeight } from '../../src/ui/Sheet';

// The lab's sheet at rest on a phone 360 x 640 (test of 20 Nov, S1): the pane is 584 px high, the playback bar 114 px; under a
// picture of the full width (360 px) there are 110 px. A task card was 319 px high, and the sheet at rest took all of it: the
// picture got 205 px.
describe('the height of a sheet at rest', () => {
  it('a header that fits takes the whole room: no empty band between the picture and the sheet', () => {
    expect(restHeight(90, 110, 90)).toBe(110);
  });
  it('a task higher than the room shows its question and answers only, never the whole card', () => {
    expect(restHeight(319, 110, 172)).toBe(172); // the picture: 584 - 114 - 172 = 298 px, not 205
  });
  it('the room wins where it is larger than what must be seen', () => {
    expect(restHeight(319, 222, 150)).toBe(222); // an upright tablet: the picture keeps its full width
  });
  it('no room under a picture of the full width (a low window): what must be seen, and never more than the card', () => {
    expect(restHeight(129, -200, 129)).toBe(129);
    expect(restHeight(100, -200, 140)).toBe(100);
  });
});
