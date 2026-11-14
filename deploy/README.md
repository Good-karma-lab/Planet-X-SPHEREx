# Deployment

Target (docs/plan/05-architecture.md 5.10): one Oracle Cloud Always Free Ampere A1 VM in US East (Ashburn), next to the bucket's
region; Cloudflare in front through a Tunnel (no inbound HTTP port); Cloudflare R2 holds a snapshot of the index and the warm set.

## What the user does once (accounts and secrets are theirs)

1. Oracle Cloud account (upgraded to pay-as-you-go to avoid idle reclamation; still free within the allowance). Create an Ampere A1
   instance, 2 OCPU / 12 GB, Ubuntu 24.04 aarch64, with a 200 GB block volume, in US East (Ashburn). Allow SSH from the admin address only.
2. Cloudflare account: a domain (or a Cloudflare-provided hostname), a Tunnel (Zero Trust → Networks → Tunnels), its token.
3. Cloudflare R2: a bucket `sky-warm` and an API token with read/write on it.

## On the VM

```bash
sudo bash deploy/provision.sh
```

```bash
sudo -u sky /opt/sky/deploy/restore.sh
```

`provision.sh` is idempotent: the `sky` user, packages, the firewall, `uv` and Node, the repository in `/opt/sky`, the store in `/var/lib/sky`,
the systemd units and `cloudflared`. Secrets go to `/etc/sky/env` (root-only, never in the repository):

```
CLOUDFLARE_TUNNEL_TOKEN=...
RCLONE_CONFIG_R2_TYPE=s3
RCLONE_CONFIG_R2_PROVIDER=Cloudflare
RCLONE_CONFIG_R2_ACCESS_KEY_ID=...
RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=...
RCLONE_CONFIG_R2_ENDPOINT=https://<account>.r2.cloudflarestorage.com
SKY_WORKERS=2
```

## Units

| Unit | What |
|---|---|
| `sky-api.service` | the API and the warm runner (uvicorn on 127.0.0.1:8780; drains 25 s on stop) |
| `sky-index.timer` → `sky-index.service` | daily: new days of the exposure index, reconciliation with the bucket |
| `sky-snapshot.timer` → `sky-snapshot.service` | daily and after the warm set completes: index + pinned products to R2 |
| `cloudflared.service` | the Tunnel to Cloudflare |

## Release

```bash
sudo -u sky /opt/sky/deploy/release.sh
```

Pull, sync dependencies, build the web app, restart the API (the store and the index stay). A product version bump purges and re-warms
by itself; watch `/api/v1/health` → `warm` before a demo.
