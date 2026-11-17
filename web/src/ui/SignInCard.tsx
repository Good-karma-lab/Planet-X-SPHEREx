// The one sign-in form of the site: the Cabinet's guest page and the lab's popup both show it. Google first (when this server has
// it), then the e-mail link; after sending, the same card says where the link went. No password anywhere.
import { useEffect, useRef, useState } from 'preact/hooks';
import { createPortal } from 'preact/compat';
import { authInfo, googleSignInUrl, signInByEmail } from '../lib/account';
import './signin.css';

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Google's "G" mark, in its own four colours (Google Identity branding guidelines). */
const G_MARK =
  '<svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>';
const MAIL =
  '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="3" y="5.5" width="18" height="13" rx="2"/><path d="m3.8 7 8.2 6 8.2-6"/></svg>';

export interface SignInProps {
  /** where the link brings the person back to (a place inside this app, "#/…") */
  next: string;
  /** one line under the title: why this asks for a name ("To count your look") */
  reason?: string;
  /** a line in red above the form (an expired link, a Google sign-in that did not finish) */
  error?: string;
  /** shown as a popup: the close button */
  onClose?: () => void;
}

export function SignInCard({ next, reason, error: given, onClose }: SignInProps) {
  const auth = authInfo();
  const google = googleSignInUrl(next);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sent, setSent] = useState<{ to: string; minutes: number; dev?: string } | null>(null);
  const [resent, setResent] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  const card = useRef<HTMLDivElement>(null);
  // the card's height while the form was on it: "Check your inbox" keeps it, so the card does not jump when the link is sent
  const [held, setHeld] = useState(0);
  useEffect(() => {
    if (sent) return;
    // now and once the popup has settled: the click that opened it may still move the focus (a tap on a phone)
    field.current?.focus();
    const t = setTimeout(() => document.activeElement !== field.current && field.current?.focus(), 120);
    return () => clearTimeout(t);
  }, [sent]);
  const send = async (to: string) => {
    if (!EMAIL.test(to)) return setError('Enter an e-mail address like name@example.org');
    setBusy(true);
    setError('');
    if (!sent) setHeld(card.current?.offsetHeight ?? 0);
    try {
      const r = await signInByEmail(to, next);
      setSent({ to, minutes: r.minutes, dev: r.dev_link });
      return true;
    } catch (e) {
      setError((e as Error).message || 'The link could not be sent · try again');
      return false;
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      ref={card}
      class="sx-card"
      style={sent && held ? { minHeight: `${held}px` } : undefined}
      role={onClose ? 'dialog' : undefined} aria-modal={onClose ? 'true' : undefined} aria-labelledby="sx-title" data-testid="signin">
      {onClose && (
        <button class="sx-close" aria-label="Close" onClick={onClose} data-testid="signin-later">
          ×
        </button>
      )}
      {sent ? (
        <div class="sx-sent" data-testid="signin-sent">
          <span class="sx-mail" dangerouslySetInnerHTML={{ __html: MAIL }} />
          <h2 id="sx-title">Check your inbox</h2>
          <p class="sx-sub" data-testid="signin-status">
            We sent a link to <b>{sent.to}</b>
          </p>
          <p class="sx-note">The link works once, for {sent.minutes} minutes</p>
          {sent.dev && (
            <a class="sx-dev" href={sent.dev} data-testid="dev-link">
              Dev mode: open the sign-in link
            </a>
          )}
          <div class="sx-again">
            <button class="sx-linkbtn" onClick={() => (setSent(null), setResent(false))} data-testid="signin-other">
              Use a different e-mail
            </button>
            <span aria-hidden="true">·</span>
            <button class="sx-linkbtn" disabled={busy || resent} onClick={() => void send(sent.to).then((ok) => ok && setResent(true))} data-testid="signin-resend">
              {resent ? 'Sent again' : busy ? 'Sending…' : 'Resend'}
            </button>
          </div>
          {error && <p class="sx-err">{error}</p>}
        </div>
      ) : (
        <>
          <h2 id="sx-title">Sign in to SPHEREx Space</h2>
          {reason && <p class="sx-sub">{reason}</p>}
          {given && (
            <p class="sx-err top" role="status" data-testid="signin-status">
              {given}
            </p>
          )}
          {google && (
            <a class="sx-google" href={google} data-testid="google">
              <span dangerouslySetInnerHTML={{ __html: G_MARK }} style={{ display: 'contents' }} />
              Continue with Google
            </a>
          )}
          {google && auth.email && (
            <div class="sx-or" aria-hidden="true">
              <span>or</span>
            </div>
          )}
          {auth.email && (
            <form
              class="sx-form"
              noValidate
              onSubmit={(e) => {
                e.preventDefault();
                if (busy) return;
                // what the field shows is what is sent (a browser's autofill may not tell the form), and it stays shown while the link goes out
                const to = (field.current?.value ?? email).trim();
                setEmail(to);
                void send(to);
              }}
            >
              <label class="sx-label" for="sx-email">
                E-mail
              </label>
              <input
                ref={field}
                autoFocus
                id="sx-email"
                class={`sx-input ${error ? 'bad' : ''}`}
                type="email"
                name="email"
                autoComplete="email"
                inputMode="email"
                placeholder="name@example.org"
                aria-invalid={error ? 'true' : undefined}
                aria-describedby={error ? 'sx-email-err' : undefined}
                value={email}
                readOnly={busy}
                onInput={(e) => (setEmail((e.target as HTMLInputElement).value), error && setError(''))}
                data-testid="signin-email"
              />
              {error && (
                <p class="sx-err" id="sx-email-err" role="alert">
                  {error}
                </p>
              )}
              <button class={`sx-submit${busy ? ' busy' : ''}`} type="submit" disabled={busy} aria-busy={busy} data-testid="signin-send">
                {busy && <span class="sx-spin" aria-hidden="true" />}
                {busy ? 'Sending…' : 'Continue with e-mail'}
              </button>
            </form>
          )}
          {!auth.email && !google && (
            <p class="sx-sub" data-testid="signin-soon">
              Sign-in opens soon · your marks are kept in this browser
            </p>
          )}
          {(auth.email || google) && (
            <p class="sx-legal">
              By continuing you agree to the <a href="/terms">Terms</a> and <a href="/privacy">Privacy</a>
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** The card as a popup over the page: Esc, × and a click outside close it. */
export function SignInDialog(props: SignInProps & { onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && props.onClose();
    document.addEventListener('keydown', esc);
    return () => document.removeEventListener('keydown', esc);
  }, [props.onClose]);
  // on the page itself: inside the lab's panel (a bottom sheet on a phone, which is transformed) a fixed popup would be cut off
  return createPortal(
    <div class="sx-back" onClick={(e) => e.target === e.currentTarget && props.onClose()} data-testid="signin-popup">
      <SignInCard {...props} />
    </div>,
    document.body,
  );
}
