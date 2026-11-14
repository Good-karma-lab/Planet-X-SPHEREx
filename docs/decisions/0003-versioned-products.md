# 0003 — Versioned product keys, automatic purge and re-warm, R2 snapshot

Date: 2026-11-14. Status: accepted.

**Context.** In the research phase one engine version was part of every key and was bumped nine times in two days, throwing away
every computed field; the demo targets were then recomputed while a person waited.

**Decision.** One version per product kind and a declared dependency graph; a key holds the product's own version, the versions of the
products it is computed from and the index cell versions it read. On start the store purges stale versions and the warm runner
re-computes the warm set at the lowest priority. The index and the warm-set products are synced to Cloudflare R2 so a new server is warm
in minutes. CI fails if a product's code changes without a version bump or a bump has no change-log entry.

**Consequences.** Bumping a version is cheap and safe; a version is frozen before a demo.
