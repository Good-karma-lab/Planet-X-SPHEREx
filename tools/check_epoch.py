# ruff: noqa: E501
"""Where do bright Gaia stars (and Barnard's star) stand in the equirect source: at their J2000, 2015.5 (the map's likely epoch) or 2025.9 places?"""

import math
import urllib.parse
import urllib.request

import numpy as np

W, H = 65536, 32768
L7 = np.memmap("eq/L7.f16", dtype=np.float16, mode="r", shape=(H, W, 3))


def simbad(q):
    d = urllib.parse.urlencode({"request": "doQuery", "lang": "adql", "format": "text", "query": q}).encode()
    t = urllib.request.urlopen(urllib.request.Request("https://simbad.harvard.edu/simbad/sim-tap/sync", data=d), timeout=90).read().decode()
    return [[c.strip() for c in ln.split("|")] for ln in t.strip().splitlines()[2:]]


ra0, dec0 = 269.4520, 4.6934  # Barnard, J2000 (17h57m48.5s +04d41m36s)
q = (
    f"SELECT TOP 6 b.main_id, b.ra, b.dec, b.pmra, b.pmdec, f.V AS vmag FROM basic AS b JOIN allfluxes AS f ON f.oidref=b.oid "
    f"WHERE CONTAINS(POINT('ICRS',b.ra,b.dec),CIRCLE('ICRS',{ra0},{dec0},4.0))=1 AND f.V < 5.0 ORDER BY vmag"
)
rows = [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in simbad(q)]
rows = [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows]
brow = [["Barnard", str(ra0), str(dec0), "-797.8", "10326.9", "9.5"]]
print("stars", [(r[0], r[5]) for r in rows])


def px(ra, dec):
    return ra / 360 * W, (90 - dec) / 180 * H


def peak_near(ra, dec, rad=40):
    x, y = px(ra, dec)
    x0, y0 = int(x), int(y)
    win = np.asarray(L7[max(0, y0 - rad) : y0 + rad + 1, x0 - rad : x0 + rad + 1]).astype(np.float32).sum(-1)
    yy, xx = np.unravel_index(win.argmax(), win.shape)
    return win.max(), (x0 - rad + xx) - x, (max(0, y0 - rad) + yy) - y


def at(r, epoch):
    ra, dec, pmra, pmdec = float(r[1]), float(r[2]), float(r[3] or 0), float(r[4] or 0)
    dt = epoch - 2000.0  # SIMBAD's positions are J2000 (epoch 2000)
    d = dec + pmdec * dt / 3.6e6
    return ra + pmra * dt / 3.6e6 / math.cos(math.radians(dec)), d


for r in rows[:4] + brow[:1]:
    print("G", r[5][:5], "id", r[0])
    for ep in (2000.0, 2015.5, 2025.9):
        ra, dec = at(r, ep)
        v, dx, dy = peak_near(ra, dec)
        print(f"   epoch {ep}: expected px offset of the brightest pixel within 40 px: dx {dx:+.0f} dy {dy:+.0f}  peak {v:.3f}")
