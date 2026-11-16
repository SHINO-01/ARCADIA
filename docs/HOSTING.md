# Hosting ARCADIA for free

Researched November 2026. Free tiers change often; the numbers below are what the providers
publish now.

## What has to run

| Part | Needs | Notes |
|---|---|---|
| Web stack: Caddy, React, FastAPI, Celery worker and beat, TimescaleDB, Redis | ~2 GB RAM, always on | The world model ticks inside the API process and Celery runs every few seconds, so anything that sleeps when idle stops the farm. |
| World model + headless twin | inside the API | Runs anywhere the web stack runs, on x86-64 or ARM. |
| Webots + ROS 2 (3D physics twin) | x86-64 Linux, OpenGL, ~4 GB RAM, several cores | Webots has no official Linux ARM build. The dashboard falls back to the headless twin when Webots is not there. |

## Options

| Option | Cost | Runs | Verdict |
|---|---|---|---|
| **Your own PC + Cloudflare Tunnel** | free | everything, including Webots | **Best for the full project.** No open ports, https, WebSockets work. `./setup.sh --tunnel` gives a random `*.trycloudflare.com` URL; a named tunnel on your own domain is also free. |
| **Oracle Cloud Always Free (Ampere A1 ARM)** | free, card needed for sign-up | web stack + world model + headless twin | **Best always-on cloud host.** Since 15 June 2026 the free allowance is 2 OCPU / 12 GB RAM (was 4/24) with 200 GB block storage. Enough for the stack; no Webots (ARM). Capacity in popular regions can be scarce. |
| GitHub Codespaces | free 120 core-hours/month (60 h on 2 cores) | web stack + world model | Good for demos and collaborators: `.devcontainer/` runs `setup.sh --cloud`; port 8080 can be made public. Stops when idle. |
| Google Cloud e2-micro (Always Free) | free in us-west1/central1/east1 | too small | 1 GB RAM cannot hold TimescaleDB, the API, two Celery processes and the model. |
| Render free | free | too limited | 512 MB / 0.1 CPU services that sleep after 15 min, no free workers, free Postgres expires after 30 days. |
| Split PaaS (Cloudflare Pages + Neon + Upstash + a free API host) | free | no | Telemetry and world updates would use Upstash's 500k commands/month in hours. Neon compute sleeps after 5 min idle, and a Celery worker has no free home. |
| Fly.io, Koyeb, Hugging Face Spaces | not free | - | Fly.io now offers only a short trial. Koyeb needs a paid plan for new users since Feb 2026. Docker Spaces on Hugging Face need PRO. |

## Recipe A: full project from your PC (free)

```bash
./setup.sh --full --tunnel        # web stack + ROS 2 + Webots + public https URL
make sim                          # in a second terminal: Webots + the robot fleet
```

The printed `https://<words>.trycloudflare.com` URL serves the dashboard, the API, the live
telemetry socket and the 3D stream. Caddy proxies `/webots-stream` to the native simulator, so the
3D view works remotely. The quick-tunnel URL changes on every restart. For a stable address:

1. Add a domain to Cloudflare (the free plan is enough).
2. In Zero Trust > Networks > Tunnels, create a tunnel with public hostname `farm.example.com` pointing to service `http://caddy:80`.
3. Run `./setup.sh --tunnel-token <token>`.

## Recipe B: always-on cloud demo on Oracle Always Free (free)

1. Create an **Ampere A1** instance (2 OCPU, 12 GB) with Ubuntu 24.04 arm64.
2. On it, run this one line (see the note on private repos below):

   ```bash
   git clone https://github.com/SHINO-01/ARCADIA.git && ARCADIA/setup.sh --cloud --tunnel --yes
   ```

   `--cloud` runs the headless twin and keeps the databases on localhost. `--tunnel` publishes
   through Cloudflare, so no ingress rules or open ports are needed in the Oracle security list.
3. Optional: `--demo` creates, analyses, lays out and activates a demo farm.

All images used are multi-architecture (python:3.12-slim, node:22-alpine, caddy, redis,
timescaledb, cloudflared publish arm64). The stack restarts with Docker on reboot
(`restart: unless-stopped`).

## Private repository note

The repository is private, so `curl .../setup.sh | bash` only works once it is public or with
a token. On a server, clone with a GitHub token or `gh auth login`, then run `ARCADIA/setup.sh`.

## Sources

- Oracle Always Free resources: https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm
- Oracle A1 cut to 2 OCPU / 12 GB (June 2026): https://www.infoq.com/news/2026/07/oracle-cloud-free-tier-limits/
- Cloudflare Quick Tunnels: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/
- Cloudflare Tunnel free and unmetered: https://bex.co/blog/2026/07/28/cloudflare-tunnel-free-zero-open-ports-ingress
- GitHub Codespaces included usage: https://docs.github.com/en/billing/concepts/product-billing/github-codespaces
- Google Cloud free tier e2-micro: https://www.cloudapicredits.com/guides/google-cloud-free-tier/
- Render free tier: https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026
- Neon free plan: https://neon.com/blog/how-to-make-the-most-of-neons-free-plan
- Upstash free tier: https://upstash.com/blog/upstash-vs-redis-cloud-a-2026-comparison
- Fly.io free tier status: https://www.saaspricepulse.com/blog/flyio-free-tier-2026
- Koyeb plan change (Feb 2026): https://www.srvrlss.io/provider/koyeb/
- Hugging Face Spaces: https://www.eesel.ai/blog/hugging-face-pricing
- Webots on Linux arm64: https://github.com/cyberbotics/webots/discussions/6079
