from __future__ import annotations

import io

import numpy as np
import pytest
from astropy.io import fits

from engine.spx import flags, psf, wave, wcs
from engine.spx.fitsio import blocks, data_size, hdu_at, header_length
from engine.spx.layout import PlainImage, RiceImage, layout_from_hdus
from engine.spx.rows import descriptors, heap_span, plain_rows, rice_rows
from tests.synth_l2 import QR2_BITS, V27_BITS, make_file


def walk(data: bytes) -> list:
    hdus, off = [], 0
    while off < len(data):
        h = hdu_at(len(hdus), off, data[off:])
        hdus.append(h)
        off = h.next_offset
    return hdus


@pytest.mark.parametrize("layout", ["qr2", "v27"])
def test_header_chain_matches_astropy(layout: str) -> None:
    data, _ = make_file(layout)
    ours = walk(data)
    with fits.open(io.BytesIO(data), disable_image_compression=True) as hl:
        info = [h.fileinfo() for h in hl]
        names = [h.name for h in hl]
    assert [h.name for h in ours] == names
    assert [h.header_offset for h in ours] == [i["hdrLoc"] for i in info]
    assert [h.data_offset for h in ours] == [i["datLoc"] for i in info]
    assert [blocks(h.data_size) for h in ours] == [i["datSpan"] for i in info]


def test_header_length_needs_the_end_card() -> None:
    h = fits.Header({"SIMPLE": True, "BITPIX": 8, "NAXIS": 0}).tostring().encode()
    assert header_length(h) == 2880
    assert header_length(h[:160]) is None


def test_data_size_counts_the_heap() -> None:
    h = fits.Header({"XTENSION": "BINTABLE", "BITPIX": 8, "NAXIS": 2, "NAXIS1": 8, "NAXIS2": 10, "PCOUNT": 123, "GCOUNT": 1})
    assert data_size(h) == 80 + 123


@pytest.mark.parametrize("layout", ["qr2", "v27"])
def test_rows_decode_exactly(layout: str) -> None:
    data, planes = make_file(layout, n=256)
    lay = layout_from_hdus(walk(data), len(data))
    assert lay.release_layout == layout
    y0, y1 = 100, 140
    a, b = lay.image.rows_range(y0, y1)
    np.testing.assert_array_equal(plain_rows(lay.image, data[a:b], y1 - y0), planes["image"][y0:y1])
    if isinstance(lay.flags, RiceImage):
        t0, t1 = lay.flags.tiles_of_rows(y0, y1)
        da, db = lay.flags.descriptor_range(t0, t1)
        d = descriptors(lay.flags, data[da:db])
        ha, hb = heap_span(d)
        got = rice_rows(lay.flags, d, data[lay.flags.heap_offset + ha : lay.flags.heap_offset + hb], ha, t0)
    else:
        assert isinstance(lay.flags, PlainImage)
        a, b = lay.flags.rows_range(y0, y1)
        got = plain_rows(lay.flags, data[a:b], y1 - y0)
    np.testing.assert_array_equal(got, planes["flags"][y0:y1])
    assert lay.detector == 1 and lay.spacecraft is not None


def test_flag_tables_of_both_releases() -> None:
    for layout, bits in (("qr2", QR2_BITS), ("v27", V27_BITS)):
        data, _ = make_file(layout)
        lay = layout_from_hdus(walk(data), len(data))
        table, src, mismatch = flags.resolve(lay.flags_header)
        assert src == "file" and table == bits and mismatch == {}


@pytest.mark.parametrize("layout", ["qr2", "v27"])
def test_a_kept_layout_holds_no_astropy_header(layout: str) -> None:
    # 26 Nov 2026: 2,000 kept layouts held 2.2 million astropy objects (Card, the lists of the Header's index), which every full
    # collection walked. The layout keeps the headers as text and what is read from them every time.
    import gc

    data, _ = make_file(layout)
    hdus = walk(data)
    lay = layout_from_hdus(hdus, len(data))
    seen: dict[int, object] = {}
    todo: list[object] = [lay]
    while todo:
        o = todo.pop()
        if id(o) in seen or isinstance(o, type):
            continue
        seen[id(o)] = o
        assert not isinstance(o, (fits.Header, fits.Card)), type(o)
        todo.extend(gc.get_referents(o))
    assert sum(gc.is_tracked(o) for o in seen.values()) < 30  # was 1,100 on a real file
    by = {h.name: h for h in hdus}
    # the same headers as before, read anew from the text
    assert lay.image_header.tostring() == by["IMAGE"].header.tostring()
    assert lay.flags_header.tostring() == by["FLAGS"].header.tostring()
    assert lay.flag_bits == flags.resolve(by["FLAGS"].header)
    h = by["IMAGE"].header
    assert lay.detector == int(h["DETECTOR"]) and lay.mjd == float(h["MJD-AVG"])
    assert lay.spacecraft == (float(h["X_SC"]), float(h["Y_SC"]), float(h["Z_SC"]))
    if lay.wave is not None:
        assert lay.wave.header.tostring() == by["WCS-WAVE"].header.tostring()


def test_a_header_without_what_is_read_fails_only_for_who_asks() -> None:
    data, _ = make_file("v27")
    hdus = walk(data)
    for h in hdus:
        if h.name == "IMAGE":
            for k in ("X_SC", "MJD-AVG"):
                del h.header[k]
            object.__setattr__(h, "text", "")  # the text is made from the header changed by hand
    lay = layout_from_hdus(hdus, len(data))
    assert lay.spacecraft is None and lay.detector == 1
    with pytest.raises(KeyError):
        _ = lay.mjd


def test_builtin_table_when_the_file_has_none_and_mismatch_reported() -> None:
    table, src, _ = flags.resolve(fits.Header())
    assert src == "builtin" and table["OUTLIER"] == 19
    h = fits.Header()
    h["HIERARCH MP_OUTLIER"] = 18
    _, _, mismatch = flags.resolve(h)
    assert mismatch == {"OUTLIER": (19, 18)}


def test_masks_by_name() -> None:
    assert flags.mask_of(flags.BUILTIN, flags.BAD_ALWAYS) == sum(1 << b for b in (4, 5, 6, 9, 10, 11, 19, 27))
    assert flags.mask_of(flags.BUILTIN, flags.BAD_FAR) == sum(1 << b for b in (0, 1, 2, 17))
    assert flags.mask_of(QR2_BITS, ["SNOWBALL"]) == 0  # a name the file does not define contributes nothing


def test_wave_map_passes_through_the_nodes_and_clips_outside() -> None:
    data, _ = make_file("v27", n=256)
    lay = layout_from_hdus(walk(data), len(data))
    assert lay.wave is not None
    a, b = lay.wave.data_offset, lay.wave.data_offset + lay.wave.data_size
    t = wave.table_from_hdu_bytes(lay.wave.header, data[a:b])
    wm = wave.WaveMap(t, detector=1)
    xs, ys = np.meshgrid(t.x_nodes, t.y_nodes)
    lam, bw = wm(xs, ys)
    np.testing.assert_allclose(lam, t.wavelength, atol=1e-6)
    np.testing.assert_allclose(bw, 0.02, atol=1e-6)
    lo, _ = wm([-50.0], [0.0])
    assert lo[0] == pytest.approx(t.wavelength[0, 0], abs=1e-6)
    assert t.calibration == "spectral_wcs_1_test.fits"


def test_calibration_offset_only_for_old_d5_d6_tables() -> None:
    for det, first, expected in ((5, 4.43616, 0.0064), (5, 4.44256, 0.0), (6, 5.01821, 0.0064), (4, 3.86286, 0.0)):
        data, _ = make_file("qr2", detector=det, wave_first=first)
        lay = layout_from_hdus(walk(data), len(data))
        assert lay.wave is not None
        t = wave.table_from_hdu_bytes(lay.wave.header, data[lay.wave.data_offset : lay.wave.data_offset + lay.wave.data_size])
        assert wave.calibration_offset(t, det) == pytest.approx(expected)


@pytest.mark.parametrize("layout", ["qr2", "v27"])
def test_psf_stamps_are_normalised_and_centred(layout: str) -> None:
    data, _ = make_file(layout, n=256)
    lay = layout_from_hdus(walk(data), len(data))
    assert lay.psf is not None
    raw = data[lay.psf.data_offset : lay.psf.data_offset + lay.psf.data_size]
    ps = psf.from_cube(lay.psf.header, raw) if lay.psf_kind == "cube" else psf.from_epsf(lay.psf.header, raw)
    s = psf.stamp(ps, ps.zone_index(60, 60), 0.0, 0.0, half=6)
    assert s.sum() == pytest.approx(1.0, abs=0.01)
    assert np.unravel_index(s.argmax(), s.shape) == (6, 6)
    shifted = psf.stamp(ps, 0, 0.4, 0.0, half=6)
    assert shifted[6, 7] > s[6, 7]  # the light moves toward +x
    assert ps.zone_index(250, 250) == 3
    assert ps.checksum == psf.checksum(raw)


def test_celestial_wcs_round_trip() -> None:
    data, _ = make_file("v27", n=256, ra=10.0, dec=-30.0)
    lay = layout_from_hdus(walk(data), len(data))
    w = wcs.celestial(lay.image_header)
    ra, dec = wcs.pix2sky(w, [12.3, 200.7], [40.1, 133.9])
    x, y = wcs.sky2pix(w, ra, dec)
    np.testing.assert_allclose(x, [12.3, 200.7], atol=1e-6)
    np.testing.assert_allclose(y, [40.1, 133.9], atol=1e-6)
