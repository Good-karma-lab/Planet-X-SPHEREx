# Moving the service to another VM

How to move spherex.space from one VM to another (e.g. from today's trial VM.Standard.E5.Flex to an Always Free A1.Flex) with a few
minutes of read-only time and a way back. The new VM is made by the user (console); everything after that is from a development machine
with `~/.ssh/oci_spherex_ed25519`. `OLD` and `NEW` below are the two VMs' addresses.

## What fits a small machine (2 OCPU / 12 GB A1)

The service sizes itself from the machine (`service/machine.py`; `/api/v1/health` → `machine` says what it chose). On 4 cores or
fewer, or 16 GB or less, it is a small machine:

| | 16 vCPU / 48 GB (today) | 2 OCPU / 12 GB | variable |
|---|---|---|---|
| pool workers | cores (SKY_WORKERS=12 in /etc/sky/env today) | 2 | `SKY_WORKERS` |
| field builds at once | 6 | 2 | `SKY_HEAVY_SLOTS` |
| memory budget of the jobs | 24 GB | 6 GB (a 60′ field peaks at 1.1 GB, a 60′ film at 5 GB) | `SKY_MEMORY_BUDGET_MB` |
| cap of one worker | 6 GB | 4.2 GB | `SKY_WORKER_MEM_MB` |
| background builds at once | 2 | 1 | `SKY_BG_HEAVY` |
| ready quarters kept | 40 | 8 | `SKY_QUARTER_STOCK` |
| the sweep of the grid | on | **off** (a person's field needs both cores) | `SKY_SWEEP=1` turns it on |

Also on a small machine, in `/etc/sky/env`: drop `SKY_WORKERS=12` (let it follow the cores), and give the units room in 12 GB with a
drop-in, `sudo systemctl edit sky-api` and `sudo systemctl edit sky-api-standby`:

```ini
# sky-api: the API, the pool and the jobs (the page cache needs the rest)
[Service]
MemoryMax=8500M
```

```ini
# sky-api-standby: serves finished products while the main one restarts
[Service]
MemoryMax=1500M
```

What a 2-core machine gives up: the sweep (it can be turned on at night, `SKY_SWEEP=1`), two of six parallel field builds (a crowd
waits longer for cold fields), and the 40-quarter stock (8 are kept). A person's own field, the lab, Findings and the accounts work
as before. Measure before relying on it: a cold 10′ field takes about 60 s on today's VM.

## 1. The new VM

The user: create the VM (Ubuntu 24.04, the same SSH key), and open 22, 80 and 443 in the security list. Then, from a dev machine:

```bash
scp -i ~/.ssh/oci_spherex_ed25519 deploy/provision.sh ubuntu@NEW:/tmp/provision.sh
```

```bash
ssh -i ~/.ssh/oci_spherex_ed25519 ubuntu@NEW 'sudo SKY_DOMAIN=spherex.space bash /tmp/provision.sh'
```

Copy the settings (they hold keys: never through the repository):

```bash
ssh -i ~/.ssh/oci_spherex_ed25519 ubuntu@OLD 'sudo cat /etc/sky/env' | ssh -i ~/.ssh/oci_spherex_ed25519 ubuntu@NEW 'sudo install -m 600 /dev/stdin /etc/sky/env'
```

Then the code and the environment, without starting the public site yet:

```bash
deploy/push.sh ubuntu@NEW
```

## 2. The store, while the old VM serves

The store is about 20 GB without the raw S3 cache (`raw/` is a cache and refills itself). A first pass, from the old VM to the new one
(through the dev machine, so no key is copied to a VM):

```bash
ssh -i ~/.ssh/oci_spherex_ed25519 ubuntu@OLD 'sudo tar -C /var/lib/sky --exclude=raw -cf - .' | ssh -i ~/.ssh/oci_spherex_ed25519 ubuntu@NEW 'sudo -u sky tar -C /var/lib/sky -xf -'
```

Run `make lint test` on the new VM's checkout if it is a new architecture (aarch64), and open
`http://NEW/api/v1/health` (Caddy serves the bare address on port 80) to see `machine` and the warm set coming up.

## 3. The switch (a few minutes)

1. Back up the old VM's databases (the deploy procedure's backup: each `.sqlite` by its own path).
2. Stop the old service: `sudo systemctl stop sky-api sky-api-standby` on OLD. From here the site is down.
3. Copy the databases consistently, each by its own path:

```bash
ssh -i ~/.ssh/oci_spherex_ed25519 ubuntu@OLD 'cd /var/lib/sky && sudo tar -cf - product.sqlite findings.db catalogue.sqlite clips/catalogue.sqlite' | ssh -i ~/.ssh/oci_spherex_ed25519 ubuntu@NEW 'sudo -u sky tar -C /var/lib/sky -xf -'
```

4. Start on NEW: `sudo systemctl restart sky-api-standby sky-api`.
5. **The address.** If OLD has a reserved public IP: unassign it from OLD and assign it to NEW in the console; DNS stays. If its IP is
   ephemeral: make it reserved first (free while attached), or change the A records of `spherex.space` and `www.spherex.space` to NEW
   (TTL permitting, a few minutes). Caddy on NEW gets its certificates as soon as the names point at it.
6. Check: `https://www.spherex.space/api/v1/health` shows NEW's `machine`; run the production checks; sign in once with the e-mail
   link; open a lab field.

## 4. The way back

Until the new VM has run a few days, keep OLD **stopped, not deleted**. To go back: stop NEW's service, copy the four databases back
the same way, start OLD, point the address back. Delete OLD only when the user says so (on the trial: before the credits end).
