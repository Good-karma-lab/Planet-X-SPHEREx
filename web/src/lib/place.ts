// The last place in the lab this person worked on (this tab): signing in from anywhere comes back there, never to a new page.

const KEY = 'spherex-last-lab';
let last = '';

export function rememberLab(hash: string): void {
  last = hash;
  try {
    sessionStorage.setItem(KEY, hash);
  } catch {
    // this tab only: the module keeps it
  }
}

/** the lab address to come back to after a sign-in ('' when this tab has not been in the lab) */
export function lastLab(): string {
  if (last) return last;
  try {
    return sessionStorage.getItem(KEY) ?? '';
  } catch {
    return '';
  }
}
