import { useEffect, useMemo, useRef } from 'preact/hooks';
import './method.css';
import { render, type Heading } from './md';
import overview from './text/overview.md?raw';
import data from './text/data.md?raw';
import stacks from './text/stacks.md?raw';
import views from './text/views.md?raw';
import spectra from './text/spectra.md?raw';
import searches from './text/searches.md?raw';
import checks from './text/checks.md?raw';
import validation from './text/validation.md?raw';
import repInjection from './text/report-injection.md?raw';
import repMost from './text/report-most.md?raw';
import repWavelength from './text/report-wavelength.md?raw';

interface Part {
  id: string;
  group: string;
  text: string;
}

const PARTS: Part[] = [
  { id: 'overview', group: 'Overview', text: overview },
  { id: 'data', group: 'Data and reading', text: data },
  { id: 'stacks', group: 'Cleaning and stacks', text: stacks },
  { id: 'views', group: 'Views', text: views },
  { id: 'spectra', group: 'Spectra', text: spectra },
  { id: 'searches', group: 'Searches', text: searches },
  { id: 'checks', group: 'Anomalies, checks and chance', text: checks },
  { id: 'validation', group: 'Validation', text: validation },
  { id: 'dated', group: 'Dated measurements', text: [repInjection, repMost, repWavelength].join('\n\n') },
];

/** The section of the address (#/method?sec=searches), if any. */
const wanted = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('sec');

/**
 * The full method of this service: every step with its parameters, the reason for the choice and the numbers the service measures, from the data it reads to the
 * validation of every search. The text lives in ./text and is rendered here with a table of contents.
 */
export function MethodFull() {
  const root = useRef<HTMLDivElement>(null);
  const rendered = useMemo(() => PARTS.map((p) => ({ part: p, r: render(p.text, p.id) })), []);
  const go = (id: string, smooth: boolean) => {
    const el = root.current?.querySelector(`#${CSS.escape(id)}`) as HTMLElement | null;
    if (!el) return false;
    el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
    return true;
  };
  useEffect(() => {
    const sec = wanted();
    if (sec) {
      go(`part-${sec}`, false);
      setTimeout(() => go(`part-${sec}`, false), 400);
    }
    const onHash = () => {
      const w = wanted();
      if (w) go(`part-${w}`, true);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  return (
    <div class="mfull" ref={root}>
      <div class="mf-cols">
        <nav class="mf-toc card glass" aria-label="Contents of the Method">
          {rendered.map(({ part, r }) => (
            <div key={part.id}>
              <a class="toc-sep" href={`#/method?sec=${part.id}`} onClick={(e) => { e.preventDefault(); go(`part-${part.id}`, true); }}>
                {part.group}
              </a>
              {r.headings
                .filter((h: Heading) => h.level === 2)
                .map((h: Heading) => (
                  <a key={h.id} href={`#/method?sec=${part.id}`} onClick={(e) => { e.preventDefault(); go(h.id, true); }}>
                    {h.title}
                  </a>
                ))}
            </div>
          ))}
        </nav>
        <div class="mf-body">
          {rendered.map(({ part, r }) => (
            <section key={part.id} id={`part-${part.id}`} class="card glass motion">
              <div class="mf-prose" dangerouslySetInnerHTML={{ __html: r.html }} />
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
