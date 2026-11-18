// The Object tab's parts that are not the catalogues' (lab v2): a find's own record in Findings, and how the object moves as our
// passes measured it beside what the catalogue says.
import { useEffect, useState } from 'preact/hooks';
import { useTalkCount } from '../findings/talk';

export interface FindRecord {
  id: string;
  kind: string;
  status?: string;
  stage?: string;
  score?: number | null;
  snr?: number | null;
  rate?: number | null;
  promise?: number | null;
  verdict?: string;
  note?: string;
  viewers?: number;
}

/** A find's record in Findings (GET /api/v1/findings?ids=…): null while asking, 'failed', or the record ('' asks nothing). */
export function useFindRecord(id: string): FindRecord | null | 'failed' {
  const [f, setF] = useState<FindRecord | null | 'failed'>(null);
  useEffect(() => {
    let stop = false;
    setF(null);
    if (!id) return;
    fetch(`/api/v1/findings?ids=${encodeURIComponent(id)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { finds?: FindRecord[] }) => !stop && setF(d.finds?.[0] ?? 'failed'))
      .catch(() => !stop && setF('failed'));
    return () => {
      stop = true;
    };
  }, [id]);
  return f;
}

/** a find's rate as Findings says it: a track within one pass in arcseconds per hour, every other mover per year */
export const rateWord = (f: { kind: string; rate?: number | null }) =>
  f.rate != null && f.rate > 0 ? `${f.rate.toFixed(f.rate >= 10 ? 1 : 2)}″/${f.kind === 'film' ? 'h' : 'yr'}` : '';

/** A find's record in Findings: its status and stage, its numbers, the verdict and the note. */
export function FindStatus({ id }: { id: string }) {
  const f = useFindRecord(id);
  if (f === null) return <div class="obj-find mono-s">In Findings: asking…</div>;
  if (f === 'failed') return <div class="obj-find mono-s">In Findings: no answer</div>;
  // each number with its meaning on hover (the words stay short on the card)
  const nums: [string, string][] = (
    [
      [f.snr != null ? `S/N ${f.snr.toFixed(1)}` : '', 'Signal to noise: how far it stands above the noise of the picture'],
      [f.score != null ? `score ${f.score.toFixed(1)}` : '', "The search's own measure of how strong the find is"],
      [rateWord(f), 'How fast it moves on the sky'],
      [f.promise != null ? `promise ${f.promise}` : '', 'What to look at first, 0 to 100: strength, checks passed, nothing known there'],
      [f.viewers ? `${f.viewers} looked` : '', 'People who looked at it'],
    ] as [string, string][]
  ).filter(([t]) => t);
  return (
    <div class="obj-find" data-testid="find-status">
      <div class="obj-findhead">
        <span class={`badge st-${f.status ?? ''}`}>{(f.status ?? '').replace(/_/g, ' ') || f.kind}</span>
        {f.stage && (
          <span class="mono-s hov" title="Where the find is on its way: new → people looked → checks → submitted">
            stage {f.stage.replace(/_/g, ' ')}
          </span>
        )}
        <a class="cb-link" href={`#/findings?lead=${encodeURIComponent(f.id)}`}>
          In Findings
        </a>
        <TalkLink id={f.id} />
      </div>
      {nums.length > 0 && (
        <div class="mono-s">
          {nums.map(([t, why], k) => (
            <span key={t} title={why} class="hov">
              {k ? ' · ' : ''}
              {t}
            </span>
          ))}
        </div>
      )}
      {f.verdict && <div class="obj-findtext">{f.verdict}</div>}
      {f.note && <div class="obj-findtext faint">{f.note}</div>}
    </div>
  );
}

/** the find's Talk in Findings, with how many comments it has */
function TalkLink({ id }: { id: string }) {
  const n = useTalkCount(id);
  return (
    <a class="cb-link" href={`#/findings?lead=${encodeURIComponent(id)}`} title="Comments and links under this finding" data-testid="talk-link">
      Talk{n ? ` · ${n}` : ''}
    </a>
  );
}
