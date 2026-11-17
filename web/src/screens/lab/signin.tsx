// A guest's click on something that needs an account (a task answer, "I have looked through this quarter", "Add to
// investigation", the first mark to keep) opens this small popup instead of moving on silently. The sign-in link brings the person
// back to the same place with `do=<the click>` in the address, and the lab does the click once they are signed in.
import type { ComponentChildren } from 'preact';
import { useState } from 'preact/hooks';
import { createPortal } from 'preact/compat';
import { authInfo, currentUser, googleSignInUrl, signInByEmail } from '../../lib/account';
import { formatHash, parseHash } from '../../lib/route';

export type SignInWhy = 'look' | 'answer' | 'add' | 'marks';

const TITLE: Record<SignInWhy, string> = {
  look: 'Sign in to count your look',
  answer: 'Sign in to count your answer',
  add: 'Sign in to add to an investigation',
  marks: 'Sign in to keep your marks',
};
const REASON: Record<SignInWhy, string> = {
  look: 'Looks count only with a name, so the same square is not checked twice and your points and finds are kept.',
  answer: 'Answers count only with a name, so your points and finds are kept.',
  add: 'Investigations are kept under your name, so you can come back to them.',
  marks: 'Marks are kept under your name, on every device, and count for the search.',
};

/** The address to come back to after the link: this place, with the click to do. */
export function returnTo(action: string): string {
  const r = parseHash(location.hash);
  const params = { ...r.params };
  if (action) params['do'] = action;
  else delete params['do'];
  return formatHash({ ...r, params }).slice(0, 600);
}

/** The click a sign-in link came back with (`do=` in the address), '' when none. */
export function pendingDo(params: Record<string, string>): string {
  return params['do'] ?? '';
}

/** Takes `do=` out of the address once it is done (no new history entry). */
export function clearDo(): void {
  const r = parseHash(location.hash);
  if (!('do' in r.params)) return;
  const params = { ...r.params };
  delete params['do'];
  history.replaceState(null, '', formatHash({ ...r, params }));
}

export function useSignIn(): { signedIn: boolean; ask: (why: SignInWhy, action: string) => void; popup: ComponentChildren } {
  const [open, setOpen] = useState<{ why: SignInWhy; action: string } | null>(null);
  const [email, setEmail] = useState('');
  const [state, setState] = useState<{ sent?: boolean; line: string; dev?: string }>({ line: '' });
  const ask = (why: SignInWhy, action: string) => {
    setOpen({ why, action });
    setState({ line: '' });
  };
  const signedIn = !!currentUser();
  const auth = authInfo();
  let popup: ComponentChildren = null;
  if (open) {
    const next = returnTo(open.action);
    const google = googleSignInUrl(next);
    const send = async () => {
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) return setState({ line: 'An e-mail address, please' });
      setState({ line: 'sending…' });
      try {
        const r = await signInByEmail(email.trim(), next);
        setState({ sent: true, line: `Link sent to ${email.trim()} · valid ${r.minutes} min`, dev: r.dev_link });
      } catch {
        setState({ line: 'The link could not be sent; try again in a moment' });
      }
    };
    popup = createPortal(
      <div class="signin-back" onClick={(e) => e.target === e.currentTarget && setOpen(null)} data-testid="signin-popup">
        <div class="signin" role="dialog" aria-modal="true" aria-label={TITLE[open.why]}>
          <b class="si-title">{TITLE[open.why]}</b>
          <p class="si-why">
            {REASON[open.why]} No password: we send a link to your e-mail.
          </p>
          {auth.email && (
            <form
              class="si-row"
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <input
                class="inp"
                type="email"
                autoComplete="email"
                placeholder="your e-mail"
                aria-label="Your e-mail"
                value={email}
                disabled={!!state.sent}
                onInput={(e) => setEmail((e.target as HTMLInputElement).value)}
                data-testid="signin-email"
              />
              <button class="btn sm primary" type="submit" disabled={!!state.sent} data-testid="signin-send">
                Send me a sign-in link
              </button>
            </form>
          )}
          {google && (
            <a class="btn sm si-google" href={google} data-testid="signin-google">
              Continue with Google
            </a>
          )}
          {!auth.email && !google && <p class="si-line">Sign-in opens soon</p>}
          <p class="si-line" data-testid="signin-line">
            {state.line}
            {state.dev && (
              <>
                {' · '}
                <a href={state.dev} data-testid="signin-dev">
                  open the link (dev)
                </a>
              </>
            )}
          </p>
          <button class="mini si-later" onClick={() => setOpen(null)} data-testid="signin-later">
            Not now
          </button>
        </div>
      </div>,
      // on the page itself: inside the lab's panel (a bottom sheet on a phone, which is transformed) a fixed popup would be cut off
      document.body,
    );
  }
  return { signedIn, ask, popup };
}
