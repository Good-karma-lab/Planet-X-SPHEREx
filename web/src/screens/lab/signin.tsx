// A guest's click on something that needs an account (a task answer, "I have looked through this quarter", "Add to
// investigation", the first mark to keep) opens this small popup instead of moving on silently. The sign-in link brings the person
// back to the same place with `do=<the click>` in the address, and the lab does the click once they are signed in.
import type { ComponentChildren } from 'preact';
import { useCallback, useState } from 'preact/hooks';
import { currentUser } from '../../lib/account';
import { SignInDialog } from '../../ui/SignInCard';
import { formatHash, parseHash } from '../../lib/route';

export type SignInWhy = 'look' | 'answer' | 'add' | 'marks';

/** The one line under the card's title: why this click needs a name. */
const REASON: Record<SignInWhy, string> = {
  look: 'To count your look',
  answer: 'To count your answer',
  add: 'To add to an investigation',
  marks: 'To keep your marks',
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
  const ask = (why: SignInWhy, action: string) => setOpen({ why, action });
  const close = useCallback(() => setOpen(null), []);
  const signedIn = !!currentUser();
  const popup: ComponentChildren = open ? <SignInDialog next={returnTo(open.action)} reason={REASON[open.why]} onClose={close} /> : null;
  return { signedIn, ask, popup };
}
