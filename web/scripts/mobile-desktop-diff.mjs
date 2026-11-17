// Proof that the responsive layer leaves a desktop alone: compares the layout dumps (`--geom` of mobile-shots.mjs) of two builds,
// element by element: the box and the computed look of everything on every screen.
//
//   node scripts/mobile-desktop-diff.mjs <before dir> <after dir>      (the folders that hold NN-screen.geom.json)
//
// The elements the layer adds for touch windows (not displayed on a desktop) are left out of the "after" list before comparing.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const [A, B] = process.argv.slice(2);
const ours = (e) => /\.(tab-more|sheet-grip|touch-only)(\.|$)/.test(e.el);
let bad = 0;
for (const f of readdirSync(A).filter((x) => x.endsWith('.geom.json')).sort()) {
  let a;
  let b;
  try {
    a = JSON.parse(readFileSync(join(A, f), 'utf8'));
    b = JSON.parse(readFileSync(join(B, f), 'utf8'));
  } catch {
    console.log(`${f}: missing in one of the two`);
    bad++;
    continue;
  }
  // our hidden additions, and what is inside them
  const hidden = b.filter(ours);
  const b2 = [];
  let skip = 0;
  for (const e of b) {
    if (ours(e)) {
      skip = e.el.includes('tab-more') ? 3 : e.el.includes('sheet-grip') ? 1 : 0; // the icon and the word of More; the bar of the grabber
      continue;
    }
    if (skip > 0 && e.box[2] === 0 && e.box[3] === 0) {
      skip--;
      continue;
    }
    skip = 0;
    b2.push(e);
  }
  const shown = hidden.filter((e) => e.box[2] > 0 || e.box[3] > 0 || !e.css.startsWith('none|'));
  const diffs = [];
  if (a.length !== b2.length) diffs.push(`element count ${a.length} → ${b2.length}`);
  const n = Math.min(a.length, b2.length);
  let moved = 0;
  let restyled = 0;
  const ex = [];
  for (let i = 0; i < n; i++) {
    const m = a[i].box.some((v, k) => Math.abs(v - b2[i].box[k]) > 0.01);
    const r = a[i].css !== b2[i].css || a[i].el !== b2[i].el;
    if (m) moved++;
    if (r) restyled++;
    if ((m || r) && ex.length < 4) ex.push(`${a[i].el} [${a[i].box}] → ${b2[i].el} [${b2[i].box}]${r && !m ? ' (look)' : ''}`);
  }
  if (moved || restyled) diffs.push(`${moved} boxes differ, ${restyled} looks differ of ${n}`);
  if (shown.length) diffs.push(`${shown.length} touch-only element(s) displayed: ${shown.map((e) => e.el).join(', ')}`);
  if (diffs.length) {
    bad++;
    console.log(`${f}: ${diffs.join('; ')}\n    ${ex.join('\n    ')}`);
  } else console.log(`${f}: identical (${n} elements)`);
}
console.log(bad ? `${bad} screen(s) differ` : 'every screen is identical');
process.exit(bad ? 1 : 0);
