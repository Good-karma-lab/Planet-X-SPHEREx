# 0002 — Priority classes: a person's work always goes first

Date: 2026-11-14. Status: accepted.

**Context.** In the research phase background work (the warm-up, abandoned jobs) held the places a person's spectrum needed, and the
person waited; polling was refused with HTTP 429.

**Decision.** Four classes: P0 a job a person watches, P1 a person's background work, P2 the warm runner, P3 maintenance. Lower classes
pause at checkpoints while a higher class needs the CPU, the reads or the memory. Small jobs have their own slots. Polling is never
charged or refused. A job a person leaves is abandoned (keeps running at P1), not cancelled. The background marker is signed by the
server so a client cannot set it.

**Consequences.** Every long job needs checkpoints and an estimate of its memory; the scheduler is part of the service core (M4).
