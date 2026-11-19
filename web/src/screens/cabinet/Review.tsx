// The review page (#/me?view=review): a reviewer gives the verdict on the findings people submitted. Only an account granted on the
// server sees it (GET /api/review answers 403 to anyone else); every verdict is logged with who gave it and why, and everyone
// credited on the finding is told (POST /api/review/{id}; its history GET /api/findings/{id}/verdicts).
//
// One place to work in: the queue on the left (oldest first, each with its checks in a glance), the one chosen on the right (its
// time-lapse, its eight checks with the service's words, who is credited, what was said before), and one verdict: choose, see what
// it does, say why, give it.

import { useCallback, useEffect, useState } from 'preact/hooks';
import { api } from '../../lib/account';
import { formatDec, formatRA } from '../../lib/sky';
import { CHECK_GLYPH, CHECK_WORD } from '../lab/markchecks';

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
  updated?: string;
  by: string | null;
  checks: Check[];
  credit: { name: string; role: string; result?: string; at?: string }[];
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

const STATUSES = ['in_review', 'known', 'artefact', 'candidate', 'confirmed'] as const;
const SAY: Record<string, string> = {
  submitted: 'Submitted',
  in_review: 'In review',
  known: 'Known',
  artefact: 'Artefact',
  candidate: 'Candidate',
  confirmed: 'Confirmed',
};
/** what a verdict does (service/accounts/verdict.py), said where it is chosen */
const DOES: Record<string, string> = {
  in_review: 'stays open · everyone credited is told it is being looked at',
  known: 'closed as known · everyone credited is told the name you give',
  artefact: 'closed as an artefact · everyone credited is told why',
  candidate: '+500 points to the submitter · everyone credited is told',
  confirmed: '+5000 points to the submitter (and the 500) · its story opens · everyone credited is told',
};
const NOTE_NEEDED = new Set(['known', 'artefact']);
const PLACEHOLDER: Record<string, string> = {
  in_review: 'What it waits for: another survey, an MPC answer…',
  known: 'Its name in the catalogue, and which catalogue',
  artefact: 'What it is: a ghost, a streak, a residual, a hot pixel…',
  candidate: 'What holds: seen in every pass, no catalogue, not an artefact',
  confirmed: 'Who saw it again, or the MPC designation',
};

const ago = (iso: string) => {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(s)) return '';
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
};
const tally = (cs: Check[]) => {
  const n = (st: string) => cs.filter((c) => c.state === st).length;
  return { pass: n('pass'), warn: n('warn'), fail: n('fail'), other: cs.length - n('pass') - n('warn') - n('fail') };
};

/** the find's time-lapse (its clip), from its record in Findings */
function Clip({ id }: { id: string }) {
  const [url, setUrl] = useState<string | null | 'none'>(null);
  useEffect(() => {
    let stop = false;
    setUrl(null);
    fetch(`/api/v1/findings?ids=${encodeURIComponent(id)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { finds?: { clip?: { url?: string | null } | null }[] } | null) => !stop && setUrl(d?.finds?.[0]?.clip?.url ?? 'none'))
      .catch(() => !stop && setUrl('none'));
    return () => {
      stop = true;
    };
  }, [id]);
  if (url === null) return <div class="rv-clip mono-s">reading its time-lapse…</div>;
  if (url === 'none') return <div class="rv-clip mono-s">no time-lapse made yet · open it in the lab</div>;
  return <img class="rv-clip" src={url} alt="The finding's time-lapse: one frame per pass, the object ringed" />;
}

/** what was said about it before, oldest first (public) */
function History({ id, touch }: { id: string; touch: number }) {
  const [v, setV] = useState<Logged[] | null>(null);
  useEffect(() => {
    let stop = false;
    api<{ verdicts: Logged[] }>(`/api/findings/${encodeURIComponent(id)}/verdicts`)
      .then((d) => !stop && setV(d.verdicts))
      .catch(() => !stop && setV([]));
    return () => {
      stop = true;
    };
  }, [id, touch]);
  if (!v?.length) return null;
  return (
    <ol class="rv-history mono-s" data-testid="review-history">
      {v.map((x) => (
        <li key={x.at}>
          <b>{SAY[x.status] ?? x.status}</b> · {x.by} · {ago(x.at)}
          {x.note ? ` · ${x.note}` : ''}
        </li>
      ))}
    </ol>
  );
}

function Detail({ w, done }: { w: Waiting; done: (status: string, said: string) => void }) {
  const [status, setStatus] = useState<string>('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [line, setLine] = useState('');
  const [touch, setTouch] = useState(0);
  useEffect(() => {
    setStatus('');
    setNote('');
    setLine('');
  }, [w.finding_id]);
  const needNote = NOTE_NEEDED.has(status) && !note.trim();
  const give = () => {
    if (!status || needNote || busy) return;
    if (status === 'confirmed' && !confirm(`Confirm "${w.title || w.finding_id}" as a new object? Everyone credited is told.`)) return;
    setBusy(true);
    setLine('giving the verdict…');
    api<{ told?: number }>(`/api/review/${encodeURIComponent(w.finding_id)}`, { method: 'POST', body: { status, note: note.trim() } }).then(
      (r) => {
        setBusy(false);
        const said = `${SAY[status]} · ${typeof r.told === 'number' ? `${r.told} told` : 'given'}`;
        setLine(said);
        setTouch((x) => x + 1);
        done(status, `${w.title || w.finding_id}: ${said}`);
      },
      (e: Error) => {
        setBusy(false);
        setLine(e.message);
      },
    );
  };
  const lab = w.ra != null && w.dec != null ? `#/lab?ra=${w.ra.toFixed(5)}&dec=${w.dec.toFixed(5)}&fov=5&find=${encodeURIComponent(w.finding_id)}` : null;
  const people = [...new Map(w.credit.map((c) => [c.name, c])).values()];
  return (
    <section class="rv-detail" data-testid="review-detail">
      <div class="rv-dhead">
        <b>{w.title || w.finding_id}</b>
        <span class={`badge st-${w.status}`}>{SAY[w.status] ?? w.status}</span>
      </div>
      <div class="rv-meta mono-s">
        {w.ra != null && w.dec != null && (
          <span>
            {formatRA(w.ra)} {formatDec(w.dec)}
          </span>
        )}
        <span>
          submitted {ago(w.submitted)}
          {w.by ? ` by ${w.by}` : ''}
        </span>
        {lab && <a href={lab}>Open in the lab ›</a>}
        <a href={`#/findings?lead=${encodeURIComponent(w.finding_id)}`}>In Findings ›</a>
      </div>
      <Clip id={w.finding_id} />
      <div class="rv-sub">Checks</div>
      <ul class="rv-checks" data-testid="review-checks">
        {w.checks.map((c) => (
          <li key={c.id} class={`ck ck-${c.state}`}>
            <b>
              {CHECK_GLYPH[c.state] ?? '…'} {CHECK_WORD[c.id] ?? c.id}
            </b>
            <span>{c.detail || c.state}</span>
          </li>
        ))}
      </ul>
      {people.length > 0 && (
        <>
          <div class="rv-sub">Credited</div>
          <div class="rv-people mono-s">
            {people.map((c) => (
              <span key={c.name} title={c.result ? `answered: ${c.result}` : ''}>
                {c.name} · {c.role.replace(/_/g, ' ')}
              </span>
            ))}
          </div>
        </>
      )}
      <History id={w.finding_id} touch={touch} />
      <div class="rv-sub">Verdict</div>
      <div class="rv-choose" role="radiogroup" aria-label="Verdict" data-testid="review-choose">
        {STATUSES.map((s) => (
          <button
            key={s}
            role="radio"
            aria-checked={status === s}
            class={`rv-opt o-${s}${status === s ? ' on' : ''}`}
            onClick={() => setStatus(s)}
            data-testid={`review-${s}`}
          >
            {SAY[s]}
          </button>
        ))}
      </div>
      <div class="rv-does mono-s">{status ? DOES[status] : 'choose one'}</div>
      <textarea
        class="inp rv-note"
        maxLength={500}
        rows={3}
        placeholder={status ? PLACEHOLDER[status] : 'Why: the people credited read it'}
        value={note}
        onInput={(e) => setNote((e.target as HTMLTextAreaElement).value)}
        aria-label="Why"
        data-testid="review-note"
      />
      <div class="rv-foot">
        <span class={`mono-s${needNote ? ' bad' : ''}`}>
          {line || (needNote ? `${SAY[status]} needs a note` : `${note.length} / 500${NOTE_NEEDED.has(status) ? ' · a note is needed' : ''}`)}
        </span>
        <button class="btn sm primary" disabled={!status || needNote || busy} onClick={give} data-testid="review-give">
          {busy ? 'Giving…' : status ? `Give: ${SAY[status]}` : 'Give the verdict'}
        </button>
      </div>
    </section>
  );
}

export function ReviewPanel() {
  const [q, setQ] = useState<Queue | null>(null);
  const [err, setErr] = useState('');
  const [sel, setSel] = useState<string | null>(null);
  // the last verdict given here, said on the page's top line (the next finding opens at once)
  const [given, setGiven] = useState('');
  const load = useCallback(() => {
    api<Queue>('/api/review').then(
      (d) => {
        setQ(d);
        setErr('');
      },
      (e: Error) => setErr(e.message),
    );
  }, []);
  useEffect(load, [load]);
  if (err) return <div class="cb-status bad">{err}</div>;
  if (!q) return <div class="mono-s">reading the queue…</div>;
  const cur = q.waiting.find((w) => w.finding_id === sel) ?? q.waiting[0] ?? null;
  return (
    <div class="rv" data-testid="panel-review">
      <div class="rv-top">
        <h2>Review</h2>
        <span class="mono-s">
          {q.waiting.length} waiting · oldest first · {q.log.length ? `last verdict ${ago(q.log[0]!.at)}` : 'no verdict yet'}
        </span>
        {given && (
          <span class="rv-given mono-s" data-testid="review-given">
            ✓ {given}
          </span>
        )}
      </div>
      <div class="rv-work">
        <ol class="rv-queue" data-testid="review-queue">
          {q.waiting.length === 0 && <li class="rv-empty mono-s">nothing waits for a verdict: a finding comes here once people have checked it and it was submitted</li>}
          {q.waiting.map((w) => {
            const t = tally(w.checks);
            return (
              <li key={w.finding_id}>
                <button class={`rv-row${cur?.finding_id === w.finding_id ? ' on' : ''}`} onClick={() => setSel(w.finding_id)} data-testid="review-item">
                  <b>{w.title || w.finding_id}</b>
                  <span class="mono-s">
                    {SAY[w.status] ?? w.status} · {ago(w.submitted)}
                    {w.by ? ` · ${w.by}` : ''}
                  </span>
                  <span class="rv-tally mono-s" title="the checks: passed · warned · failed · not answered">
                    <i class="ck-pass">✓ {t.pass}</i>
                    {t.warn > 0 && <i class="ck-warn">! {t.warn}</i>}
                    {t.fail > 0 && <i class="ck-fail">✗ {t.fail}</i>}
                    {t.other > 0 && <i class="ck-running">… {t.other}</i>}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
        {cur && (
          <Detail
            key={cur.finding_id}
            w={cur}
            done={(status, said) => {
              setGiven(said);
              // in review stays in the queue; a closing verdict moves on to the next one
              if (status !== 'in_review') setSel(q.waiting.find((x) => x.finding_id !== cur.finding_id)?.finding_id ?? null);
              load();
            }}
          />
        )}
      </div>
      {q.log.length > 0 && (
        <>
          <div class="rv-sub">Latest verdicts</div>
          <ol class="rv-log mono-s" data-testid="review-log">
            {q.log.map((l) => (
              <li key={`${l.finding_id}${l.at}`}>
                <span>{ago(l.at)}</span>
                <a href={`#/findings?lead=${encodeURIComponent(l.finding_id)}`}>{l.finding_id}</a>
                <b>{SAY[l.status] ?? l.status}</b>
                <span>{l.by}</span>
                {l.note && <span class="rv-lnote">{l.note}</span>}
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
