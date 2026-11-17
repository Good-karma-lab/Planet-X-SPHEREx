import { beforeEach, describe, expect, it } from 'vitest';
import { addMark, allMarks, fieldKey, marksOf, marksSyncNote, onMarks, removeMark, resetMarksForTest, setMarksSync, updateMark, type Mark, type MarksRemote } from '../../src/lib/marks';

class MemStore {
  data = new Map<string, string>();
  getItem(k: string) {
    return this.data.get(k) ?? null;
  }
  setItem(k: string, v: string) {
    this.data.set(k, v);
  }
}

/** A fake of the account side (`/api/me/marks`, spec §8): an upsert by id. */
class FakeRemote implements MarksRemote {
  rows = new Map<string, Mark>();
  fail = false;
  async list() {
    return [...this.rows.values()];
  }
  async put(m: Mark) {
    if (this.fail) throw new Error('offline');
    this.rows.set(m.id, m);
    return m;
  }
  async remove(id: string) {
    this.rows.delete(id);
  }
}

const base = { field_key: fieldKey(269.4486, 4.7379, 5), cell_id: null, ra: 269.45, dec: 4.74, pass: 1, label: 'A', level: 'suspicious' as const, note: '' };
const settle = () => new Promise((r) => setTimeout(r, 0));

describe('marks', () => {
  let store: MemStore;
  beforeEach(() => {
    store = new MemStore();
    resetMarksForTest(store);
  });

  it('keeps a guest’s marks in the browser store, with an id made here', () => {
    const m = addMark(base);
    expect(m.id).toBeTruthy();
    expect(new Date(m.created).toISOString()).toBe(m.created);
    resetMarksForTest(store); // a new visit reads the store again
    expect(allMarks()).toEqual([m]);
    expect(marksOf(base.field_key)).toHaveLength(1);
    expect(marksOf('elsewhere')).toHaveLength(0);
  });

  it('edits and removes, and tells its listeners', () => {
    let calls = 0;
    onMarks(() => calls++);
    const m = addMark(base);
    expect(updateMark(m.id, { label: 'B', level: 'artefact' })?.level).toBe('artefact');
    expect(allMarks()[0]!.label).toBe('B');
    removeMark(m.id);
    expect(allMarks()).toEqual([]);
    expect(calls).toBe(3);
    expect(updateMark('nope', { label: 'x' })).toBeNull();
  });

  it('moves a guest’s marks to the account at sign-in and merges the account’s own', async () => {
    const mine = addMark(base);
    const remote = new FakeRemote();
    const theirs: Mark = { ...base, id: 'srv-1', label: 'from account', created: '2026-11-01T00:00:00.000Z' };
    remote.rows.set(theirs.id, theirs);
    await setMarksSync(remote);
    expect(allMarks().map((m) => m.id)).toEqual(['srv-1', mine.id]);
    expect(remote.rows.has(mine.id)).toBe(true);
    // from then on every change goes to the account
    const next = addMark({ ...base, label: 'C' });
    updateMark(next.id, { note: 'n' });
    await settle();
    expect(remote.rows.get(next.id)?.note).toBe('n');
    removeMark(next.id);
    await settle();
    expect(remote.rows.has(next.id)).toBe(false);
  });

  it('says so when the account cannot be reached, and stops syncing at sign-out', async () => {
    const remote = new FakeRemote();
    await setMarksSync(remote);
    remote.fail = true;
    const m = addMark(base);
    await settle();
    expect(marksSyncNote()).toBe('Marks not saved to the account');
    await setMarksSync(null);
    expect(marksSyncNote()).toBe('');
    updateMark(m.id, { label: 'local only' });
    await settle();
    expect(remote.rows.size).toBe(0);
    expect(allMarks()).toHaveLength(1);
  });
});
