import { useEffect, useState } from 'preact/hooks';
import { peopleLine, usePeople } from './people';
import { formatDec, formatRA } from '../../lib/sky';
import { checkHref, KIND_ONE, STAGE_LABEL, takeTask, type CardFind } from './cards';
import { titleOf, type Find } from './finds';
import './findings.css';

/** A find with the fields of the product flow (`GET /api/v1/findings/{id}`). */
type FullFind = Find &
  Pick<CardFind, 'stage' | 'promise' | 'viewers' | 'clip'> & {
    credit: CardFind['credit'] & { people?: { name: string; role: string; result?: string | null; at?: string }[] };
  };

const STAGE_BADGE: Record<string, string> = {
  new: 'orange',
  in_review: 'blue',
  submitted: 'purple',
  known: 'green',
  artefact: '',
  candidate: 'yellow',
  confirmed: 'green',
};
const RESULT: Record<string, string> = {
  moves: 'it moves',
  artefact: 'artefact',
  known: 'known object',
  nothing: 'nothing here',
  marks: 'marks left',
};
const ROLE: Record<string, string> = { checked: 'checked', marked: 'marked first', submitted: 'submitted' };

/**
 * The permanent page of one finding (`#/findings/<id>`): its time-lapse large, where it stands in the flow, who found and checked it, what the
 * catalogues said, the verdicts people gave, and the button that opens the task in the lab. `back` is where "All findings" leads.
 */
export function FindingPage({ id, back = '#/findings' }: { id: string; back?: string }) {
  const [f, setF] = useState<FullFind | null>(null);
  const [state, setState] = useState<'loading' | 'ok' | 'none' | 'failed'>('loading');
  const [clip, setClip] = useState<'wait' | 'ready' | 'none'>('wait');
  const [why, setWhy] = useState('');
  const peopleSay = peopleLine(usePeople([id])[id]);
  useEffect(() => {
    let stop = false;
    setState('loading');
    setF(null);
    void fetch(`/api/v1/findings/${encodeURIComponent(id)}`)
      .then(async (r) => {
        if (stop) return;
        if (r.status === 404) return setState('none');
        if (!r.ok) return setState('failed');
        setF((await r.json()) as FullFind);
        setState('ok');
      })
      .catch(() => !stop && setState('failed'));
    return () => {
      stop = true;
    };
  }, [id]);
  // the clip: ready at once, or asked for until its field is built
  useEffect(() => {
    if (!f?.clip) return setClip('none');
    if (f.clip.state === 'ready') return setClip('ready');
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setClip('wait');
    const ask = async () => {
      try {
        const r = await fetch(f.clip!.url);
        if (stop) return;
        if (r.ok) return setClip('ready');
        if (r.status === 404) {
          const b = (await r.json().catch(() => ({}))) as { state?: string; reason?: string };
          if (b.state === 'none') {
            setWhy(b.reason ?? '');
            return setClip('none');
          }
        }
      } catch {
        // asked again below
      }
      if (!stop) timer = setTimeout(ask, 6000);
    };
    void ask();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [f?.id, f?.clip?.state]);

  if (state === 'loading') return <div class="mono-s">reading the finding…</div>;
  if (state !== 'ok' || !f)
    return (
      <div class="fpage">
        <p class="mono-s">{state === 'none' ? 'no such finding' : 'the finding did not load'}</p>
        <a class="btn sm" href={back}>
          All findings
        </a>
      </div>
    );
  const card = {
    ...(f as unknown as CardFind),
    name: f.identity?.name ?? null,
    field: (f.data.field_params as CardFind['field']) ?? null,
    group: (f.data.group as string) ?? null,
    passes: null,
  };
  const people = f.credit.people ?? [];
  const looks = Object.entries(f.credit.looks ?? {});
  const nums: [string, string][] = [
    ...(f.rate != null ? ([['Rate', `${f.rate.toFixed(2)} ${f.kind === 'film' ? '″/h' : '″/yr'}`]] as [string, string][]) : []),
    ...(f.snr != null ? ([['S/N', f.snr.toFixed(1)]] as [string, string][]) : []),
    ...(f.score != null ? ([['Score', f.score.toFixed(2)]] as [string, string][]) : []),
    ['Place', `${formatRA(f.ra)} ${formatDec(f.dec)}`],
    ['Field', `${f.place} · ${f.fov}′`],
    ['Promise', String(f.promise)],
  ];
  return (
    <article class="fpage">
      <a class="mono-s fpage-back" href={back}>
        ← All findings
      </a>
      <header class="fpage-head">
        <h1>{titleOf(f)}</h1>
        <span class={`badge ${STAGE_BADGE[f.stage] ?? ''}`}>{STAGE_LABEL[f.stage] ?? f.stage}</span>
      </header>
      <div class="mono-s">
        {KIND_ONE[f.kind] ?? f.kind} · {f.place} · {f.id}
      </div>
      {peopleSay && <div class="mono-s fcard-people">{peopleSay}</div>}
      <div class="fpage-grid">
        <div class="fpage-clip glass">
          {clip === 'ready' && f.clip && (
            <img src={f.clip.url} alt={`Time-lapse of ${titleOf(f)}: one frame per SPHEREx pass, the object ringed`} />
          )}
          {clip !== 'ready' && f.clip?.still && (
            <img class="fstill" src={f.clip.still} alt="" onError={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')} />
          )}
          {clip === 'wait' && <span class="mono-s">building the time-lapse…</span>}
          {clip === 'none' && (
            <span class="mono-s">no time-lapse: {why || f.clip?.reason || 'no SPHEREx field covers this place yet'}</span>
          )}
        </div>
        <div class="fpage-side">
          <p class="fverdict">{f.verdict}</p>
          <div class="fnums">
            {nums.map(([k, v]) => (
              <div key={k}>
                <span>{k}</span>
                <b>{v}</b>
              </div>
            ))}
          </div>
          <section class="fpage-sec">
            <h2>Credit</h2>
            <p>
              Found by {f.credit.found_by}
              {f.credit.first_marked_by ? ` · marked first by ${f.credit.first_marked_by}` : ''}
            </p>
            <p class={f.viewers || f.stage !== 'new' ? '' : 'st-un'}>
              {f.viewers
                ? `${f.viewers} ${f.viewers === 1 ? 'look' : 'looks'} in the lab${f.credit.checked_by.length ? ` · checked by ${f.credit.checked_by.join(', ')}` : ''}`
                : f.stage === 'new'
                  ? 'waiting for a look'
                  : 'no looks yet'}
            </p>
          </section>
          <div class="chips">
            <a
              class="btn primary"
              href={checkHref(card)}
              onClick={(e) => {
                e.preventDefault();
                void takeTask(card, e.currentTarget as HTMLElement);
              }}
            >
              Check this finding
            </a>
          </div>
        </div>
      </div>
      <section class="fpage-sec">
        <h2>Verdicts</h2>
        {people.length === 0 && looks.length === 0 ? (
          <p class="mono-s">nobody has given a verdict yet</p>
        ) : (
          <table class="ftab">
            <tbody>
              {people.map((p, i) => (
                <tr key={i}>
                  <td>{p.at ? p.at.slice(0, 10) : ''}</td>
                  <td>{p.name}</td>
                  <td>{ROLE[p.role] ?? p.role}</td>
                  <td>{p.result ? (RESULT[p.result] ?? p.result) : ''}</td>
                </tr>
              ))}
              {people.length === 0 &&
                looks.map(([k, n]) => (
                  <tr key={k}>
                    <td />
                    <td>
                      {n} {n === 1 ? 'look' : 'looks'}
                    </td>
                    <td>checked</td>
                    <td>{RESULT[k] ?? k}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </section>
      <section class="fpage-sec">
        <h2>Checks against the catalogues</h2>
        <table class="ftab cat">
          <tbody>
            {f.idents.map((i) => (
              <tr key={i.catalogue} class={`c-${i.state}`}>
                <td>{i.label}</td>
                <td>
                  {i.state === 'ok'
                    ? `${i.name ?? '—'}${i.type ? ` · ${i.type}` : ''}`
                    : i.state === 'none'
                      ? 'nothing there'
                      : i.state === 'unavailable'
                        ? 'did not answer'
                        : 'not asked yet'}
                </td>
                <td>{i.state === 'ok' && i.sep != null ? `${i.sep.toFixed(1)}″` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {f.positions.some((p) => p.ra != null) && (
        <section class="fpage-sec">
          <h2>Positions per pass</h2>
          <table class="ftab">
            <tbody>
              {f.positions.map((p, i) => (
                <tr key={i}>
                  <td>{p.label}</td>
                  <td>{p.mjd == null ? '' : new Date((p.mjd - 40587) * 864e5).toISOString().slice(0, 10)}</td>
                  <td>{p.ra != null && p.dec != null ? `${p.ra.toFixed(5)} ${p.dec.toFixed(5)}` : ''}</td>
                  <td>{p.snr != null ? `S/N ${p.snr.toFixed(0)}` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </article>
  );
}
