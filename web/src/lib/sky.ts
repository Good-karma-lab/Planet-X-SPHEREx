// Sky coordinates as people type and read them: sexagesimal with h m s / d m s, or plain degrees.

export const D2R = Math.PI / 180;

const numbers = (s: string) => (s.match(/\d+(?:\.\d+)?/g) ?? []).map(Number);
const two = (n: number) => String(n).padStart(2, '0');

/** RA in degrees from "17h 57m 48.5s", "17 57 48.5" (hours) or "269.45" (degrees); null when it cannot be read or is out of range. */
export function parseRA(text: string): number | null {
  const s = text.trim().toLowerCase();
  const n = numbers(s);
  if (!n.length || n.length > 3) return null;
  const deg = n.length === 1 && !s.includes('h') ? n[0]! : (n[0]! + (n[1] ?? 0) / 60 + (n[2] ?? 0) / 3600) * 15;
  return deg >= 0 && deg < 360 ? deg : null;
}

/** Dec in degrees from "+04d 41m 36s", "-56 47 0" or "4.69"; null when it cannot be read or is out of range. */
export function parseDec(text: string): number | null {
  const s = text.trim().replace('−', '-');
  const n = numbers(s);
  if (!n.length || n.length > 3) return null;
  const v = n[0]! + (n[1] ?? 0) / 60 + (n[2] ?? 0) / 3600;
  const deg = s.startsWith('-') ? -v : v;
  return Math.abs(deg) <= 90 ? deg : null;
}

/** "17h 57m 47.11s" (or without spaces for links). */
export function formatRA(deg: number, compact = false): string {
  const total = Math.round(((((deg % 360) + 360) % 360) / 15) * 3600 * 100) % (24 * 3600 * 100);
  const h = Math.floor(total / 360000);
  const m = Math.floor((total % 360000) / 6000);
  const s = (total % 6000) / 100;
  const sep = compact ? '' : ' ';
  return `${two(h)}h${sep}${two(m)}m${sep}${s.toFixed(2).padStart(5, '0')}s`;
}

/** "+04d 46m 04.5s" (or without spaces for links). */
export function formatDec(deg: number, compact = false): string {
  const sign = deg < 0 ? '-' : '+';
  const total = Math.round(Math.abs(deg) * 3600 * 10);
  const d = Math.floor(total / 36000);
  const m = Math.floor((total % 36000) / 600);
  const s = (total % 600) / 10;
  const sep = compact ? '' : ' ';
  return `${sign}${two(d)}d${sep}${two(m)}m${sep}${s.toFixed(1).padStart(4, '0')}s`;
}

/** Angular distance in degrees. */
export function separation(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const a = Math.sin(((dec2 - dec1) * D2R) / 2) ** 2 + Math.cos(dec1 * D2R) * Math.cos(dec2 * D2R) * Math.sin(((ra2 - ra1) * D2R) / 2) ** 2;
  return (2 * Math.asin(Math.min(1, Math.sqrt(a)))) / D2R;
}

/** Where a star is at an epoch: its J2000 place moved by proper motion (mas/yr). */
export function atEpoch(ra: number, dec: number, pmra: number, pmdec: number, epoch: number, from = 2000): [number, number] {
  const dt = epoch - from;
  const d = dec + (pmdec * dt) / 3.6e6;
  const r = ra + (pmra * dt) / 3.6e6 / Math.max(0.05, Math.cos(((d + dec) / 2) * D2R));
  return [((r % 360) + 360) % 360, d];
}

/** The middle of the SPHEREx passes: a field opened from the globe is centred where a moving star is during the survey. */
export const SURVEY_EPOCH = 2025.9;
