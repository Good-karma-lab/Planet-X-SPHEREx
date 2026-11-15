# GitHub Pages: the presentation site

Static, no build step: the files in `docs/` (index.html, slides.html, validation.html, style.css, img/, media/). Once the repository is public, enable Pages: Settings → Pages → Deploy from a branch → `main`, folder `/docs`.

| File | What |
|---|---|
| `index.html` | home: hero, 30-s video, the 7 slides, one-page summary (what it does, how we validated it, what is new), links, SI line, IRSA acknowledgement |
| `slides.html` | the 7-slide deck: scroll to read; **Present** (or `?present`, key P) for one slide at a time, ← → / space, Esc, F full screen; `#3` opens slide 3 |
| `spherex-sky-slides.pdf` | the same deck, 7 pages 16:9 (re-export: headless Chrome `--print-to-pdf` of `slides.html`) |
| `media/spherex-sky-demo.mp4`, `media/poster.jpg` | **draft video slot**: replace with Sunday's re-recording (same file name); poster = a frame of it |
| `img/` | real screenshots of spherex.space (from `docs/submission/demo/img`) and real SPHEREx frames |

To fill on Sunday: the YouTube link (`#yt` in `index.html`), the new MP4 and poster (`media/`). Everything else is final: the numbers are the
live ones (Gaia motions, 6 of 8 asteroids, chance 0.0, Eris 95.6 ± 0.4 au and Makemake 52.5 ± 0.2 au found blind and linked), and the
screenshots are fresh captures of spherex.space (2026-11-15). `validation.html` is the page of checks; the full account is the live Method
page (https://spherex.space/#/method). No NASA logos; all pictures real data.
