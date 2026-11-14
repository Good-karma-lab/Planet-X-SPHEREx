# 2.7 Validation targets

The numbers the running system must reproduce. They become the automated validation suite (`validation/`): each row is one check with a tolerance; the suite runs against the live archive on demand and nightly on the server, and
writes a dated report. "Target" is the value measured when the method was worked out; the tolerance is a
judgement and the report always states the value obtained. A row that cannot be met is a failure until its cause is found.

| # | Check | Target | Tolerance |
|---|---|---|---|
| V1 | wavelength of a pixel vs IRSA's tool (ε Indi B, 123 points) | median +0.00003 µm | ≤ 0.0005 µm |
| V2 | PSF identity across files per (detector, release) | 12 of 12 pairs identical | all identical or per-file fallback logged |
| V3 | exposures overlapping Barnard 5′ / 60′; containing the centre at 5′ | 384 / 603; 361 | exact for the same index snapshot |
| V4 | Barnard's Star 7′, three passes: motion | 10.40 ± 0.13 ″/yr, PA 353° (Gaia 10.39, 356°) | ±0.2 ″/yr, ±3° |
| V5 | registration residual | 0.03–0.05″ (Barnard), 0.04–0.10″ (ε Indi) | ≤ 0.1″ |
| V6 | white-frame detectors | Barnard D1–D6; ε Indi B D2, D3, D5, D6 | exact |
| V7 | ε Indi B motion; shift first to last | 4.55–4.58 ″/yr; 6.47″ over 1.41 yr | ±0.1 ″/yr |
| V8 | cosmic-ray test | Barnard narrow 165–195 px (D6 most); ε Indi B 800–934; no star core rejected | ±30 %; zero cores |
| V9 | per-visit test | ε Indi B ~5,700–5,800 px, ~76–80 % D5/D6; Barnard 321–477 | ±30 % |
| V10 | sharpness: FWHM of isolated unsaturated stars, white frame | 8.8–9.2″ (5′–20′) | ≤ 9.5″ |
| V11 | weight lattice in any stack | none above 3 % | none above 3 % |
| V12 | 60′ Barnard coverage by all six detectors in all passes | ~96 % | ±2 % |
| V13 | streaming build vs in-memory build | 1e-4 MJy/sr | ≤ 1e-3 |
| V14 | Difference: width of z on empty sky, white frame, every pass of Barnard and ε Indi B | 0.93–1.05 | 0.9–1.1 |
| V15 | wall of detectors agreement | Barnard 6/6 (9.7–10.4″); ε Indi B 6/6 (D2 6.30, D3 6.36, D5 6.56, D6 6.52″); RW Aur 6/6; WISE 0855 1/6; R Dra 3/6 | same counts; shifts ±0.3″ |
| V16 | marker within 0.3 px of the core for the five targets at every size; Gaia-star offset | ≤ 0.3 px; median ≤ 0.13 px | same |
| V17 | colour: field stars white, every pass of the five targets at 10′/20′ | within ±0.1 | same |
| V18 | Gaia completeness of the source list (G < 16, 3″) | Barnard 5′ 75 %, 10′ 88 %, 20′ 89 % | ≥ these −5 % |
| V19 | spectrum of 2MASS J17572542+0446559 | ~347–362 points; J 0.98, H 0.99, Ks 1.02–1.03, W1 0.98; scatter 1–4 % | ratios ±0.03 |
| V20 | ε Indi B spectrum | 414–421 points; J 0.91–0.92, H 0.93–0.94, Ks 0.98–0.99, W1 1.18–1.20; peaks 1.3, 1.6, 2.1, 4 µm | ratios ±0.04 |
| V21 | WISE 0855 binned | detected 4.0–4.5 µm 0.40 mJy (8σ), 4.5–5.1 µm 0.48 mJy (11σ); limits elsewhere | ±20 % |
| V22 | thermal ratio | field stars 0.23; ε Indi B 2.08 ± 0.13 | ±0.03; ±0.3 |
| V23 | live spectrum timing on a warm field | first points 0.8–1.9 s; complete 3–15 s | first points ≤ 3 s |
| V24 | search between passes, Barnard 20′ | ~343–470 sources/pass, **one track** 9.98–10.46 ″/yr, PA 352–353°, Gaia `same` | one track |
| V25 | ε Indi 20′ | two tracks: B 4.52 ″/yr (`none`), A saturated (`same`) | two tracks |
| V26 | RW Aurigae 10′/20′ | no mover; one pair (5.7″, ratio 0.67 → 0.85 → 0.96) | same verdict |
| V27 | chance control between passes | < 5 s per field; mean chance tracks reported | time ≤ 5 s |
| V28 | ecliptic 60′, Dec 2025 film search | 4 of 4 known bodies ((9952), (9078), (11020), (43959)); control 0; chance 0.0 for ≥ 4 visits | 4 of 4, 0 false |
| V29 | Barnard, ε Indi B, WISE 0855, RW Aur 60′ films | 0 tracks | 0 drawn tracks |
| V30 | Barnard field, search inside a pass (narrow) | no accepted track | 0 |
| V31 | eight known bodies inside a visit | 7 of 8 | ≥ 7 of 8 |
| V32 | no published fast body on Barnard / RW Aur / ε Indi B 10′ | 0 | 0 |
| V33 | anomaly map ε Indi 20′ | ε Indi B first, score 11.4–11.8 vs next 3.0 | first by > 3× |
| V34 | Eris one pass ×3 (18′, 30–300 au) | 12.0 / 13.3 / 13.5σ vs ceilings 6.7 / 8.0 / 6.5σ; intervals contain 95.5 au; track within 0.5–6″ of JPL | found 3/3 |
| V35 | Eris deep sum | 20.3σ; 0.100 ± 0.005 mJy; 95.5 au | found; d ±2 % |
| V36 | Sedna deep sum (75–92 au) | 6.6σ under 8.1σ ceiling; 0.031 ± 0.003 mJy, 9.1σ vs threshold 6.8σ; 82.9 au; control 0 of 8 | found as the one lead; d ±3 % |
| V37 | link of passes | Eris 95.89 ± 0.54 au; Makemake 52.38 ± 0.23 au | ±1 % |
| V38 | D4 blind limit with artificial bodies | 0.039 clean / 0.047 ecliptic / 0.042 prior (mJy) | ±15 % |
| V39 | WISE archive check of the 394 au candidate | −0.0005 ± 0.0031 mJy; would be 13.6σ | ruled out |
| V40 | warm opening of each test target at every size, every view | ≤ 1.87 s (all < 2 s) | < 2 s from the globe |
| V41 | view switch Picture → walls → Picture, band/detector changes, tab changes | 0 new requests | 0 |
