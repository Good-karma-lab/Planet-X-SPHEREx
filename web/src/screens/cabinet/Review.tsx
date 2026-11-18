// The review page (#/me?view=review): a reviewer gives the verdict on the findings people submitted. Only an account granted on the
// server sees it (GET /api/review answers 403 to anyone else); every verdict is logged with who gave it and why, and everyone
// credited on the finding is told.

import { useCallback, useEffect, useState } from 'preact/hooks';
import { api } from '../../lib/account';
import { formatDec, formatRA } from '../../lib/sky';

interface Check {
  id: string;
  state: string;
  detail?: string;
}
interface Waiting {
  finding_id: string;
  title: string;
  status: string;
  ra: number | null;
  dec: number | null;
  field_key: string | null;
  submitted: string;
  by: string | null;
  checks: Check[];
  credit: { name: string; role: string }[];
}
interface Logged {
  finding_id: string;
  status: string;
  by: string;
  note: string;
  at: string;
}
interface Queue {
  waiting: Waiting[];
  log: Logged[];
  statuses: string[];
}

const SAY: Record<string, string> = {
  in_review: 'In review',
  known: 'Known',
  artefact: 'Artefact',
  candidate: 'Candidate',
  confirmed: 'Confirmed',
};
const WHY: Record<string, string> = {
  in_review: 'Looked at; the verdict waits for more (another survey, an MPC answer).',
  known: 'A catalogued object: name it in the note.',
  artefact: 'Not a body: a ghost, a streak, a residual. Say which in the note.',
  candidate: 'Nothing known explains it and it holds in every pass: worth another telescope.',
  confirmed: 'Seen again by another telescope or accepted by the MPC. Everyone credited is told.',
};

function One({ w, done }: { w: Waiting; done: () => void }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const give = (status: string) => {
    if ((status === 'known' || status === 'artefact') && !note.trim()) {
      setErr('Say why in the note: the people credited read it.');
      return;
    }
    if (status === 'confirmed' && !confirm(`Confirm ${w.finding_id} as a new object? Everyone credited is told.`)) return;
    setBusy(status);
    setErr('');
    api(`/api/review/${encodeURIComponent(w.finding_id)}`, { method: 'POST', body: { status, note } }).then(done, (e: Error) => {
      setErr(e.message);
      setBusy('');
    });
  };
  const lab = w.ra != null && w.dec != null ? `#/lab?ra=${w.ra.toFixed(5)}&dec=${w.dec.toFixed(5)}&fov=5` : null;
  const names = [...new Set(w.credit.map((c) => c.name))];
  return (
    <li class="rv-one" data-testid="review-item">
      <div class="rv-head">
        <b>{w.title || w.finding_id}</b>
        <span class="mono-s">
          {w.finding_id} · {SAY[w.status] ?? w.status} · submitted {w.submitted.slice(0, 10)}
          {w.by ? ` by ${w.by}` : ''}
        </span>
      </div>
      <div class="rv-links mono-s">
        {w.ra != null && w.dec != null && (
          <span>
            {formatRA(w.ra)} {formatDec(w.dec)}
          </span>
        )}
        {lab && <a href={lab}>Open in the lab</a>}
        <a href={`#/findings/${encodeURIComponent(w.finding_id)}`}>The finding's page</a>
        {names.length > 0 && <span>credited: {names.join(', ')}</span>}
      </div>
      <ul class="rv-checks">
        {w.checks.map((c) => (
          <li key={c.id} class={`rv-check ${c.state}`}>
            <b>{c.id}</b> {c.state}
            {c.detail ? `: ${c.detail}` : ''}
          </li>
        ))}
      </ul>
      <textarea
        class="rv-note"
        maxLength={500}
        placeholder="Why: the catalogue's name, what the other survey showed, what it is not"
        value={note}
        onInput={(e) => setNote((e.target as HTMLTextAreaElement).value)}
      />
      <div class="rv-actions">
        {['in_review', 'known', 'artefact', 'candidate', 'confirmed'].map((s) => (
          <button
            key={s}
            class={`btn sm ${s === 'candidate' || s === 'confirmed' ? 'primary' : ''}`}
            title={WHY[s]}
            disabled={!!busy}
            onClick={() => give(s)}
          >
            {busy === s ? '…' : SAY[s]}
          </button>
        ))}
      </div>
      {err && <div class="cb-status bad">{err}</div>}
    </li>
  );
}

export function ReviewPanel() {
  const [q, setQ] = useState<Queue | null>(null);
  const [err, setErr] = useState('');
  const load = useCallback(() => {
    api<Queue>('/api/review').then(setQ, (e: Error) => setErr(e.message));
  }, []);
  useEffect(load, [load]);
  if (err) return <div class="cb-status bad">{err}</div>;
  if (!q) return <div class="mono-s">Loading…</div>;
  return (
    <div class="rv" data-testid="panel-review">
      <h2>Review</h2>
      <p class="mono-s">
        The findings people submitted, oldest first. Each passed every check; the stage is yours to give. Hover a button for what it means.
      </p>
      {q.waiting.length === 0 ? <div class="mono-s">Nothing is waiting for a verdict.</div> : null}
      <ul class="rv-list">
        {q.waiting.map((w) => (
          <One key={w.finding_id} w={w} done={load} />
        ))}
      </ul>
      {q.log.length > 0 && (
        <>
          <h3>Latest verdicts</h3>
          <ul class="rv-log mono-s">
            {q.log.map((l) => (
              <li key={`${l.finding_id}${l.at}`}>
                {l.at.slice(0, 16).replace('T', ' ')} · <a href={`#/findings/${encodeURIComponent(l.finding_id)}`}>{l.finding_id}</a> ·{' '}
                {SAY[l.status] ?? l.status} · {l.by}
                {l.note ? `: ${l.note}` : ''}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
