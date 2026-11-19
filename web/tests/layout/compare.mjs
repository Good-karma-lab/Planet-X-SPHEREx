// node tests/layout/compare.mjs before/report.json after/report.json — what moved (>4 px) per page and size
import fs from 'node:fs';
const [a, b] = process.argv.slice(2).map((p) => JSON.parse(fs.readFileSync(p, 'utf8')));
for (const k of Object.keys(b)) {
  if (!a[k]) { console.log(`${k}: new`); continue; }
  const sa = a[k].sig, sb = b[k].sig, moved = [], gone = [], added = [];
  for (const id of Object.keys(sa)) {
    if (!sb[id]) { gone.push(id); continue; }
    if (sa[id].some((v, i) => Math.abs(v - sb[id][i]) > 4)) moved.push(`${id} ${sa[id].join(',')} → ${sb[id].join(',')}`);
  }
  for (const id of Object.keys(sb)) if (!sa[id]) added.push(id);
  const n = moved.length + gone.length + added.length;
  console.log(`${k}: ${n ? `${moved.length} moved, ${gone.length} gone, ${added.length} new` : 'same'} · overlaps ${a[k].overlaps.length} → ${b[k].overlaps.length}`);
  if (n && process.env.V) { for (const m of moved.slice(0, +process.env.V)) console.log('   ~', m); for (const g of gone.slice(0, +process.env.V)) console.log('   -', g); for (const g of added.slice(0, +process.env.V)) console.log('   +', g); }
}
