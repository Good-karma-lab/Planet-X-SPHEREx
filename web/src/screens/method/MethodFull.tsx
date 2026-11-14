import { useEffect, useMemo, useRef } from 'preact/hooks';
import './method.css';
import { render, type Heading } from './md';
import { playWhenVisible } from './media';

interface Part {
  id: string;
  group: string;
  text: string;
}

// One part per file in ./text, in file-name order (01-…, 02-…): the id of a part is its file name without the extension
const files = import.meta.glob('./text/*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
const PARTS: Part[] = Object.entries(files)
  .map(([path, text]) => ({ id: path.replace(/^.*\//, '').replace(/\.md$/, ''), text }))
  .sort((a, b) => a.id.localeCompare(b.id))
  .map(({ id, text }) => ({ id, text, group: /^# (.*)$/m.exec(text)?.[1] ?? id }));

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
      go(`m-${sec}`, false);
      setTimeout(() => go(`m-${sec}`, false), 400);
    }
    const onHash = () => {
      const w = wanted();
      if (w) go(`m-${w}`, true);
    };
    window.addEventListener('hashchange', onHash);
    const stopMedia = root.current ? playWhenVisible(root.current) : () => {};
    return () => {
      window.removeEventListener('hashchange', onHash);
      stopMedia();
    };
  }, []);
  return (
    <div class="mfull" ref={root}>
      <div class="mf-cols">
        <nav class="mf-toc card glass" aria-label="Contents of the Method">
          {rendered.map(({ part, r }) => (
            <div key={part.id}>
              <a class="toc-sep" href={`#/method?sec=${part.id}`} onClick={(e) => { e.preventDefault(); go(`m-${part.id}`, true); }}>
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
            <section key={part.id} id={`m-${part.id}`} class="card glass motion">
              <div class="mf-prose" dangerouslySetInnerHTML={{ __html: r.html }} />
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
