# Deployment

Production: https://spherex.space. One Oracle Cloud VM in Madrid (eu-madrid-1), Ubuntu 24.04; today VM.Standard.E5.Flex (x86-64,
8 OCPU / 16 vCPU, 48 GB, 193 GB disk; the trial), later an Ampere A1 (aarch64). Everything here works on both architectures, and the
service sizes itself from the machine (`service/machine.py`). Moving to another VM: `deploy/MIGRATE.md`. Caddy terminates HTTPS
(automatic certificates for the domain) and proxies to the API on 127.0.0.1:8780. The SPHEREx bucket is in us-east-1: one range read
from Madrid takes about 1 s, so the reader keeps many reads in flight.

## Once, on a fresh VM

The user creates the VM, opens 22, 80 and 443 in the VCN security list and points the domain's A record at the VM. Then, from a
development machine:

```bash
scp -i ~/.ssh/oci_spherex_ed25519 deploy/provision.sh ubuntu@HOST:/tmp/provision.sh
```

```bash
ssh -i ~/.ssh/oci_spherex_ed25519 ubuntu@HOST 'sudo SKY_DOMAIN=spherex.space bash /tmp/provision.sh'
```

```bash
deploy/push.sh ubuntu@HOST --index
```

The globe's baked tiles live in `/var/www/spherex-tiles` and are served by Caddy at `/tiles/*` (immutable cache, any origin); the route is part of the Caddyfile that `provision.sh` writes, and `push.sh` / `release.sh` never touch the Caddyfile.

`provision.sh` is idempotent: packages, Caddy and its Caddyfile, ports 80/443 in iptables (Oracle's images keep their rules there; ufw
is not used), the `sky` user, `uv`, the store in `/var/lib/sky` and the systemd units. `push.sh --index` also copies a consistent
snapshot of the local exposure index (about 0.9 GB), so the VM does not rebuild it from IRSA.

Settings live in `/etc/sky/env` (root-only; never in the repository), e.g. `SKY_WORKERS=3`.

## Release

```bash
deploy/push.sh ubuntu@HOST
```

Builds the web app locally (the same on every architecture), copies the code, syncs the Python environment on the VM, restarts the
API. The store (index, products) stays. A product version bump purges and re-warms by itself; watch `/api/v1/health` → `warm`.

## Units

| Unit | What |
|---|---|
| `sky-api.service` | the API and the warm runner (uvicorn on 127.0.0.1:8780; drains 25 s on stop) |
| `sky-index.timer` → `sky-index.service` | daily: new days of the exposure index, reconciliation with the bucket |
| `caddy.service` | HTTPS for the domain and plain HTTP on the bare address |
| `sky-snapshot.*`, `sky-tunnel.service` | optional: snapshot to object storage; a Cloudflare Tunnel instead of open ports |
