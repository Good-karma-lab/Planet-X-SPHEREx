# nasa-extras: state (2026-11-14)

Built and tested (ruff, mypy strict, pytest 166 pass): cube spectrum, xmatch (AllWISE + Gaia), CADC deep references, ADES export,
Gaia DR4 readiness, tracked stack + injection, SSOIS, R Dra regression field. One wiring line enables all routes:
`from service import routes_extras` and `routes_extras.register(app)` in `create_app`.

Open:
- Tracked stack (`/api/v1/film/{key}/tracked`) has only run on synthetic noise: try it on the Dec 2025 ecliptic film.
- ZTF DR24 / NEOWISE Parquet and Euclid DR1 rows not built (30-124 s per partition from a laptop; re-measure in the cloud region).
- ADES: needs real submitter/observer names and astCat before a file is ready; SPHEREx position comes from Horizons (-163182).
- On 2026-12-02 run GET /api/v1/gaia/readiness; if ready, set SKY_GAIA_TABLE/EPOCH/NAME as it says.
