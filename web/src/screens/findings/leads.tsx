import { useEffect, useState } from 'preact/hooks';
import { formatDec, formatRA } from '../../lib/sky';
import { FIELD, type Live } from './live';
import { formatHash } from '../../lib/route';
import { FieldThumb } from './thumb';

/** One lead: a track no catalogue explains, an odd spectrum, or a mover that is a known star (listed last, as a recovery). */
export interface Lead {
  id: string;
  kind: 'track' | 'anomaly';
  /** 0 = unexplained track, 1 = anomaly, 2 = known object (a recovered star, or the target of its field), 3 = comet */
  tier: 0 | 1 | 2 | 3;
  score: number;
  title: string;
  sub: string;
  field: string;
  fieldKey: string | null;
  passes: number;
  ra: number;
  dec: number;
  rows: [string, string][];
  /** catalogue identity, the small second line of the list row */
  ident?: string;
  /** the target of its field (an odd spectrum that is expected) */
  expected?: boolean;
  /** 3I/ATLAS: its panel shows the SPHEREx frame beside the partner agency's */
  atlas?: boolean;
}

const GREEK: Record<string, string> = { alf: 'α', bet: 'β', gam: 'γ', del: 'δ', eps: 'ε', zet: 'ζ', eta: 'η', tet: 'θ', iot: 'ι', kap: 'κ', lam: 'λ', mu: 'μ', nu: 'ν', ksi: 'ξ', pi: 'π', rho: 'ρ', sig: 'σ', tau: 'τ', phi: 'φ', chi: 'χ', psi: 'ψ', ome: 'ω' };
/** A SIMBAD name read by people: no leading star mark, Greek letters spelled as letters. */
const cleanName = (n: string) => n.replace(/^\*+\s+/, '').replace(/^([a-z]{2,3})(?=\s)/, (m) => GREEK[m] ?? m).replace(/\s+/g, ' ').trim();
const um = (v: number) => v.toFixed(2).replace(/0$/, '');
/** Is a SIMBAD name the field's own target ("ε Ind B" for "ε Indi B")? Token by token, the name's token is the start of the field's. */
const sameName = (simbad: string, target: string) => {
  const a = cleanName(simbad).toLowerCase().split(' ');
  const b = target.toLowerCase().split(' ');
  return a.length === b.length && a.every((t, i) => b[i]!.startsWith(t));
};
const BD = (t?: string) => (t === 'BD*' ? 'a brown dwarf' : t ? `type ${t}` : '');

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
      const ident = sb?.state === 'ok' && sb.name ? cleanName(sb.name) : ga?.state === 'ok' ? `Gaia DR3 ${ga.source_id}` : 'not in SIMBAD or Gaia';
      const top = [...a.drivers].sort((x, y) => Math.abs(y.z) - Math.abs(x.z))[0];
      const label = top ? `${top.z > 0 ? 'Excess' : 'Deficit'} at ${um(top.lo)}–${um(top.hi)} µm` : 'Odd spectrum';
      const expected = sb?.state === 'ok' && !!sb.name && sameName(sb.name, l.name);
      out.push({
        id: `a:${l.name}:${a.n}`,
        kind: 'anomaly',
        tier: expected ? 2 : 1,
        score: an.threshold ? a.score / an.threshold : a.score,
        title: expected ? `${l.name}: the target itself` : label,
        sub: `${l.name} field · score ${a.score.toFixed(1)}`,
        ident: expected ? `${label} · expected` : ident,
        expected,
        field: l.name,
        fieldKey: l.movers?.result?.field ?? null,
        passes: l.movers?.result?.passes ?? 0,
        ra: a.ra ?? l.ra,
        dec: a.dec ?? l.dec,
        rows: [
          ...(expected ? ([['Known', `the target of this field: SIMBAD lists it as ${[BD(sb?.type), sb?.sp].filter(Boolean).join(' ')}, so its odd spectrum is expected, not a find`]] as [string, string][]) : []),
          ['Score', `${a.score.toFixed(2)} against a bar of ${an.threshold?.toFixed(2) ?? '?'} (the field's median + 6 robust σ)`],
          ['Drives it', a.drivers.map((d) => `${d.z > 0 ? 'excess' : 'deficit'} at ${d.lo.toFixed(2)}–${d.hi.toFixed(2)} µm (${d.z > 0 ? '+' : '−'}${Math.abs(d.z).toFixed(0)}σ)`).join('; ') || 'n/a'],
          ['Catalogues', `${ident}${sb?.type ? ` · ${sb.type}` : ''}${ga?.state === 'ok' ? ` · Gaia DR3 ${ga.source_id}` : ''}`],
          ['Moves', a.moves ? 'yes, between the passes' : 'no, stationary'],
          ['Measured in', a.points != null ? `${a.points} exposures${a.snr != null ? ` · S/N ${a.snr} in the white stack` : ''}` : 'n/a'],
          ['Place', `${formatRA(a.ra ?? l.ra)} ${formatDec(a.dec ?? l.dec)}`],
        ],
      });
    });
  }
  return out.sort((a, b) => a.tier - b.tier || b.score - a.score);
}

/** The interstellar comet 3I/ATLAS: SPHEREx imaged it on 14 Dec 2025, and CSA's NEOSSat took it the same day. Listed after the ranked leads. */
export const ATLAS_LEAD: Lead = {
  id: 'c:3I-ATLAS',
  kind: 'track',
  tier: 3,
  score: 0,
  title: '3I/ATLAS · interstellar comet · 14 Dec 2025',
  sub: 'seen by SPHEREx on 14 Dec 2025 and the same day by NEOSSat (CSA)',
  field: '3I/ATLAS',
  fieldKey: null,
  passes: 1,
  ra: 166.8274,
  dec: 5.2787,
  rows: [],
  atlas: true,
};
const ATLAS_FILM = formatHash({ screen: 'lab', params: { ra: '166.82740', dec: '5.27870', fov: '60', film: '61023.6' } });

const labHref = (l: Lead) => l.atlas ? ATLAS_FILM : `#/lab?ra=${l.ra.toFixed(5)}&dec=${l.dec.toFixed(5)}&fov=${FIELD}`;

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

const TIER = ['Unexplained mover', 'Odd spectrum', 'Known object', 'Comet'];
const SHOWN = 6;

/** The ranked list of leads in groups by kind with counts, one dense row each; a click opens the lead in the side panel. `all` is the lead count of every searched field. */
export function LeadsList({ leads, onOpen, openId, all }: { leads: Lead[]; onOpen: (id: string) => void; openId: string | null; all?: number }) {
  const [more, setMore] = useState(false);
  if (!leads.length) return null;
  const ranked = leads.filter((l) => !l.atlas);
  // collapsed: every unexplained mover and known object, the first few odd spectra
  const shown = new Set(leads.filter((l) => more || l.tier !== 1 || leads.filter((m) => m.tier === 1).indexOf(l) < SHOWN).map((l) => l.id));
  let n = 0;
  return (
    <section class="card glass leads">
      <h3>Leads, ranked</h3>
      <p class="hint leadcount">
        {ranked.length} in the five test fields{all != null && all > ranked.length ? ` · ${all} across all searched fields` : ''}
        {leads.filter((l) => l.tier === 1).length > SHOWN && (
          <button class="lnk" onClick={() => setMore(!more)}>
            {more ? 'show fewer' : 'show all'}
          </button>
        )}
      </p>
      {TIER.map((name, t) => {
        const group = leads.filter((l) => l.tier === t);
        if (!group.length) return null;
        const rows = group.filter((l) => shown.has(l.id));
        return (
          <div key={name}>
            <div class="leadgroup">
              {name} <span>{group.length}</span>
            </div>
            <ol class="leadlist">
              {rows.map((l) => (
                <li key={l.id}>
                  <button class="leadrow" aria-current={l.id === openId ? 'true' : undefined} title={`${l.title} · ${l.field}${l.ident ? ` · ${l.ident}` : ''}`} onClick={() => onOpen(l.id)}>
                    <span class="rank">{++n}</span>
                    <span class="lt">
                      <b>
                        {l.title}
                        {!l.expected && l.kind === 'anomaly' ? <span class="lf"> · {l.field}</span> : null}
                      </b>
                      <span class="mono-s">{l.atlas ? l.sub : l.ident ?? `${l.field} field · ${l.sub.split(' · ').slice(1).join(' · ')}`}</span>
                    </span>
                    <span class={`badge ${l.tier >= 2 ? 'green' : 'orange'}`}>{l.atlas ? 'Comet' : l.expected ? 'Expected' : l.tier === 2 ? 'Known' : l.tier === 1 ? 'Odd' : 'Mover'}</span>
                  </button>
                </li>
              ))}
            </ol>
          </div>
        );
      })}
      <p class="hint leadnote">A lead is not a discovery. Known objects are listed so a recovery is not mistaken for a find.</p>
    </section>
  );
}

interface NeosCard {
  date_obs: string;
  exposure_s: number;
  wcs_offset_arcsec: number;
  gaia_matches: number;
  offset_from_prediction_arcsec: number;
  g_mag: number | null;
  snr: number | null;
}

function PanelShell({ lead, rows, media, onClose }: { lead: Lead; rows: [string, string][]; media: preact.ComponentChildren; onClose: () => void }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [onClose]);
  const json = () => JSON.stringify({ title: lead.title, field: lead.field, ra: lead.ra, dec: lead.dec, kind: lead.kind, rows: Object.fromEntries(rows), note: lead.atlas ? 'SPHEREx and NEOSSat observations of 3I/ATLAS' : 'a lead, not a discovery', generated: new Date().toISOString() }, null, 2);
  return (
    <aside class="leadpanel glass" role="dialog" aria-label="Lead report">
      <button class="leadpanel-x" aria-label="Close" onClick={onClose}>
        ×
      </button>
      {media}
      <h3 class="lp-title">{lead.title}</h3>
      <div class="mono-s">{lead.atlas ? lead.sub : `${lead.sub} · ${TIER[lead.tier]}${lead.ident ? ` · ${lead.ident}` : ''}`}</div>
      <dl class="lp-kv">
        {rows.map(([k, v]) => (
          <div class="kvr" key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <div class="chips">
        <a class="btn sm primary" href={labHref(lead)}>
          {lead.atlas ? 'Open the film in the lab' : 'Open in the lab'}
        </a>
        <a class="btn sm" download={`lead-${lead.id.replace(/[^a-z0-9]+/gi, '_')}.json`} href={`data:application/json;charset=utf-8,${encodeURIComponent(json())}`}>
          Download JSON
        </a>
      </div>
    </aside>
  );
}

/** 3I/ATLAS: the SPHEREx picture of the 60′ field and the NEOSSat frame of the same day, side by side, with the measured numbers. */
function AtlasPanel({ lead, onClose }: { lead: Lead; onClose: () => void }) {
  const [c, setC] = useState<NeosCard | null>(null);
  const [fld, setFld] = useState<{ key: string | null; passes: number }>({ key: null, passes: 1 });
  useEffect(() => {
    void fetch('/neossat/3i-2025-12-14.json').then((r) => (r.ok ? r.json() : null)).then(setC).catch(() => {});
    // the pass of the 60′ field that contains 14 Dec 2025 (MJD 61023.6)
    void fetch(`/api/v1/fields?ra=${lead.ra.toFixed(5)}&dec=${lead.dec.toFixed(5)}&fov=60`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { key?: string; meta?: { passes?: { index: number; mjd0: number; mjd1: number }[] } } | null) => {
        const ps = d?.meta?.passes ?? [];
        const hit = ps.find((q) => q.mjd0 <= 61023.6 && q.mjd1 >= 61023.6);
        if (d?.key && hit) setFld({ key: d.key, passes: hit.index + 1 });
      })
      .catch(() => {});
  }, [lead.ra, lead.dec]);
  const rows: [string, string][] = [
    ['SPHEREx', "55 exposures on 14 Dec 2025 (IRSA's exposure table); the film rings the comet where JPL predicts it on every visit"],
    ...(c
      ? ([
          ['NEOSSat (CSA)', `${c.date_obs.replace('T', ' ').slice(0, 16)} UT, ${c.exposure_s.toFixed(0)} s exposure`],
          ['Found', `${c.offset_from_prediction_arcsec.toFixed(0)}″ from JPL's predicted place, G ≈ ${c.g_mag?.toFixed(1)}, S/N ${c.snr?.toFixed(0)}`],
          ['Pointing', `refitted on ${c.gaia_matches} Gaia DR3 stars (the header was ${c.wcs_offset_arcsec.toFixed(0)}″ off)`],
        ] as [string, string][])
      : []),
    ['Place', `${formatRA(lead.ra)} ${formatDec(lead.dec)}`],
  ];
  const media = (
    <div class="lp-pair">
      <figure>
        <FieldThumb fieldKey={fld.key} passes={fld.passes} size={170} />
        <figcaption class="mono-s">SPHEREx, 60′ field</figcaption>
      </figure>
      <figure>
        <img class="lp-neos" src="/neossat/3i-2025-12-14.png" alt="3I/ATLAS in a NEOSSat frame, ringed at the place JPL predicts" />
        <figcaption class="mono-s">NEOSSat (CSA), north up, east left</figcaption>
      </figure>
    </div>
  );
  return <PanelShell lead={lead} rows={rows} media={media} onClose={onClose} />;
}

/** The lead slid in from the right: the picture of the field, every number and check, the link into the lab, and the report as JSON. Closes with × or Esc. */
export function LeadPanel({ lead, onClose }: { lead: Lead; onClose: () => void }) {
  if (lead.atlas) return <AtlasPanel lead={lead} onClose={onClose} />;
  return <PanelShell lead={lead} rows={lead.rows} media={<FieldThumb fieldKey={lead.fieldKey} passes={lead.passes} size={200} />} onClose={onClose} />;
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
