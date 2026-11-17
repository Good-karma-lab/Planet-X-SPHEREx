// The cabinet's corner: the notifications bell with its count and the menu under the person's name (name, public page, leaderboard,
// data, sign out, delete). Both open a small glass panel and close on a click outside or Escape.

import { useEffect, useRef, useState } from 'preact/hooks';
import type { ComponentChildren } from 'preact';
import { api, loadUser, renameUser, signOut, type User } from '../../lib/account';

export interface Notice {
  id: number;
  kind: string;
  text: string;
  ref: string | null;
  created: string;
  read: boolean;
}

const ago = (iso: string) => {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(s)) return '';
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${iso.slice(0, 10)}`;
};

/** A button with a panel under it; `open` can be set from the address (#/me?tab=notifications). */
function Pop({ open, setOpen, button, children, cls }: { open: boolean; setOpen: (v: boolean) => void; button: ComponentChildren; children: ComponentChildren; cls: string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const out = (e: Event) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('pointerdown', out);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('pointerdown', out);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);
  return (
    <div class={`cb-pop ${cls} ${open ? 'open' : ''}`} ref={box}>
      {button}
      {open && <div class="cb-poppanel glass">{children}</div>}
    </div>
  );
}

const BELL =
  '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M6 10a6 6 0 0 1 12 0c0 4.2 1.2 6.3 2 7.2H4c.8-.9 2-3 2-7.2Z"/><path d="M9.8 20a2.4 2.4 0 0 0 4.4 0"/></svg>';
const CARET = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m7 10 5 5 5-5"/></svg>';

export function Bell({ notices, initiallyOpen, reload }: { notices: Notice[] | null; initiallyOpen: boolean; reload: () => void }) {
  const [open, setOpen] = useState(initiallyOpen);
  const [status, setStatus] = useState('');
  useEffect(() => setOpen(initiallyOpen), [initiallyOpen]);
  const unread = notices?.filter((n) => !n.read).length ?? 0;
  const readAll = () => api('/api/me/notifications/read', { method: 'POST', body: {} }).then(reload, (e: Error) => setStatus(e.message));
  return (
    <Pop
      cls="cb-bell"
      open={open}
      setOpen={setOpen}
      button={
        <button class={`cb-iconbtn ${unread ? 'hot' : ''}`} aria-label={`Notifications: ${unread} unread`} aria-expanded={open} onClick={() => (open ? setOpen(false) : (setOpen(true), reload()))} data-testid="tab-notifications">
          <span dangerouslySetInnerHTML={{ __html: BELL }} style={{ display: 'contents' }} />
          {unread > 0 && (
            <span class="cb-count" data-testid="bell-count">
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </button>
      }
    >
      <div class="cb-pophead">
        <b>Notifications</b>
        {unread > 0 && (
          <button class="cb-link" onClick={() => void readAll()}>
            Mark all read
          </button>
        )}
      </div>
      <div class="cb-notes" data-testid="panel-notifications">
        {!notices ? (
          <p class="cb-empty">Loading…</p>
        ) : notices.length === 0 ? (
          <p class="cb-empty">No notifications</p>
        ) : (
          notices.map((n) => (
            <a key={n.id} class={`cb-noterow ${n.read ? '' : 'unread'}`} href={!n.ref ? '#/me' : n.ref.startsWith('#/') ? n.ref : `#/findings?lead=${encodeURIComponent(n.ref)}`} onClick={() => setOpen(false)} data-testid="notice-row">
              <span>{n.text}</span>
              <span class="mono-s">{ago(n.created)}</span>
            </a>
          ))
        )}
      </div>
      {status && <p class="cb-status bad">{status}</p>}
    </Pop>
  );
}

function Toggle({ label, on, disabled, set, testid }: { label: string; on: boolean; disabled?: boolean; set: (v: boolean) => void; testid: string }) {
  return (
    <label class="cb-menuitem cb-toggle">
      <span>{label}</span>
      <input type="checkbox" checked={on} disabled={disabled} onChange={(e) => set((e.target as HTMLInputElement).checked)} data-testid={testid} />
      <i aria-hidden="true" />
    </label>
  );
}

export function AccountMenu({ user, initiallyOpen }: { user: User; initiallyOpen: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(user.name);
  const [status, setStatus] = useState('');
  const [prefs, setPrefs] = useState<{ slug: string; public: boolean; hide: boolean } | null>(null);
  useEffect(() => setOpen(initiallyOpen), [initiallyOpen]);
  useEffect(() => {
    if (!open || prefs) return;
    void Promise.all([api<{ slug: string; public: boolean }>('/api/me/profile'), api<{ hide_from_board: boolean }>('/api/me/score')]).then(
      ([p, s]) => setPrefs({ slug: p.slug, public: p.public, hide: s.hide_from_board }),
      (e: Error) => setStatus(e.message),
    );
  }, [open]);
  const patch = (body: Record<string, boolean>, next: Partial<{ public: boolean; hide: boolean }>) => {
    if (prefs) setPrefs({ ...prefs, ...next });
    api('/api/me', { method: 'PATCH', body }).then(
      () => setStatus(''),
      (e: Error) => setStatus(e.message),
    );
  };
  const save = (e: Event) => {
    e.preventDefault();
    renameUser(name.trim() || user.name).then(
      () => setEditing(false),
      (x: Error) => setStatus(x.message),
    );
  };
  return (
    <Pop
      cls="cb-acct"
      open={open}
      setOpen={setOpen}
      button={
        <button class="cb-namebtn" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="account-menu">
          <span class="cb-avatar">{(user.name.trim()[0] ?? '?').toUpperCase()}</span>
          <b data-testid="who-name">{user.name}</b>
          <span dangerouslySetInnerHTML={{ __html: CARET }} style={{ display: 'contents' }} />
        </button>
      }
    >
      <div class="cb-menu" data-testid="panel-account">
        <div class="cb-menuwho">
          {editing ? (
            <form onSubmit={save} class="cb-rename">
              <input class="inp" aria-label="Name shown in the credit line" maxLength={60} value={name} onInput={(e) => setName((e.target as HTMLInputElement).value)} />
              <button class="btn sm" type="submit">
                Save
              </button>
            </form>
          ) : (
            <>
              <b>{user.name}</b>
              <span class="mono-s">{user.email}</span>
            </>
          )}
        </div>
        {!editing && (
          <button class="cb-menuitem" onClick={() => setEditing(true)} data-testid="rename">
            Change name
          </button>
        )}
        <Toggle label="Public profile page" on={prefs?.public === true} disabled={!prefs} set={(v) => patch({ public_profile: v }, { public: v })} testid="public-profile" />
        {prefs?.public && (
          <a class="cb-menuitem sub" href={`#/u/${prefs.slug}`} data-testid="open-profile">
            Open my page
          </a>
        )}
        <Toggle label="Hide me from the leaderboard" on={prefs?.hide === true} disabled={!prefs} set={(v) => patch({ hide_from_board: v }, { hide: v })} testid="hide-board" />
        <a class="cb-menuitem" href="#/leaders">
          Leaderboard
        </a>
        <a class="cb-menuitem" href="/api/me/export" download="spherex-space-my-data.json" data-testid="download-data">
          Download my data
        </a>
        <hr />
        <button class="cb-menuitem" data-testid="signout" onClick={() => void signOut().catch((x: Error) => setStatus(x.message))}>
          Sign out
        </button>
        <button
          class="cb-menuitem danger"
          data-testid="delete-account"
          onClick={() =>
            confirm('Delete your account and all your data? Marks, squares, investigations, history and notifications are removed and cannot be restored. Findings you submitted stay, without your name.') &&
            void api('/api/me', { method: 'DELETE' }).then(() => loadUser(), (e: Error) => setStatus(e.message))
          }
        >
          Delete my account and my data
        </button>
        {status && <p class="cb-status bad">{status}</p>}
      </div>
    </Pop>
  );
}
