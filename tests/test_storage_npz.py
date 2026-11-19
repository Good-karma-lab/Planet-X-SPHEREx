"""load_npz reads what np.load reads (raw zip members, one zlib.decompress each) and lets go of the GIL while it inflates."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from service.storage import load_npz, save_npz


def test_it_reads_what_np_load_reads(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    arrays = {
        "rows": rng.normal(size=(64, 2040)).astype(np.float32),
        "flags": rng.integers(0, 2**16, size=(64, 2040), dtype=np.uint16),
        "f": np.asfortranarray(rng.normal(size=(7, 5))),
        "b": np.array([True, False, True]),
        "s": np.array(3.5),
        "empty": np.zeros((0, 4), np.int32),
        "text": np.array(["a", "bc"]),
        "big_endian": np.arange(10, dtype=">f4"),
    }
    p = tmp_path / "a.npz"
    save_npz(p, **arrays)
    q = tmp_path / "plain.npz"
    np.savez(q, **arrays)  # stored, not deflated
    for path in (p, q):
        got = load_npz(path)
        assert got is not None and set(got) == set(arrays)
        with np.load(path) as ref:
            for k in arrays:
                assert got[k].dtype == ref[k].dtype and got[k].shape == ref[k].shape
                assert np.array_equal(got[k], ref[k])
        got["rows"][0, 0] = 1.0  # writable, as np.load's arrays are


def test_a_broken_or_missing_file_is_none(tmp_path: Path) -> None:
    assert load_npz(tmp_path / "none.npz") is None
    p = tmp_path / "a.npz"
    save_npz(p, x=np.arange(100000, dtype=np.float64))
    blob = bytearray(p.read_bytes())
    (tmp_path / "cut.npz").write_bytes(blob[: len(blob) // 2])
    assert load_npz(tmp_path / "cut.npz") is None
    blob[len(blob) // 3] ^= 0xFF  # a flipped byte inside the deflate stream: an error or a bad CRC, never wrong numbers
    (tmp_path / "flip.npz").write_bytes(bytes(blob))
    assert load_npz(tmp_path / "flip.npz") is None
    np.save(tmp_path / "obj.npy", np.array([{"a": 1}], dtype=object), allow_pickle=True)
    import zipfile

    with zipfile.ZipFile(tmp_path / "obj.npz", "w") as z:
        z.write(tmp_path / "obj.npy", "o.npy")
    assert load_npz(tmp_path / "obj.npz") is None  # pickled objects are never read


def test_it_lets_the_event_loop_thread_run_while_it_inflates(tmp_path: Path) -> None:
    """The longest gap a ticking thread sees while another thread reads a 60 MB row cache file: np.load held the GIL throughout."""
    p = tmp_path / "rows.npz"
    rng = np.random.default_rng(2)
    save_npz(p, rows=(rng.normal(size=(4000, 4000)) * 100).astype(np.int16).astype(np.float32))

    def worst_gap(fn: object) -> float:
        stop = threading.Event()
        gaps: list[float] = []

        def tick() -> None:
            last = time.perf_counter()
            while not stop.is_set():
                time.sleep(0.001)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        t = threading.Thread(target=tick)
        t.start()
        time.sleep(0.02)
        for _ in range(3):
            fn()  # type: ignore[operator]
        stop.set()
        t.join()
        return max(gaps)

    def old() -> None:
        with np.load(p) as z:
            _ = {k: z[k] for k in z.files}

    new_gap = worst_gap(lambda: load_npz(p))
    old_gap = worst_gap(old)
    assert new_gap < max(0.15, old_gap / 2), (new_gap, old_gap)  # a floor for a loaded machine (other builds share its cores)
