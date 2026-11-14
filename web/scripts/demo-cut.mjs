// Cuts the recorded demo (scripts/demo-video.mjs) to 30 s: each scene from its mark, for a fixed length, then the title card.
//   cd web && node scripts/demo-cut.mjs [out-dir] [result.mp4]
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const OUT = process.argv[2] ?? '../docs/submission/demo/out';
const MP4 = process.argv[3] ?? '../docs/submission/demo/spherex-sky-demo.mp4';
const marks = Object.fromEntries(JSON.parse(readFileSync(join(OUT, 'marks.json'), 'utf8')).map((m) => [m.name, m.t]));
// scene: [mark, offset after the mark, length]
const scenes = [
  ['globe', 0.2, 4.0],
  ['pendulum', 0.0, 6.0],
  ['archive', 0.0, 5.6],
  ['spectrum', 1.3, 5.5],
  ['anomaly', 0.3, 5.5],
  ['title', 0.2, 3.0],
];
const parts = scenes.map(([m, off, len], i) => `[0:v]trim=${(marks[m] + off).toFixed(2)}:${(marks[m] + off + len).toFixed(2)},setpts=PTS-STARTPTS[s${i}]`);
const filter = `${parts.join(';')};${scenes.map((_, i) => `[s${i}]`).join('')}concat=n=${scenes.length}:v=1:a=0,fps=30,format=yuv420p[v]`;
execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-i', join(OUT, 'raw.webm'), '-filter_complex', filter, '-map', '[v]', '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-movflags', '+faststart', MP4], { stdio: 'inherit' });
console.log(`${MP4}: ${scenes.reduce((s, x) => s + x[2], 0).toFixed(1)} s`);
