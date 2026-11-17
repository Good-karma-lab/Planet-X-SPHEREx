// While a field is being built or loaded, the picture never plays half-built stacks: it holds one finished pass (dimmed) or stays
// dark, and a progress block in the middle of the picture says what the service is doing, with a bar when it says how far.

export interface LoadStep {
  /** what is being done, short */
  title: string;
  /** how far, in numbers ('' when the service does not say) */
  detail: string;
  /** 0..1, or null when there is no measure */
  frac: number | null;
  failed?: boolean;
}

/** The service's phase line (lib/plain.ts wording) as a progress step. `pictures` / `passes`: finished pass pictures in hand. */
export function loadStep(phase: string, pictures: number, passes: number): LoadStep {
  const s = phase.trim();
  let m = s.match(/^reading the exposures: (\d+) of (\d+), (\d+) MB downloaded/);
  if (m) return { title: 'Loading the exposures', detail: `${m[1]} of ${m[2]} · ${m[3]} MB`, frac: Number(m[1]) / Math.max(1, Number(m[2])) };
  m = s.match(/^pass (\d+) of (\d+): D\d stacked \((\d+) of (\d+) detector stacks\)/);
  if (m) return { title: `Stacking pass ${m[1]} of ${m[2]}`, detail: `${m[3]} of ${m[4]} detector stacks`, frac: Number(m[3]) / Math.max(1, Number(m[4])) };
  m = s.match(/^quick picture of pass (\d+) of (\d+)/);
  if (m) return { title: `Stacking pass ${m[1]} of ${m[2]}`, detail: 'first exposures', frac: null };
  m = s.match(/^pass (\d+): published/);
  if (m) return { title: `Pass ${m[1]} stacked`, detail: passes ? `${pictures} of ${passes} passes ready` : '', frac: passes ? pictures / passes : null };
  m = s.match(/^(\d+) exposures found in (\d+) passes/);
  if (m) return { title: 'Finding the exposures', detail: `${m[1]} exposures in ${m[2]} passes`, frac: null };
  if (/^comparing passes/.test(s)) return { title: 'Comparing the passes', detail: '', frac: null };
  m = s.match(/^Request queued(?: · (\d+) ahead)?/);
  if (m) return { title: 'Request queued', detail: m[1] ? `${m[1]} ahead` : '', frac: null };
  if (/^loading the seasons/.test(s)) return { title: 'Loading the pictures', detail: passes ? `${pictures} of ${passes} passes` : '', frac: passes ? pictures / passes : null };
  if (/^failed: /.test(s)) return { title: 'The field could not be made', detail: s.replace(/^failed: /, ''), frac: null, failed: true };
  if (/restarting/.test(s)) return { title: 'The sky service is restarting', detail: 'trying again', frac: null };
  return { title: s.replace(/…$/, '') || 'Asking the sky service', detail: '', frac: null };
}

/** The block in the middle of the picture. Fixed size: its text changes, the block does not move or grow. */
export function LoadBlock({ step }: { step: LoadStep }) {
  return (
    <div class={`float loadblock${step.failed ? ' failed' : ''}`} role="status" aria-live="polite" data-testid="load-block">
      <b>{step.title}</b>
      <span class="lb-bar" aria-hidden="true">
        <i class={step.frac === null ? 'busy' : ''} style={{ width: `${step.frac === null ? 30 : Math.max(2, Math.min(100, step.frac * 100))}%` }} />
      </span>
      <span class="lb-detail">{step.detail}</span>
    </div>
  );
}
