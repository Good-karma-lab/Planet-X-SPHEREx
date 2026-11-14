# Search UI components

Self-contained pieces for the lab and the globe. Each fetches its own data from the sky service, renders nothing while its route (or product) is absent, and takes plain props; the owner of the screen mounts it and decides what the callbacks do. Import from `./search`.

| piece | exports | route(s) it reads | mounted in |
|---|---|---|---|
| context rows | `ContextRows`, `useContext` | `GET /api/v1/context?ra&dec&epoch&radius` (each catalogue `{state: ok|none|unavailable}`) | object / anomaly passport |
| found tracks of a film (the Days scale) | `useFilmTracks`, `FilmTracks` (`compact` under the timeline), `daysStatus`, `visitMarks`, `drawTracks`, `drawBlink` | `GET /api/v1/film/<key>/tracks` → `{state, phase, tracks[], control}`; track `pts[{f,x,y}]` in the film's grid (index = f·n·n + y·n + x, row 0 north) | under the film; draw on the film's overlay |
| pair card | `PairCard`, `PairButton` | pair objects from `GET /api/v1/movers` (`pairs[]`, `chance_pairs`, `mjd[]`) | find card |
| Find Planet X (distance search) | `useDistant`, `DistantPanel`, `DistantCanvas`, `peakRows` | `GET /api/v1/distant?ra&dec&fov&pass|mjd&dmin&dmax` (job; `result.bin_url`) | side panel + canvas over the picture |
| Find Planet X · deep sum | `DeepSumPanel` | `GET /api/v1/deep?…&plan=1` (plan), `…&fields=…` (job) | side panel |
| Find Planet X · link and jump | `LinkPanel`, `LinkCard`, `JumpView` | `GET /api/v1/link?keys=a,b[,c]`, `GET /api/v1/jump?key&i&mjd&corridor` | side panel |
| globe: comets/asteroids | `KnownList`, `fetchBodyPath`, `drawBodyPath` |
| globe: moving catalogue objects | `drawMovingPath`, `placeAt`, `BACKDROP_YEAR` | none (proper motion from the catalogue row; `imageYears` from the archive image dates) | globe/map draw loop | `GET /api/v1/known`, `/api/v1/ephem`, `/api/v1/known/crossings` | globe side panel + draw loop |

Gates: the distance, deep, link and jump pieces appear only when `GET /api/v1/health` lists the product (`product_versions.distant | deep | link`); nothing is asked of a search route just to find out. Jobs are asked again every 2–3 s and show the phase, "n of m", the time so far and the time left (the service's `estimate_s`, else from the rate).

Status lines carry the honest numbers (blind limit about V 20–21; Planet Nine margin 1.4–1.7× only in D4); nothing is marked unless a search found it.
