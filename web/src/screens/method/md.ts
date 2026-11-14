/** A small Markdown renderer for the Method text: headings, paragraphs, lists, tables, code blocks, bold, italics-free inline code and links. HTML in the text is escaped. */

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** Inline: `code`, **bold**, [text](url). */
function inline(s: string): string {
  const parts = s.split(/(`[^`]+`)/g);
  return parts
    .map((p) => {
      if (p.startsWith('`') && p.endsWith('`') && p.length > 1) return `<code>${esc(p.slice(1, -1))}</code>`;
      let t = esc(p);
      t = t.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
      t = t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
      return t;
    })
    .join('');
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

export interface Heading {
  id: string;
  title: string;
  level: number;
}

export interface Rendered {
  html: string;
  headings: Heading[];
}

/** Renders one Markdown text; `prefix` keeps the ids of several texts apart. */
export function render(md: string, prefix: string): Rendered {
  const lines = md.split('\n');
  const out: string[] = [];
  const headings: Heading[] = [];
  let i = 0;
  const isTable = (l: string) => l.trim().startsWith('|');
  while (i < lines.length) {
    const l = lines[i]!;
    if (!l.trim()) {
      i++;
      continue;
    }
    if (l.startsWith('```')) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i]!.startsWith('```')) buf.push(lines[i++]!);
      i++;
      out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`);
      continue;
    }
    const h = /^(#{1,4}) (.*)$/.exec(l);
    if (h) {
      const level = h[1]!.length;
      const id = `${prefix}-${slug(h[2]!)}`;
      headings.push({ id, title: h[2]!, level });
      out.push(`<h${level + 1} id="${id}">${inline(h[2]!)}</h${level + 1}>`);
      i++;
      continue;
    }
    if (isTable(l)) {
      const rows: string[][] = [];
      while (i < lines.length && isTable(lines[i]!)) {
        const cells = lines[i]!.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      const [head, ...body] = rows;
      out.push(
        `<div class="mf-scroll"><table class="tbl"><thead><tr>${head!.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${body
          .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
          .join('')}</tbody></table></div>`,
      );
      continue;
    }
    const list = /^(\s*)([-*]|\d+\.) (.*)$/.exec(l);
    if (list && list[1] === '') {
      const ordered = /\d/.test(list[2]!);
      const items: string[] = [];
      while (i < lines.length) {
        const m = /^([-*]|\d+\.) (.*)$/.exec(lines[i]!);
        if (!m) break;
        let t = m[2]!;
        i++;
        while (i < lines.length && /^\s+\S/.test(lines[i]!) && !/^\s*([-*]|\d+\.) /.test(lines[i]!)) t += ' ' + lines[i++]!.trim();
        items.push(`<li>${inline(t)}</li>`);
      }
      out.push(`<${ordered ? 'ol' : 'ul'}>${items.join('')}</${ordered ? 'ol' : 'ul'}>`);
      continue;
    }
    const buf: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !isTable(lines[i]!) && !/^(#{1,4} |```)/.test(lines[i]!) && !/^([-*]|\d+\.) /.test(lines[i]!)) buf.push(lines[i++]!.trim());
    out.push(`<p>${inline(buf.join(' '))}</p>`);
  }
  return { html: out.join('\n'), headings };
}
