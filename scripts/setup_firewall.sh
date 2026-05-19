#!/usr/bin/env sh
# Firewall setup for dental-bot on VPS
# Run: sudo sh scripts/setup_firewall.sh

set -eu

echo "=== Configuring UFW ==="

# Allow SSH first — never lock yourself out
ufw allow 22/tcp comment 'SSH'

# Allow HTTP and HTTPS for Caddy
ufw allow 80/tcp comment 'Caddy HTTP'
ufw allow 443/tcp comment 'Caddy HTTPS'

# Enable UFW
ufw --force enable

echo ""
echo "=== UFW status ==="
ufw status numbered

echo ""
echo "=== Listening ports ==="
ss -tlnp | grep -E ':22|:80|:443|:8000' || true

echo ""
echo "=== Done ==="
echo "22 (SSH), 80 (HTTP), 443 (HTTPS) are open."
echo "8000 is internal (127.0.0.1 only) — not exposed to internet."
