#!/usr/bin/env bash
set -euo pipefail
event="${1:?usage: alert.sh <event>}"
if [[ -z "${AUTONOMO_ALERT_WEBHOOK:-}" ]]; then
  logger -t autonomo-alert "alert webhook missing; event=$event"
  exit 1
fi
payload="{\"event\":\"$event\",\"host\":\"$(hostname)\"}"
curl --fail --silent --show-error --max-time 20 \
  -H 'Content-Type: application/json' \
  --data "$payload" \
  "$AUTONOMO_ALERT_WEBHOOK"
