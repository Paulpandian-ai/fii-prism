#!/usr/bin/env bash
# Post-deploy smoke test.
#   ./scripts/smoke.sh https://api.dev.fii-prism.example.com
# Exits non-zero on any failure so the deploy job fails fast.
set -euo pipefail

API_BASE="${1:-${NEXT_PUBLIC_API_BASE_URL:-http://localhost:8000}}"
SYMBOL="${SMOKE_SYMBOL:-AAPL}"

log() { printf '\n[smoke] %s\n' "$*"; }

log "1/4 GET ${API_BASE}/health"
curl -fsS "${API_BASE}/health" >/dev/null

log "2/4 GET ${API_BASE}/admin/cost-cap"
curl -fsS "${API_BASE}/admin/cost-cap" >/dev/null

log "3/4 POST ${API_BASE}/analyses (quick_refresh ${SYMBOL})"
RESP=$(curl -fsS -X POST "${API_BASE}/analyses" \
  -H 'Content-Type: application/json' \
  -d "{\"symbol\": \"${SYMBOL}\", \"analysis_type\": \"quick_refresh\", \"event_type\": \"news_shock\"}")
ANALYSIS_ID=$(printf '%s' "${RESP}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["analysis_id"])')
echo "    analysis_id=${ANALYSIS_ID}"

log "4/4 polling for succeeded (90s budget)"
for i in $(seq 1 45); do
  STATUS=$(curl -fsS "${API_BASE}/analyses/${ANALYSIS_ID}" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("status","?"))')
  if [ "${STATUS}" = "succeeded" ]; then
    echo "    ✓ succeeded after ${i} polls"
    exit 0
  fi
  sleep 2
done

echo "[smoke] FAIL: analysis ${ANALYSIS_ID} did not reach 'succeeded' in 90s"
exit 1
