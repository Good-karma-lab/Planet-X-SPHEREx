// Small rules of the lab that are easy to lose in a merge, each with a unit test.
import { taskOfParams } from './task';

/** A square task ("Mark everything that moves and has no ring") has no object of its own: nothing is selected when the field
 *  opens (not even a test target that happens to lie in the square), and the 2′ inset waits for a source the user clicks. */
export function isSquareTask(p: Record<string, string>): boolean {
  return !!p['task'] && taskOfParams(p)?.kind === 'cell';
}

/** Whether the test target in the field is selected when the field opens: always, except in a square task. */
export function selectsTargetOnOpen(p: Record<string, string>): boolean {
  return !isSquareTask(p);
}
