from __future__ import annotations

import gc

from service.memwatch import MemWatch


def test_line_names_sizes_and_survives_a_missing_collection() -> None:
    def gone() -> int:
        raise AttributeError("not there yet")

    w = MemWatch({"kept": lambda: 7, "gone": gone})
    w.start()
    try:
        gc.collect()
        line = w.line()
    finally:
        w.stop()
    assert "'kept': 7" in line and "'gone': 'AttributeError'" in line
    assert "gc2 1 " in line  # the collection was counted, and the next line starts anew
    assert "gc2 0 " in w.line()
    assert w._on_gc not in gc.callbacks
