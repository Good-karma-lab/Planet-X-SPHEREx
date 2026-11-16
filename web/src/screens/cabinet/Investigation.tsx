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
  const { checks, note: checksNote } = useChecks(inv.checks_job);
  const own = marks.filter((m) => inv.mark_ids.includes(m.id));
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
    const first = own[0];
    if (!first) return setStatus('No marks in this investigation: put a mark in the lab first');
    const key = /^[A-Za-z0-9_-]*$/.test(first.field_key) ? first.field_key : '';
    const body = { field_key: key, ra: first.ra, dec: first.dec, marks: own.map((m) => ({ pass: m.pass ?? 0, ra: m.ra, dec: m.dec })) };
    void run(api<{ job: string }>('/api/checks', { method: 'POST', body }).then((r) => api(`/api/me/investigations/${inv.id}`, { method: 'PATCH', body: { checks_job: r.job } })));
  };
  const submit = () => void run(api('/api/findings/submit', { method: 'POST', body: { investigation_id: inv.id, checks_job: inv.checks_job } }));
  const running = !!inv.checks_job && (!checks || checks.state !== 'done');
  const warn = checks?.checks.some((c) => c.state === 'warn') ?? false;
  const stage = inv.submitted ? (finding?.status ?? 'submitted') : null;
  return (
    <div class="cb-page" data-testid="investigation-page">
      <a class="btn sm cb-back" href="#/me?tab=investigations">
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

      <h4>Marks</h4>
      {own.length === 0 && <span class="mono-s">No marks</span>}
      <div class="cb-list">
        {own.map((m) => (
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
        <div class="cb-submit">
          {!inv.checks_job || (checks?.state === 'done' && !checks.can_submit) ? (
            <button class="btn primary" disabled={busy} onClick={startChecks} data-testid="check-submit">
              {inv.checks_job ? 'Check again' : 'Check and submit'}
            </button>
          ) : (
            <button class="btn primary" disabled={busy || running || !checks?.can_submit || (warn && inv.notes.length === 0)} onClick={submit} data-testid="check-submit">
              Submit the finding
            </button>
          )}
          <span class={`cb-status ${status || checksNote ? 'bad' : ''}`} role="status" data-testid="checks-status">
            {status ||
              checksNote ||
              (running
                ? `Checks running: ${checks ? checks.checks.filter((c) => !['queued', 'running'].includes(c.state)).length : 0} of ${checks?.checks.length ?? 8} answered. The NEOWISE check is slow; the result waits here.`
                : checks?.can_submit
                  ? warn && inv.notes.length === 0
                    ? 'A check gave a warning: add a note that explains it'
                    : 'Every check has answered: ready to submit'
                  : checks
                    ? 'A check blocks the submit'
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
            onClick={() => confirm(`Delete the investigation “${inv.title}”?`) && void api(`/api/me/investigations/${inv.id}`, { method: 'DELETE' }).then(() => (location.hash = '#/me?tab=investigations'), (e: Error) => setStatus(e.message))}
          >
            Delete
          </button>
        )}
      </div>
    </div>
  );
}
