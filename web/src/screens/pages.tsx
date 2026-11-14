import { useEffect, useState } from 'preact/hooks';
import type { Health } from '../lib/api';
import type { Screen } from '../lib/route';
import './findings/findings.css';
import { useLive } from './findings/live';
import { IndexV2 } from './index/IndexV2';
import { AboutBody } from './about/About';
import { ATLAS_LEAD, LeadPanel, LeadsList, rankLeads, useLeadParam } from './findings/leads';
import { mountTour } from '../ui/Tour';
import { SearchedCounter, useSearched } from './search/searched';


mountTour();


/** True from 1100 px up: the lead report then sits beside the list instead of sliding over it. */
function useWide() {
  const q = '(min-width: 1100px)';
  const [w, setW] = useState(() => matchMedia(q).matches);
  useEffect(() => {
    const m = matchMedia(q);
    const f = () => setW(m.matches);
    m.addEventListener('change', f);
    return () => m.removeEventListener('change', f);
  }, []);
  return w;
}

function FindingsPage() {
  const live = useLive();
  const searched = useSearched();
  const leads = [...rankLeads(live), ATLAS_LEAD];
  const [leadId, setLeadId] = useLeadParam();
  const wide = useWide();
  const open = leads.find((l) => l.id === leadId);
  const shown = open ?? (wide ? leads[0] : undefined);
  const all = searched ? searched.reduce((a, f) => a + f.found, 0) : undefined;
  return (
    <>
      <div class="pg-h">
        <h1>Findings</h1>
        <p class="hint">What the searches find in real SPHEREx exposures. Every number is measured by this system on SPHEREx Level-2 files.</p>
      </div>
      <SearchedCounter fields={searched} />
      {wide ? (
        <div class="fsplit">
          <LeadsList leads={leads} onOpen={setLeadId} openId={shown?.id ?? null} all={all} />
          {shown ? <LeadPanel lead={shown} onClose={() => setLeadId(null)} inline /> : <div class="leadpanel glass inline mono-s">reading the searches…</div>}
        </div>
      ) : (
        <>
          <LeadsList leads={leads} onOpen={setLeadId} openId={open?.id ?? null} all={all} />
          {open && <LeadPanel lead={open} onClose={() => setLeadId(null)} />}
        </>
      )}
      <p class="fmethod">
        <a href="#/method?sec=validation">How we know it works → Method, validation</a>
      </p>
    </>
  );
}

/** The full method is 80 kB of text: loaded when the Method page opens, not with the app. */
function LazyMethodFull() {
  const [C, setC] = useState<null | (() => preact.JSX.Element)>(null);
  useEffect(() => {
    void import('./method/MethodFull').then((m) => setC(() => m.MethodFull));
  }, []);
  return C ? <C /> : <div class="mono-s">Loading the full method…</div>;
}

function MethodPage() {
  return <LazyMethodFull />;
}

function AboutPage() {
  return <AboutBody />;
}

export function InfoPage({ screen, health }: { screen: Screen; health: Health | null }) {
  return (
    <div class={`screen${screen === 'index' ? ' ixscreen' : ''}`}>
      {screen === 'index' ? (
        <IndexV2 health={health} />
      ) : (
      <div class={`ipage ${screen === 'method' ? 'method' : ''}`}>
        {screen === 'findings' ? (
          <FindingsPage />
        ) : screen === 'method' ? (
          <MethodPage />
        ) : (
          <AboutPage />
        )}
      </div>
      )}
    </div>
  );
}
