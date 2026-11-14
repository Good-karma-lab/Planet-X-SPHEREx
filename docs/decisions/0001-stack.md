# 0001 — Python engine and API, TypeScript + Preact web app, WebGL2 renderer

Date: 2026-11-14. Status: accepted.

**Context.** Every algorithm of the method was worked out and measured in Python (astropy, numpy, scipy). The interface must switch
views, bands and passes without a request and show float16 stacks as moving pictures.

**Decision.** Server: Python 3.12; `engine` holds pure functions on arrays; hot loops vectorised with NumPy or compiled with Numba;
HTTP by FastAPI on uvicorn; compute in recycled worker processes. Browser: TypeScript built by Vite, Preact for panels, a hand-written
WebGL2 renderer over `R16F` texture arrays, no other runtime dependency. Tests: pytest (+ hypothesis), Vitest, Playwright in Chromium.

**Consequences.** One language for the method and its service; typed API contracts; a small web bundle. Numba ties the Python version
to the versions Numba supports (3.12 chosen for that reason).
