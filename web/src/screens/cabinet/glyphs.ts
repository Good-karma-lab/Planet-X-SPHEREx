// The line glyphs of the achievements, drawn for this app in the thin-stroke style of ui/icons.ts (24 × 24 box, strokes only).
// One glyph per achievement; the tiers of one family share a glyph and differ by the ring around it.

const c = (cx: number, cy: number, r: number) => `<circle cx="${cx}" cy="${cy}" r="${r}"/>`;
const dot = (cx: number, cy: number, r = 0.9) => `<circle cx="${cx}" cy="${cy}" r="${r}" fill="currentColor" stroke="none"/>`;
const p = (d: string) => `<path d="${d}"/>`;

export const GLYPHS: Record<string, string> = {
  // a square of sky with a star in it
  square: p('M5 5h14v14H5z') + dot(12, 12, 1.3),
  // four squares: a patch of the grid
  grid: p('M4.5 4.5h15v15h-15zM12 4.5v15M4.5 12h15'),
  // a mark: a point with a ring and ticks
  mark: c(12, 12, 4) + dot(12, 12) + p('M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3'),
  check: c(12, 12, 8) + p('M8 12.3l2.8 2.8L16.2 9.5'),
  // a name tag
  tag: p('M4 8.5h16v9H4zM9 8.5V6h6v2.5M7.5 12h6M7.5 14.6h9'),
  eye: p('M2.8 12s3.4-5.6 9.2-5.6S21.2 12 21.2 12s-3.4 5.6-9.2 5.6S2.8 12 2.8 12z') + c(12, 12, 2.4),
  // a run of right answers: three ticks in a row
  run: p('M3.5 12.5l2 2 3.4-3.6M10 12.5l2 2 3.4-3.6M16.5 12.5l2 2 3.4-3.6') + p('M3.5 18.5h17'),
  // nine of ten: a target
  target: c(12, 12, 8) + c(12, 12, 4.6) + dot(12, 12, 1.2),
  book: p('M4 5.5c2.8-1 5.4-1 8 .6 2.6-1.6 5.2-1.6 8-.6v13c-2.8-1-5.4-1-8 .6-2.6-1.6-5.2-1.6-8-.6zM12 6.1v13'),
  // an asteroid: an uneven body with a crater, on its path
  asteroid: p('M8 7.5l5-2 4.5 2.5 1 5-3 4.5-5.5.8-3.8-3.2-.6-4.6z') + c(13, 11.5, 1.4) + p('M3 20.5l3-2.2'),
  // an artefact: a spike across a star (a false point)
  artefact: p('M12 6.2l1.4 4.4 4.4 1.4-4.4 1.4-1.4 4.4-1.4-4.4-4.4-1.4 4.4-1.4z') + p('M4.5 19.5l15-15'),
  // a star that runs: a point with a trail of earlier places
  runner: dot(17, 7.5, 1.7) + c(12.2, 12, 1.1) + c(8.4, 15.4, 0.9) + c(5.4, 18.2, 0.7) + p('M17 3.6v1.6M17 9.8v1.6M13.1 7.5h1.6M19.3 7.5h1.6'),
  // a cold neighbour: a small dim body with bands
  dwarf: c(12, 12, 6.5) + p('M6.2 9.8h11.6M5.6 12.6h12.8M7 15.4h10'),
  // a far world: a small body on a wide, tilted orbit
  farworld: `<ellipse cx="12" cy="12" rx="9" ry="4.2" transform="rotate(-20 12 12)"/>` + dot(12, 12, 1.2) + c(19.2, 8.2, 1.3),
  // a ringed planet: the Planet Nine band
  ringed: c(12, 12, 4.4) + `<ellipse cx="12" cy="12" rx="9.4" ry="2.6" transform="rotate(-18 12 12)"/>`,
  // the ecliptic: the Sun on its line
  ecliptic: c(12, 12, 3) + p('M2.5 15.5c6-6 13-6 19 0M12 5.2v1.6M6.6 7.4l1.1 1.1M17.4 7.4l-1.1 1.1'),
  // both skies: a sphere with its equator, a star above and below
  hemispheres: c(12, 12, 8) + p('M4 12h16') + dot(12, 8) + dot(12, 16),
  // a pole: a sphere, its axis and a flag
  pole: c(12, 14, 6) + p('M12 3v17M12 3.6l4.5 1.7L12 7'),
  // the Milky Way: a band of stars across the square
  milkyway: p('M3.5 16.5c5-1 12-6.5 17-9.5M3.5 20c6-1.4 12-6 17-9') + dot(8, 14.6) + dot(12, 12.6) + dot(16, 9.6) + dot(6, 6, 0.7) + dot(18.5, 17.5, 0.7),
  clock: c(12, 12, 8) + p('M12 7.2V12l3.2 2'),
  // first eyes: a flag planted
  flag: p('M6.5 20.5v-16M6.5 5.2c4-2 6.5 2 10.5 0v7c-4 2-6.5-2-10.5 0'),
  note: p('M6 4.5h9l3 3v12H6zM15 4.5v3h3M8.8 11.5h6.4M8.8 14.5h6.4'),
  ruler: p('M3.5 15.5l12-12 5 5-12 12zM7.2 12l1.8 1.8M10 9.2l1.8 1.8M12.8 6.4l1.8 1.8'),
  // time traveller: a clock that runs back
  back: p('M5.2 9A8 8 0 1 1 4 12.6M5.2 4.6V9h4.4M12 8v4.2l2.8 1.6'),
  // case closed: a folder with a tick
  folder: p('M3.5 7h6l1.8 2h9.2v9.5h-17zM9 14l2 2 4-4.2'),
  // a submit: an arrow up from a tray
  submit: p('M12 15.5V4.5M7.8 8.6L12 4.5l4.2 4.1M4.5 14.5v5h15v-5'),
  // a clean sheet
  sheet: p('M6.5 4h11v16h-11zM9.2 11.8l2 2 3.8-4'),
  hourglass: p('M7 4h10M7 20h10M8 4c0 4.5 4 5.5 4 8s-4 3.5-4 8M16 4c0 4.5-4 5.5-4 8s4 3.5 4 8'),
  // a candidate: a star in a ring
  medal: c(12, 12, 8) + p('M12 7.6l1.3 2.9 3.1.3-2.3 2.1.7 3.1-2.8-1.6-2.8 1.6.7-3.1-2.3-2.1 3.1-.3z'),
  trophy: p('M8 4.5h8v5a4 4 0 0 1-8 0zM8 6H4.8c0 3 1.4 4.4 3.4 4.6M16 6h3.2c0 3-1.4 4.4-3.4 4.6M12 13.5v3.5M8.5 19.5h7M9.5 17h5'),
  // a second look: two eyes
  peers: p('M2.5 12s2-3.4 5-3.4 5 3.4 5 3.4-2 3.4-5 3.4-5-3.4-5-3.4zM11.5 12s2-3.4 5-3.4 5 3.4 5 3.4-2 3.4-5 3.4-5-3.4-5-3.4z') + dot(7.5, 12, 1.1) + dot(16.5, 12, 1.1),
  calendar: p('M4.5 6.5h15v13h-15zM4.5 10.5h15M8.5 4v4M15.5 4v4') + dot(8.5, 14) + dot(12, 14) + dot(15.5, 14),
  weekend: p('M4.5 6.5h15v13h-15zM4.5 10.5h15M8.5 4v4M15.5 4v4') + dot(13.5, 15.5, 1.2) + dot(16.6, 15.5, 1.2),
  podium: p('M3.5 20V13h5.5v7M9 20V7.5h6V20M15 20v-9.5h5.5V20M2.5 20h19'),
  telescope: p('M4 13.5l11-5.2 1.8 3.8-11 5.2zM15 8.3l3.2-1.5 1.8 3.8-3.2 1.5M10.5 15.2L8 20.5M10.5 15.2l3.5 5.3'),
  moon: p('M19 14.5A8 8 0 0 1 9.5 5a8 8 0 1 0 9.5 9.5z') + dot(16.5, 6.5, 0.8),
  scales: p('M12 4.5v15M7.5 19.5h9M5 7.5h14M5 7.5l-2.4 6a2.6 2.6 0 0 0 4.8 0zM19 7.5l-2.4 6a2.6 2.6 0 0 0 4.8 0z'),
};

/** The glyph of each achievement (the ids of service/accounts/score.py). */
export const GLYPH_OF: Record<string, string> = {
  first_light: 'square', first_mark: 'mark', first_check: 'check', named: 'tag',
  stargazer: 'grid', surveyor: 'grid', mapper: 'grid', cartographer: 'grid', atlas: 'grid', sky_keeper: 'grid',
  sharp_eye: 'eye', eagle_eye: 'eye', never_fooled: 'run', nine_of_ten: 'target', flawless_hundred: 'run',
  catalogue_reader: 'book', librarian: 'book', asteroid_wrangler: 'asteroid', artefact_spotter: 'artefact', ghostbuster: 'artefact',
  barnards_runner: 'runner', cold_neighbour: 'dwarf', far_world: 'farworld',
  p9_band: 'ringed', p9_deep: 'ringed', ecliptic_walker: 'ecliptic', both_skies: 'hemispheres', polar_explorer: 'pole', milky_way: 'milkyway', around_the_clock: 'clock',
  pioneer: 'flag', trailblazer: 'flag', frontier: 'flag',
  note_taker: 'note', measurer: 'ruler', time_traveller: 'back', case_closed: 'folder',
  first_submit: 'submit', clean_sheet: 'sheet', patient: 'hourglass', five_passed: 'submit',
  candidate: 'medal', three_candidates: 'medal', confirmed: 'trophy',
  peer: 'peers', referee: 'peers',
  days_3: 'calendar', days_7: 'calendar', days_30: 'calendar', days_100: 'calendar', weekend_watch: 'weekend',
  top_ten_week: 'podium', first_week: 'podium', first_month: 'podium',
  tombaugh: 'telescope', night_owl: 'moon', second_opinion: 'scales',
}; // prettier-ignore
