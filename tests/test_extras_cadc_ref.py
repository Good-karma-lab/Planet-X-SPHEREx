"""Deep references from CADC: the TAP answer, the DataLink artifact, the SODA cutout and its PNG."""

from __future__ import annotations

import asyncio
import io
import zlib

import httpx
import numpy as np
import pytest
from astropy.io import fits
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.extras import cadc_ref
from service.routes_extras import Extras, register

CSV = (
    "collection,publisher_id,band,level,exposure_s,pixel_arcsec\n"
    "CFHTMEGAPIPE,ivo://cadc.nrc.ca/CFHTMEGAPIPE?MegaPipe.339.189/MegaPipe.339.189.R.MP9602,r,3,2401.0,0.186\n"
    "CFHTMEGAPIPE,ivo://cadc.nrc.ca/CFHTMEGAPIPE?MegaPipe.339.189/MegaPipe.339.189.G.MP9401,g,3,480.0,0.186\n"
    "HSTHLA,ivo://cadc.nrc.ca/HSTHLA?x/y,F814W,3,,0.05\n"
)
DATALINK = """<?xml version="1.0"?>
<VOTABLE xmlns="http://www.ivoa.net/xml/VOTable/v1.3" version="1.4"><RESOURCE type="results"><TABLE><DATA><TABLEDATA>
<TR><TD>ID</TD><TD>https://x/raven/files/cadc:CFHTSG/MegaPipe.339.189.R.MP9602.fits.gif</TD><TD/><TD/><TD>#preview</TD></TR>
<TR><TD>ID</TD><TD>https://x/raven/files/cadc:CFHTSG/MegaPipe.339.189.R.MP9602.fits</TD><TD/><TD/><TD>#this</TD></TR>
</TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>"""


def fits_bytes(n: int = 32) -> bytes:
    rng = np.random.default_rng(1)
    img = rng.normal(100.0, 2.0, (n, n)).astype(np.float32)
    img[5, 8] += 400.0  # a star low in the picture (row 5 counted from the bottom)
    buf = io.BytesIO()
    h = fits.PrimaryHDU(img)
    h.header["EXPTIME"] = 2401.0
    h.writeto(buf)
    return buf.getvalue()


def handler(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    if "argus/sync" in u:
        return httpx.Response(200, text=CSV)
    if "caom2ops/datalink" in u:
        assert "MegaPipe" in request.url.params["ID"]
        return httpx.Response(200, text=DATALINK)
    if "caom2ops/sync" in u:
        assert request.url.params["ID"] == "cadc:CFHTSG/MegaPipe.339.189.R.MP9602.fits"
        assert request.url.params["CIRCLE"].split()[2] == "0.008333"
        return httpx.Response(200, content=fits_bytes())
    return httpx.Response(404)


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_the_search_asks_for_deep_stacks_only() -> None:
    q = cadc_ref.search_adql(169.89, 4.35)
    assert "calibrationLevel >= 3" in q and "time_exposure >= 300" in q and "INTERSECTS(POINT('ICRS', 169.890000, 4.350000)" in q


def test_references_are_parsed_deepest_first_and_rows_without_exposure_dropped() -> None:
    refs = cadc_ref.parse_references(CSV)
    assert [r.band for r in refs] == ["r", "g"] and refs[0].pixel_arcsec == pytest.approx(0.186, abs=1e-3)
    with pytest.raises(cadc_ref.CadcError):
        cadc_ref.parse_references("<?xml version='1.0'?><error/>")


def test_datalink_names_the_artifact_of_the_file() -> None:
    assert cadc_ref.artifact_of(DATALINK) == "cadc:CFHTSG/MegaPipe.339.189.R.MP9602.fits"
    with pytest.raises(cadc_ref.CadcError):
        cadc_ref.artifact_of("<VOTABLE><TR><TD>a</TD></TR></VOTABLE>")


def test_cutout_refuses_odd_sizes_and_names() -> None:
    async def go() -> None:
        async with client() as c:
            with pytest.raises(ValueError):
                await cadc_ref.cutout(c, "cadc:A/b.fits", 1.0, 1.0, 5000.0)
            with pytest.raises(ValueError):
                await cadc_ref.cutout(c, "http://evil/x.fits", 1.0, 1.0, 60.0)

    asyncio.run(go())


def test_the_png_is_a_valid_north_up_grey_image() -> None:
    png, facts = cadc_ref.to_png(fits_bytes())
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and facts["width"] == 32 and facts["exptime"] == 2401.0
    # decode the IDAT: the star is at FITS row 5 from the bottom, so row 32 - 1 - 5 = 26 from the top
    idat = png[png.index(b"IDAT") + 4 : png.index(b"IEND") - 8]
    raw = zlib.decompress(idat)
    rows = [np.frombuffer(raw[i * 33 + 1 : (i + 1) * 33], dtype=np.uint8) for i in range(32)]
    assert int(rows[26][8]) > 200 and int(np.median(rows[10])) < 60


def test_the_routes_find_and_cut_out() -> None:
    app = FastAPI()
    extras = register(app, Extras(cadc_client=client()))
    c = TestClient(app)
    r = c.get("/api/v1/reference/deep", params={"ra": 169.89, "dec": 4.35})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "ok" and body["references"][0]["band"] == "r" and body["references"][0]["publisher_id"].startswith("ivo://")
    pid = body["references"][0]["publisher_id"]
    png = c.get("/api/v1/reference/deep/cutout.png", params={"ra": 169.89, "dec": 4.35, "size": 60, "id": pid})
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    raw = c.get("/api/v1/reference/deep/cutout.fits", params={"ra": 169.89, "dec": 4.35, "size": 60, "id": pid})
    assert raw.status_code == 200 and raw.content.startswith(b"SIMPLE")
    assert c.get("/api/v1/reference/deep/cutout.png", params={"ra": 1, "dec": 1, "size": 60, "id": "x"}).status_code == 422
    assert c.get("/api/v1/reference/deep/cutout.png", params={"ra": 1, "dec": 1, "size": 9999, "id": pid}).status_code == 422
    asyncio.run(extras.close())


def test_a_place_without_a_deep_reference_says_none() -> None:
    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="collection,publisher_id,band,level,exposure_s,pixel_arcsec\n")

    app = FastAPI()
    register(app, Extras(cadc_client=httpx.AsyncClient(transport=httpx.MockTransport(empty))))
    r = TestClient(app).get("/api/v1/reference/deep", params={"ra": 269.4479, "dec": 4.7687})
    assert r.json()["state"] == "none" and r.json()["references"] == []


@pytest.mark.network
def test_live_the_ecliptic_field_has_a_deep_cfht_stack() -> None:
    async def go() -> None:
        async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "spherex-sky-test"}) as c:
            refs = await cadc_ref.find(c, 169.89, 4.35)
            assert refs and refs[0].collection == "CFHTMEGAPIPE" and refs[0].exposure_s >= 300

    asyncio.run(go())
