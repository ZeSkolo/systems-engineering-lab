# 4. Deploy a Private Meta-Search Engine

Inspired by *NetworkChuck* (self-hosted SearXNG). This is infrastructure: you do not write a search index — you orchestrate engines, strip trackers, cache rate-limits, and terminate TLS at the edge.

## Architecture

```
browser --TLS--> nginx/caddy  (no X-Real-IP forwarded)
                     |
                     v
                 SearXNG  ----parallel----> Google / Bing / Brave / DDG / Wiki / ...
                     |
                     +--> Redis (limiter + short-lived cache)
                     v
            Tracker URL remover + hostnames plugin + re-rank
```

| Layer | Files |
| --- | --- |
| Compose | `docker-compose.yml` — SearXNG + Redis + nginx (Caddy optional) |
| Engine config | `searxng/settings.yml`, `limiter.toml` |
| Edge | `nginx/default.conf`, `caddy/Caddyfile` |
| Hardening | `scripts/harden.sh` |
| Aggregation lab | `aggregator/metasearch.py` (runs without Docker) |

## Run the aggregator (no Docker required)

```bash
python3 aggregator/metasearch.py "B+ tree" --offline
python3 aggregator/metasearch.py "ReAct prompting"
```

## Deploy SearXNG

```bash
cp .env.example .env
# put certs in ./certs/fullchain.pem ./certs/privkey.pem
# change server.secret_key in searxng/settings.yml
docker compose up -d
# or: docker compose --profile caddy up -d
```

DNS: A/AAAA for `search.example.com` → this host. Only 22/80/443 should be open.

## What you learn

Production search privacy is request hygiene: drop client IPs before they reach crawlers, strip `utm_*` / `gclid` / `fbclid`, terminate TLS at a reverse proxy, and rate-limit at Redis so a shared instance cannot be used as an open proxy.
