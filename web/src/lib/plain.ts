/**
 * The service's progress words said for a visitor: no workers, jobs, blocks or pools. One place, used by every status line
 * (field, searches, films), so the service can keep its own wording in its logs.
 */
export function plainPhase(phase: string | null | undefined): string {
  const s = (phase ?? '').trim();
  if (!s) return '';
  let m = s.match(/^waiting for a free worker \((\d+) ahead\)/);
  if (m) return `Request queued · ${m[1]} ahead`;
  if (/^waiting for a free worker/.test(s)) return 'Request queued';
  if (s === 'starting') return 'Finding the exposures…';
  if (/^paused: a person is working/.test(s)) return 'Paused: requests of people at the page come first';
  m = s.match(/^scanning distance and own motion: block (\d+) of (\d+) \((\d+) s, about (\d+) s left\)/);
  if (m) return `Searching every distance and motion: ${m[1]} of ${m[2]} (about ${m[4]} s left)`;
  m = s.match(/^scanning distance and own motion: (\d+) blocks, about (\d+) s/);
  if (m) return `Searching every distance and motion: about ${m[2]} s`;
  m = s.match(/^summing along the orbits: job (\d+) of (\d+)/);
  if (m) return `Adding the passes along the orbits: ${m[1]} of ${m[2]}`;
  return s.replace(/\bworker processes?\b/g, 'the server').replace(/\bin a worker\b/g, 'on the server');
}
