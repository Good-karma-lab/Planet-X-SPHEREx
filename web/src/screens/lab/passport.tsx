// The Object tab's parts that are not the catalogues' (lab v2): a find's own record in Findings, and how the object moves as our
// passes measured it beside what the catalogue says.
import { useEffect, useState } from 'preact/hooks';

interface FindRecord {
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

/** A find's record in Findings (GET /api/v1/findings?ids=…): its status and stage, its numbers, the verdict and the note. */
export function FindStatus({ id }: { id: string }) {
  const [f, setF] = useState<FindRecord | null | 'failed'>(null);
  useEffect(() => {
    let stop = false;
    setF(null);
    fetch(`/api/v1/findings?ids=${encodeURIComponent(id)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { finds?: FindRecord[] }) => !stop && setF(d.finds?.[0] ?? 'failed'))
      .catch(() => !stop && setF('failed'));
    return () => {
      stop = true;
    };
  }, [id]);
  if (f === null) return <div class="obj-find mono-s">In Findings: asking…</div>;
  if (f === 'failed') return <div class="obj-find mono-s">In Findings: no answer</div>;
  const nums = [
    f.snr != null ? `S/N ${f.snr.toFixed(1)}` : '',
    f.score != null ? `score ${f.score.toFixed(1)}` : '',
    f.rate != null && f.rate > 0 ? `${f.rate.toFixed(2)}″/yr` : '',
    f.promise != null ? `promise ${f.promise}` : '',
    f.viewers ? `${f.viewers} looked` : '',
  ].filter(Boolean);
  return (
    <div class="obj-find" data-testid="find-status">
      <div class="obj-findhead">
        <span class={`badge st-${f.status ?? ''}`}>{(f.status ?? '').replace(/_/g, ' ') || f.kind}</span>
        {f.stage && <span class="mono-s">stage {f.stage.replace(/_/g, ' ')}</span>}
        <a class="cb-link" href={`#/findings?lead=${encodeURIComponent(f.id)}`}>
          In Findings
        </a>
      </div>
      {nums.length > 0 && <div class="mono-s">{nums.join(' · ')}</div>}
      {f.verdict && <div class="obj-findtext">{f.verdict}</div>}
      {f.note && <div class="obj-findtext faint">{f.note}</div>}
    </div>
  );
}
