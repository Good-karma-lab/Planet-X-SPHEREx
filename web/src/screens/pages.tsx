import { useEffect, useState } from 'preact/hooks';
import type { Health } from '../lib/api';
import type { Screen } from '../lib/route';
import './findings/findings.css';
import { IndexV2 } from './index/IndexV2';
import { AboutBody } from './about/About';
import { FindingsView } from './findings/finds';
import { useLeadParam } from './findings/leads';
import { mountTour } from '../ui/Hints';
import { SearchedCounter, useSearched } from './search/searched';


mountTour();


/** True on a desktop and a two-pane tablet (responsive.css): the lead report then sits beside the list instead of sliding over it. */
function useWide() {
  const q = '(min-width: 1000px) and (max-width: 1279.98px) and (min-height: 480px) and (orientation: landscape), (min-width: 1280px)';
  const [w, setW] = useState(() => matchMedia(q).matches);
  useEffect(() => {
    const m = matchMedia(q);
    const f = () => setW(m.matches);
    m.addEventListener('change', f);
    return () => m.removeEventListener('change', f);
  }, []);
  return w;
}

const FINDINGS_HINT =
  'Every find of every searched field, each checked against the catalogues. Every number is measured by this system on SPHEREx Level-2 files.';

function FindingsPage() {
  const searched = useSearched();
  const [leadId, setLeadId] = useLeadParam();
  const wide = useWide();
  return (
    <>
      <div class="pg-h">
        <h1>Findings</h1>
        <p class="hint">{FINDINGS_HINT}</p>
      </div>
      <SearchedCounter fields={searched} leads={false} />
      <FindingsView
        wide={wide}
        leadId={leadId}
        setLeadId={setLeadId}
        intro={
          <>
            <p class="hint">{FINDINGS_HINT}</p>
            <SearchedCounter fields={searched} leads={false} />
          </>
        }
      />
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
