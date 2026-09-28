#!/usr/bin/env bash
# Week-0 latency check (plan B): run from Mosul (or wherever users are) to compare regions.
#   ./deploy/rtt.sh [samples]
# Prints median TCP connect, TLS handshake and total time per endpoint.
set -euo pipefail
N="${1:-10}"
TARGETS=(
  "https://storage.eu-north1.nebius.cloud"
  "https://storage.eu-west1.nebius.cloud"
  "https://api.tokenfactory.nebius.com/v1/models"
)
printf "%-48s %10s %10s %10s\n" endpoint connect_ms tls_ms total_ms
for url in "${TARGETS[@]}"; do
  for _ in $(seq "$N"); do
    curl -s -o /dev/null -w "%{time_connect} %{time_appconnect} %{time_total}\n" --max-time 10 "$url" || echo "nan nan nan"
  done | python3 -c '
import sys, statistics
rows = [list(map(float, l.split())) for l in sys.stdin if "nan" not in l]
med = [statistics.median(c) * 1000 for c in zip(*rows)] if rows else [float("nan")] * 3
print("%.0f %.0f %.0f" % tuple(med))' | { read c t a; printf "%-48s %10s %10s %10s\n" "$url" "$c" "$t" "$a"; }
done
