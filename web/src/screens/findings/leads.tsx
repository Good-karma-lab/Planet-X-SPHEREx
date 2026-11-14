import { useEffect, useState } from 'preact/hooks';
import { formatDec, formatRA } from '../../lib/sky';
import { FIELD, type Live } from './live';
import { FieldThumb } from './thumb';

/** One lead: a track no catalogue explains, an odd spectrum, or a mover that is a known star (listed last, as a recovery). */
export interface Lead {
  id: string;
  kind: 'track' | 'anomaly';
  /** 0 = unexplained track, 1 = anomaly, 2 = explained track (a recovery, not a lead) */
  tier: 0 | 1 | 2;
  score: number;
  title: string;
  sub: string;
  field: string;
  fieldKey: string | null;
  passes: number;
  ra: number;
  dec: number;
  rows: [string, string][];
}

const ut = (m: number) => new Date((m - 40587) * 864e5).toISOString().slice(0, 10);

/** Every lead of the searched fields, ranked: unexplained tracks first (by signal), then spectral anomalies (by score over the field's bar), then known movers recovered. */
export function rankLeads(live: Live[]): Lead[] {
  const out: Lead[] = [];
  for (const l of live) {
    const mv = l.movers?.result;
    const an = l.anomalies?.result;
    mv?.tracks.forEach((t, i) => {
      const explained = t.check === 'same';
      const ra = t.ra_2025_9 ?? l.ra;
      const dec = t.dec_2025_9 ?? l.dec;
      const sn = (t.snr ?? []).filter((v): v is number => v != null);
      const snr = sn.length ? Math.min(...sn) : 0;
      out.push({
        id: `t:${l.name}:${i}`,
        kind: 'track',
        tier: explained ? 2 : 0,
        score: snr,
        title: explained ? `${t.speed.toFixed(2)}″/yr mover: a known star` : `${t.speed.toFixed(2)}″/yr mover no catalogue explains`,
        sub: `${l.name} field · ${t.passes.length} passes`,
        field: l.name,
        fieldKey: mv.field ?? null,
        passes: mv.passes,
        ra,
        dec,
        rows: [
          ['Rate', `${t.speed.toFixed(2)} ″/yr toward ${t.pa.toFixed(0)}° (east ${t.v_east?.toFixed(2) ?? '?'}, north ${t.v_north?.toFixed(2) ?? '?'})`],
          ['Seen in', `${t.passes.length} passes${t.mjd ? `: ${t.mjd.map(ut).join(', ')}` : ''}`],
          ['Straight-line fit', t.rms_arcsec != null ? `rms ${t.rms_arcsec}″ about a straight line` : 'n/a'],
          ['Signal', t.snr?.some((v) => v != null) ? `S/N ${t.snr.map((v) => (v == null ? '–' : v.toFixed(0))).join(' · ')} in the passes` : 'n/a'],
          [
            'Gaia check',
            t.check === 'same' && t.gaia
              ? `Gaia DR3 ${t.gaia.source_id} (G ${t.gaia.g ?? '?'}) moves ${t.gaia.pm} ″/yr toward ${t.gaia.pa}°: the same motion, ${t.gaia.offset_arcsec}″ from the track`
              : t.check === 'none'
                ? 'no Gaia star with this motion near the track'
                : 'not checked yet',
          ],
          ['Chance', mv.chance ? `${mv.chance.mean.toFixed(2)} tracks per field from chance alone (${mv.chance.trials} trials)` : 'n/a'],
          ['Place on 2025.9', `${formatRA(ra)} ${formatDec(dec)}`],
        ],
      });
    });
    an?.anomalies.forEach((a) => {
      const sb = a.identified?.simbad;
      const ga = a.identified?.gaia;
      const name = sb?.state === 'ok' && sb.name ? sb.name : ga?.state === 'ok' ? `Gaia DR3 ${ga.source_id}` : 'not in SIMBAD or Gaia';
      out.push({
        id: `a:${l.name}:${a.n}`,
        kind: 'anomaly',
        tier: 1,
        score: an.threshold ? a.score / an.threshold : a.score,
        title: `Odd spectrum: ${name}`,
        sub: `${l.name} field · score ${a.score.toFixed(1)}`,
        field: l.name,
        fieldKey: l.movers?.result?.field ?? null,
        passes: l.movers?.result?.passes ?? 0,
        ra: a.ra ?? l.ra,
        dec: a.dec ?? l.dec,
        rows: [
          ['Score', `${a.score.toFixed(2)} against a bar of ${an.threshold?.toFixed(2) ?? '?'} (the field's median + 6 robust σ)`],
          ['Drives it', a.drivers.map((d) => `${d.z > 0 ? 'excess' : 'deficit'} at ${d.lo.toFixed(2)}–${d.hi.toFixed(2)} µm (${d.z > 0 ? '+' : '−'}${Math.abs(d.z).toFixed(0)}σ)`).join('; ') || 'n/a'],
          ['Catalogues', `${name}${sb?.type ? ` · ${sb.type}` : ''}`],
          ['Moves', a.moves ? 'yes, between the passes' : 'no, stationary'],
          ['Measured in', a.points != null ? `${a.points} exposures${a.snr != null ? ` · S/N ${a.snr} in the white stack` : ''}` : 'n/a'],
          ['Place', `${formatRA(a.ra ?? l.ra)} ${formatDec(a.dec ?? l.dec)}`],
        ],
      });
    });
  }
  return out.sort((a, b) => a.tier - b.tier || b.score - a.score);
}

const labHref = (l: Lead) => `#/lab?ra=${l.ra.toFixed(5)}&dec=${l.dec.toFixed(5)}&fov=${FIELD}`;

/** The lead id in the address (#/findings?lead=...), kept in step with the address bar. */
export function useLeadParam(): [string | null, (id: string | null) => void] {
  const read = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('lead');
  const [id, setId] = useState<string | null>(read());
  useEffect(() => {
    const f = () => setId(read());
    window.addEventListener('hashchange', f);
    return () => window.removeEventListener('hashchange', f);
  }, []);
  return [id, (v) => (location.hash = v ? `#/findings?lead=${encodeURIComponent(v)}` : '#/findings')];
}

const TIER = ['Unexplained', 'Odd spectrum', 'Known mover'];

/** The ranked list of leads, one dense row each; a click opens the lead in the side panel. */
export function LeadsList({ leads, onOpen, openId }: { leads: Lead[]; onOpen: (id: string) => void; openId: string | null }) {
  if (!leads.length) return null;
  return (
    <section class="card glass leads">
      <h3>Leads in the test fields, ranked</h3>
      <ol class="leadlist">
        {leads.slice(0, 12).map((l, i) => (
          <li key={l.id}>
            <button class="leadrow" aria-current={l.id === openId ? 'true' : undefined} title={`${l.title} · ${l.sub}`} onClick={() => onOpen(l.id)}>
              <span class="rank">{i + 1}</span>
              <span class="lt">
                <b>{l.title}</b>
                <span class="mono-s">{l.field}</span>
              </span>
              <span class={`badge ${l.tier === 2 ? 'green' : 'orange'}`}>{TIER[l.tier]}</span>
            </button>
          </li>
        ))}
      </ol>
      <p class="hint leadnote">A lead is not a discovery.</p>
    </section>
  );
}

/** The lead slid in from the right: the picture of the field, every number and check, the link into the lab, and the report as JSON. Closes with × or Esc. */
export function LeadPanel({ lead, onClose }: { lead: Lead; onClose: () => void }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [onClose]);
  const json = () => JSON.stringify({ title: lead.title, field: lead.field, ra: lead.ra, dec: lead.dec, kind: lead.kind, rows: Object.fromEntries(lead.rows), note: 'a lead, not a discovery', generated: new Date().toISOString() }, null, 2);
  return (
    <aside class="leadpanel glass" role="dialog" aria-label="Lead report">
      <button class="leadpanel-x" aria-label="Close" onClick={onClose}>
        ×
      </button>
      <FieldThumb fieldKey={lead.fieldKey} passes={lead.passes} size={200} />
      <h3 class="lp-title">{lead.title}</h3>
      <div class="mono-s">
        {lead.sub} · {TIER[lead.tier]} · a lead, not a discovery
      </div>
      <dl class="lp-kv">
        {lead.rows.map(([k, v]) => (
          <div class="kvr" key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <div class="chips">
        <a class="btn sm primary" href={labHref(lead)}>
          Open in the lab
        </a>
        <a class="btn sm" download={`lead-${lead.id.replace(/[^a-z0-9]+/gi, '_')}.json`} href={`data:application/json;charset=utf-8,${encodeURIComponent(json())}`}>
          Download JSON
        </a>
      </div>
    </aside>
  );
}

/** One plain sentence for a field card: what the searches found there, in words a visitor can follow. */
export function plainLine(l: Live): string {
  const mv = l.movers?.result;
  const an = l.anomalies?.result;
  const parts: string[] = [];
  if (!mv) parts.push('This field has not been searched yet.');
  else if (mv.passes < 3) parts.push(`SPHEREx saw this place in ${mv.passes} passes; a mover needs three.`);
  else if (!mv.tracks.length) parts.push(`Nothing moves between the seasons beyond what chance makes (${mv.chance ? `${mv.chance.mean.toFixed(2)} false tracks per field` : 'a control run says how many'}).`);
  else {
    const t = mv.tracks[0]!;
    parts.push(
      t.check === 'same'
        ? `One source drifts ${t.speed.toFixed(1)}″ a year, and Gaia knows a star with that motion: this is the target itself, recovered blind.`
        : `One source drifts ${t.speed.toFixed(1)}″ a year and no Gaia star explains it: a lead to look at, not a discovery.`,
    );
  }
  if (an?.anomalies.length) {
    const a = an.anomalies[0]!;
    const nm = a.identified?.simbad?.name ?? (a.identified?.gaia?.state === 'ok' ? `Gaia DR3 ${a.identified.gaia.source_id}` : 'an uncatalogued source');
    parts.push(`${an.anomalies.length} source${an.anomalies.length === 1 ? ' has a' : 's have'} spectrum${an.anomalies.length === 1 ? '' : 's'} unlike the ordinary stars of the field; the strongest is ${nm}.`);
  }
  return parts.join(' ');
}
