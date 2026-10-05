#!/usr/bin/env bash
# Minimal Linux edge hardening checklist for a SearXNG box.
set -euo pipefail

echo "== ufw =="
if command -v ufw >/dev/null; then
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
fi

echo "== sysctl =="
cat >/etc/sysctl.d/99-metasearch.conf <<'EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.accept_source_route = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
EOF
sysctl --system >/dev/null || true

echo "== docker =="
if ! command -v docker >/dev/null; then
  echo "Install Docker Engine, then: docker compose up -d"
fi

echo "Put TLS material in ./certs/fullchain.pem and ./certs/privkey.pem"
echo "Set a long server.secret_key in searxng/settings.yml"
echo "Point DNS A/AAAA for search.example.com at this host."
