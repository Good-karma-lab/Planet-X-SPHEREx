// One investigation, opened inside the cabinet: its marks, notes, saved frames, the live state of its checks job
// (GET /api/checks/{job}), "Check and submit" (POST /api/checks, then POST /api/findings/submit) and the verdict.

import { useEffect, useState } from 'preact/hooks';
import { api, type AccountMark } from '../../lib/account';
import { formatDec, formatRA } from '../../lib/sky';
import { Pic } from './pic';
import type { Investigation, MyFinding } from './Cabinet';

interface Check {
  id: string;
  state: string;
  detail?: string;
}
interface ChecksJob {
  job: string;
  state: string;
  checks: Check[];
  can_submit: boolean;
  needs_comment?: string[];
}

const when = (iso: string | null) => (iso ? `${iso.slice(0, 16).replace('T', ' ')} UTC` : '');
const words = (s: string) => s.replace(/_/g, ' ');
const VERDICT: Record<string, string> = {
  submitted: 'Submitted. Waiting for the verdict.',
  in_review: 'In review.',
  known: 'Verdict: a known object.',
  artefact: 'Verdict: an artefact.',
  candidate: 'Verdict: a candidate. It is in no catalogue we checked.',
  confirmed: 'Verdict: confirmed.',
};

/** The checks job of an investigation, asked again every few seconds until every check has answered. */
function useChecks(job: string | null): { checks: ChecksJob | null; note: string } {
  const [checks, setChecks] = useState<ChecksJob | null>(null);
  const [note, setNote] = useState('');
  useEffect(() => {
    setChecks(null);
    setNote('');
    if (!job) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ask = () =>
      api<ChecksJob>(`/api/checks/${encodeURIComponent(job)}`).then(
        (c) => {
          if (stop) return;
          setChecks(c);
          setNote('');
          if (c.state !== 'done') timer = setTimeout(ask, 4000);
        },
        (e: Error) => {
          if (stop) return;
          setNote(`Checks did not answer: ${e.message}`);
          timer = setTimeout(ask, 15000);
        },
      );
    void ask();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [job]);
  return { checks, note };
}

export function InvestigationPage({ inv, marks, finding, reload }: { inv: Investigation; marks: AccountMark[]; finding: MyFinding | null; reload: () => void }) {
  const [note, setNote] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const [declNote, setDeclNote] = useState('');
  const { checks, note: checksNote } = useChecks(inv.checks_job);
  const own = marks.filter((m) => inv.mark_ids.includes(m.id));
  const items = inv.items ?? [];
  // marks of the investigation that came with no object (put there before the lab added objects)
  const loose = own.filter((m) => !items.some((it) => it.mark_ids.includes(m.id)));
  const run = (job: Promise<unknown>) => {
    setBusy(true);
    setStatus('');
    return job.then(reload, (e: Error) => setStatus(e.message)).finally(() => setBusy(false));
  };
  const addNote = (e: Event) => {
    e.preventDefault();
    if (!note.trim()) return;
    void run(api(`/api/me/investigations/${inv.id}/notes`, { method: 'POST', body: { text: note.trim() } }).then(() => setNote('')));
  };
  const startChecks = () => {
    const start = async () => {
      let body: { field_key: string; ra: number; dec: number; marks: { pass: number; ra: number; dec: number; mjd?: number }[] } | null = null;
      if (inv.finding_id) {
        // a machine finding brought to submit: the dated visits the machine measured it in (a body that moves inside one pass)
        const f = await api<{ ra: number; dec: number; data?: { visits?: { ra: number; dec: number; mjd: number }[] } }>(`/api/v1/findings/${encodeURIComponent(inv.finding_id)}`).catch(() => null);
        const visits = (f?.data?.visits ?? []).filter((v) => Number.isFinite(v.ra) && Number.isFinite(v.dec) && Number.isFinite(v.mjd));
        if (f && visits.length >= 2) body = { field_key: '', ra: f.ra, dec: f.dec, marks: visits.map((v) => ({ pass: 0, ra: v.ra, dec: v.dec, mjd: v.mjd })) };
      }
      const first = own[0];
      if (!body && first) {
        const key = /^[A-Za-z0-9_,.+-]*$/.test(first.field_key) ? first.field_key : ''; // a store key, or the lab's "ra,dec,fov"
        body = { field_key: key, ra: first.ra, dec: first.dec, marks: own.map((m) => ({ pass: m.pass ?? 0, ra: m.ra, dec: m.dec, ...(m.mjd != null ? { mjd: m.mjd } : {}) })) };
      }
      if (!body) throw new Error('No marks in this investigation: put a mark in the lab first');
      const r = await api<{ job: string }>('/api/checks', { method: 'POST', body });
      // said once: from here the server finishes the submit by itself, whether this page stays open or not
      await api(`/api/me/investigations/${inv.id}/submit`, { method: 'POST', body: { checks_job: r.job, note: declNote.trim() || undefined } });
      setDeclNote('');
    };
    void run(start());
  };
  const rows = checks?.checks ?? [];
  const waiting = rows.filter((c) => ['running', 'queued', 'waiting'].includes(c.state));
  const said = waiting.find((c) => c.state === 'running' && c.detail)?.detail ?? '';
  // a job that ended with a check to ask again keeps the server's own line ("JPL did not answer and is asked again")
  const progress = checks && checks.state !== 'done' ? `${rows.length - waiting.length} of ${rows.length} checks answered${said ? ` · ${said}` : ''}` : inv.progress;
  const warns = rows.filter((c) => c.state === 'warn');
  const need = Math.max(0, warns.length - inv.notes.length);
  const failed = rows.find((c) => c.state === 'fail') ?? rows.find((c) => !['pass', 'warn'].includes(c.state));
  // the server settles a declared submit when the job ends; the page then reads the investigation again
  const jobDone = checks?.state === 'done';
  const anyFail = rows.some((c) => c.state === 'fail'); // a fail is final at once, whatever the slower checks will say
  useEffect(() => {
    if ((jobDone || anyFail) && inv.state === 'checking') reload();
  }, [jobDone, anyFail, inv.state]);
  const stage = inv.submitted ? (finding?.status ?? 'submitted') : null;
  return (
    <div class="cb-page" data-testid="investigation-page">
      <a class="btn sm cb-back" href="#/me">
        ← All investigations
      </a>
      <div class="cb-title">
        <b>{inv.title}</b>
        <span class="mono-s">changed {when(inv.updated)}</span>
        {stage && <span class={`badge st-${stage}`}>{words(stage)}</span>}
      </div>
      {stage && (
        <p class="cb-status good" data-testid="verdict">
          {VERDICT[stage] ?? words(stage)} Finding {inv.submitted}
        </p>
      )}
      {inv.submitted && (
        <a class="btn sm cb-back" href={`#/findings/${inv.submitted}/story`} data-testid="to-story">
          Write the story and share it
        </a>
      )}

      <h4>Objects</h4>
      {items.length === 0 && own.length === 0 && <span class="mono-s">Nothing gathered yet: add an object in the lab</span>}
      <div class="cb-list" data-testid="inv-items">
        {items.map((it) => {
          const mine = marks.filter((m) => it.mark_ids.includes(m.id));
          const passes = [...new Set(mine.map((m) => m.pass).filter((p): p is number => p != null))].sort((a, b) => a - b);
          return (
            <div key={it.id} class="cb-row haspic" data-testid="inv-item">
              {it.thumb ? <img class="cb-pic" src={it.thumb} alt="" width={96} height={96} /> : <Pic findingId={it.finding_id} ra={it.ra} dec={it.dec} label={it.label || 'object'} />}
              <span class="cb-main">
                <b>{it.label || 'Object'}</b>
                <span class="mono-s">
                  {formatRA(it.ra, true)} {formatDec(it.dec, true)}
                  {mine.length > 0 ? ` · ${mine.length} ${mine.length === 1 ? 'mark' : 'marks'}${passes.length ? ` on ${passes.length === 1 ? 'pass' : 'passes'} ${passes.map((p) => p + 1).join(', ')}` : ''}` : ' · no marks'}
                </span>
                {it.ruler && (
                  <span class="cb-note" data-testid="inv-ruler">
                    Ruler: {it.ruler.rate.toFixed(2)}″/yr{it.ruler.pa != null ? ` toward ${Math.round(it.ruler.pa)}°` : ''}
                    {it.ruler.shift != null ? `, ${it.ruler.shift.toFixed(1)}″ between the passes` : ''}
                    {it.ruler.catalogue_rate != null ? ` · the catalogue: ${it.ruler.catalogue_rate.toFixed(2)}″/yr${it.ruler.object ? ` (${it.ruler.object})` : ''}` : ''}
                  </span>
                )}
                <span class="cb-note" data-testid="inv-catalogue">
                  {it.catalogue ? `Catalogue: ${it.catalogue.name}${it.catalogue.type ? `, ${it.catalogue.type}` : ''}${it.catalogue.sep != null ? `, ${it.catalogue.sep.toFixed(1)}″ away` : ''} (${it.catalogue.source})` : 'Catalogue: no match recorded'}
                </span>
              </span>
              <span class="cb-side">
                {it.ref && (
                  <a class="btn sm" href={it.ref}>
                    Open in the lab
                  </a>
                )}
                {!inv.submitted && (
                  <button class="btn sm" disabled={busy} onClick={() => confirm(`Take “${it.label || 'this object'}” out of the investigation?`) && void run(api(`/api/me/investigations/${inv.id}/items/${it.id}`, { method: 'DELETE' }))}>
                    Remove
                  </button>
                )}
              </span>
            </div>
          );
        })}
        {loose.map((m) => (
          <div key={m.id} class="cb-row haspic">
            <Pic ra={m.ra} dec={m.dec} label={m.label || 'mark'} />
            <span class="cb-main">
              <b>{m.label || 'Mark'}</b>
              <span class="mono-s">
                {formatRA(m.ra, true)} {formatDec(m.dec, true)}
                {m.pass != null ? ` · pass ${m.pass + 1}` : ''}
              </span>
            </span>
            <span class="cb-side">
              <span class={`badge st-${m.level}`}>{m.level}</span>
            </span>
          </div>
        ))}
      </div>

      <h4>Checks</h4>
      <div class="cb-checks" data-testid="checks">
        {checks?.checks.map((c) => (
          <span key={c.id} class={`badge st-${c.state}`} title={c.detail ?? ''}>
            {words(c.id)}: {words(c.state)}
          </span>
        ))}
        {!inv.checks_job && <span class="mono-s">Not checked yet</span>}
      </div>
      {checks?.checks
        .filter((c) => c.detail && c.state !== 'pass')
        .map((c) => (
          <div key={c.id} class="cb-note">
            {words(c.id)}: {c.detail}
          </div>
        ))}
      {!inv.submitted && (
        <div class="cb-submit" data-state={inv.state}>
          {(inv.state === 'open' || inv.state === 'blocked') && (
            <>
              {inv.state === 'open' && (
                <input
                  class="inp cb-declnote"
                  placeholder="Comment for the reviewers (optional)"
                  aria-label="Comment for the reviewers"
                  maxLength={4000}
                  value={declNote}
                  onInput={(e) => setDeclNote((e.target as HTMLInputElement).value)}
                  data-testid="declare-note"
                />
              )}
              <button class="btn primary" disabled={busy} onClick={startChecks} data-testid="check-submit" title="Every check runs first (JPL takes minutes, the NEOWISE archive up to twenty). You may leave: the finding is submitted by itself when all have answered and none failed">
                {inv.state === 'blocked' ? 'Check again' : 'Check and submit'}
              </button>
            </>
          )}
          <span class={`cb-status ${status || checksNote || inv.state === 'blocked' ? 'bad' : ''}`} role="status" data-testid="checks-status">
            {status ||
              checksNote ||
              (inv.state === 'checking'
                ? `Submit declared · ${progress || 'checks running'} · you may leave, it is submitted by itself`
                : inv.state === 'needs_comment'
                  ? `Every check has answered · ${need === 1 ? 'one comment' : `${need} comments`} needed for the warnings (${warns.map((c) => words(c.id)).join(', ')}) · then it is submitted by itself`
                  : inv.state === 'blocked'
                    ? `Not submitted · ${failed ? `${words(failed.id)}: ${words(failed.state)}` : 'a check blocks the submit'}`
                    : '')}
          </span>
        </div>
      )}

      <h4>Notes</h4>
      {inv.notes.length === 0 && <span class="mono-s">No notes yet</span>}
      {inv.notes.map((n, i) => (
        <div key={i} class="cb-note">
          {n.text} <span class="mono-s">{when(n.at)}</span>
        </div>
      ))}
      <form class="cb-addnote" onSubmit={addNote}>
        <input class="inp" placeholder="Add a note" aria-label="Add a note" maxLength={4000} value={note} onInput={(e) => setNote((e.target as HTMLInputElement).value)} />
        <button class="btn sm" type="submit" disabled={busy}>
          Add
        </button>
      </form>

      <h4>Saved frames</h4>
      {inv.frames.length === 0 && <span class="mono-s">No saved frames</span>}
      <div class="cb-frames">
        {inv.frames.map((f, i) => (
          <a key={i} href={f.ref} title="Open this place">
            {f.thumb ? <img src={f.thumb} alt={f.title || 'Saved frame'} loading="lazy" /> : <span class="cb-noimg" />}
            <span>{f.title || 'Frame'}</span>
            {f.note && <span class="mono-s">{f.note}</span>}
          </a>
        ))}
      </div>

      <div class="cb-submit">
        {inv.finding_id && (
          <a class="btn sm" href={`#/findings?lead=${encodeURIComponent(inv.finding_id)}`}>
            Open the finding
          </a>
        )}
        {!inv.submitted && (
          <button
            class="btn sm danger"
            onClick={() => confirm(`Delete the investigation “${inv.title}”?`) && void api(`/api/me/investigations/${inv.id}`, { method: 'DELETE' }).then(() => (location.hash = "#/me"), (e: Error) => setStatus(e.message))}
          >
            Delete
          </button>
        )}
      </div>
    </div>
  );
}
