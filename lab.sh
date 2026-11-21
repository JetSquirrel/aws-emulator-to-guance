#!/usr/bin/env bash
# Run the whole order lab: pricing-service + shipping-worker in the background,
# order-api in the foreground. Ctrl-C stops all three.
#   ./lab.sh                                   # defaults: 1 order/s, 15% errors, 10% slow
#   ./lab.sh --interval 0.5 --error-rate 0.3   # any order_api.py flags
set -euo pipefail
cd "$(dirname "$0")"
args=("$@"); [[ ${#args[@]} -eq 0 ]] && args=(--loop --interval 1 --error-rate 0.15 --slow-rate 0.1)
pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM
./run.sh python pricing_service.py & pids+=($!)
./run.sh python shipping_worker.py & pids+=($!)
for i in $(seq 20); do curl -sf "http://127.0.0.1:8081/price?item=book" >/dev/null && break; sleep 0.5; done
./run.sh python order_api.py "${args[@]}"
