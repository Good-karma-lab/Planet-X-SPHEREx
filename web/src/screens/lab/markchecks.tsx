// A mark's checks as the lab shows them (spec §6): the investigation the service made for it ("It moves" on two passes), each check
// as it answers, and the verdict the checks reach. Asked again every few seconds while they run, never forever.
import { useEffect, useState } from 'preact/hooks';

export interface Check {
  id: string;
  state: string;
  detail?: string;
}
export interface MarkChecks {
  inv: string;
  title: string;
  /** open · checking · blocked · needs_comment · ready · submitted (service/accounts/submit.py state_of) */
  state: string;
  checks: Check[];
  progress: string;
  submitted?: string | null;
}

/** the checks' names as a person reads them */
export const CHECK_WORD: Record<string, string> = {
  two_passes: 'two passes',
  straight_line: 'straight line',
  gaia: 'Gaia',
  jpl: 'JPL',
  simbad: 'SIMBAD',
  edge_bright: 'edge, bright stars',
  above_bar: 'above the bar',
  neowise: 'NEOWISE',
};
export const CHECK_GLYPH: Record<string, string> = { pass: '✓', warn: '!', fail: '✗', no_answer: '↻' };

/** The investigation holding a mark (or its mover's other mark), followed while its checks run. */
export function useMarkChecks(markIds: string[], on: boolean): MarkChecks | null | 'none' {
  const [c, setC] = useState<MarkChecks | null | 'none'>(null);
  const key = markIds.join(',');
  useEffect(() => {
    setC(null);
    if (!on || !markIds.length) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const t0 = Date.now();
    const ask = async () => {
      try {
        const r = await fetch('/api/me/investigations', { cache: 'no-store' });
        if (stop) return;
        if (!r.ok) throw new Error(String(r.status));
        const d = (await r.json()) as {
          investigations?: { id: string; title: string; state: string; mark_ids: string[]; progress?: string; submitted?: string | null; checks?: { checks?: Check[] } | null }[];
        };
        const inv = (d.investigations ?? []).find((x) => x.mark_ids.some((id) => markIds.includes(id)));
        if (!inv) {
          setC('none');
        } else {
          setC({ inv: inv.id, title: inv.title, state: inv.state, checks: inv.checks?.checks ?? [], progress: inv.progress ?? '', submitted: inv.submitted });
          if (inv.state !== 'checking') return; // a verdict: nothing more to ask
        }
      } catch {
        // asked again below
      }
      // while the checks run (or the service has not made the investigation yet): every 6 s, for at most 20 minutes
      if (!stop && Date.now() - t0 < 20 * 60 * 1000) timer = setTimeout(() => void ask(), 6000);
    };
    void ask();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [key, on]);
  return c;
}

/** The verdict line of a mark's checks, in the service's own words where it gives them. */
export function checksVerdict(c: MarkChecks): { text: string; state: 'now' | 'done' | 'bad' } {
  const rows = c.checks;
  const fail = rows.find((x) => x.state === 'fail');
  if (c.state === 'submitted') return { text: 'Submitted · in Findings', state: 'done' };
  if (fail) return { text: `${CHECK_WORD[fail.id] ?? fail.id}: ${fail.detail || 'it fails'}`, state: 'bad' };
  if (c.state === 'ready') return { text: 'Every check passed · ready to submit', state: 'done' };
  if (c.state === 'needs_comment') return { text: 'A check warns · add a comment, then submit', state: 'now' };
  const answered = rows.filter((x) => ['pass', 'warn', 'fail'].includes(x.state)).length;
  const asking = rows.find((x) => x.state === 'no_answer' || (x.state === 'running' && x.detail));
  if (c.state === 'blocked') return { text: asking?.detail || 'The checks could not finish · asked again by themselves', state: 'bad' };
  // what the slowest check is doing, in the service's words ("Asking JPL for the bodies of visit 2 of 4")
  const said = rows.find((x) => x.state === 'running' && x.detail)?.detail ?? '';
  return { text: `checks running · ${answered} of ${rows.length || 8} answered${said ? ` · ${said.replace(/\.$/, '')}` : ''}`, state: 'now' };
}

/** What the service concluded about a mark (GET /api/me/marks: `verdict`), its text ready to show. */
export interface MarkVerdict {
  /** known · no_match · needs_another_pass · waiting · checking · comment */
  state: string;
  text: string;
  name?: string;
  retry_at?: string;
  investigation_id?: string;
  finding_id?: string;
}

/** Every mark's verdict, read again every 30 s while one of them is still being checked or waits for a catalogue. */
export function useMarkVerdicts(on: boolean, touch: string): Record<string, MarkVerdict> {
  const [v, setV] = useState<Record<string, MarkVerdict>>({});
  useEffect(() => {
    if (!on) return setV({});
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ask = async () => {
      let again = false;
      try {
        const r = await fetch('/api/me/marks', { cache: 'no-store' });
        if (stop) return;
        if (r.ok) {
          const d = (await r.json()) as { marks?: { id: string; verdict?: MarkVerdict | null }[] };
          const out: Record<string, MarkVerdict> = {};
          for (const m of d.marks ?? []) if (m.verdict?.text) out[m.id] = m.verdict;
          setV(out);
          again = Object.values(out).some((x) => x.state === 'checking' || x.state === 'waiting');
        } else again = true;
      } catch {
        again = true;
      }
      if (!stop && again) timer = setTimeout(() => void ask(), 30000);
    };
    // a mark just put or given its kind: its verdict is asked for a moment later
    timer = setTimeout(() => void ask(), 1500);
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [on, touch]);
  return v;
}
