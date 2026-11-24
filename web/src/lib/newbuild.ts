// A tab opened before a deploy asks for the old build's lazy parts: if one cannot load, the page reloads once onto the new build.

const RELOADED = 'spherex-reloaded-for-new-build';

/** Whether an error says that a lazy part of the page could not be loaded (Chrome, Firefox and Safari word it apart). */
export const isChunkError = (e: unknown): boolean =>
  /dynamically imported module|Importing a module script failed|module script failed|Unable to preload CSS/i.test(String((e as Error | undefined)?.message ?? e ?? ''));

/** Reloads the page, once: false when it was already reloaded for this (or the browser keeps no session storage to know it). */
export function reloadOnce(): boolean {
  try {
    if (sessionStorage.getItem(RELOADED)) return false;
    sessionStorage.setItem(RELOADED, '1');
  } catch {
    return false; // nowhere to remember it: a reload could go round for ever
  }
  location.reload();
  return true;
}

/** A page that stayed up is on the current build: a later deploy may reload it again. */
export function forgetReload(): void {
  try {
    sessionStorage.removeItem(RELOADED);
  } catch {
    /* ignore */
  }
}
