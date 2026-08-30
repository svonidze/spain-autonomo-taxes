#!/usr/bin/env bash
# Record the alert durably first, then deliver it when a channel is configured.
# The webhook is optional, so a missing channel must still leave a readable trace
# instead of failing this unit and making its own failure the only signal.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/lib.sh
source "$script_dir/lib.sh"
event="${1:?usage: alert.sh <event>}"
if [[ -f "$(runtime_env_path)" ]]; then load_runtime_env; fi

delivery=skipped
status=0
if [[ -n "${AUTONOMO_ALERT_WEBHOOK:-}" ]]; then
  payload="{\"event\":\"$event\",\"host\":\"$(hostname)\"}"
  if curl --fail --silent --show-error --max-time 20 \
    -H 'Content-Type: application/json' \
    --data "$payload" \
    "$AUTONOMO_ALERT_WEBHOOK" >/dev/null; then
    delivery=delivered
  else
    delivery=failed
    status=1
  fi
fi

logger -t autonomo-alert "event=$event delivery=$delivery" || true
record_state last-alert.json "event=$event" "delivery=$delivery" "host=$(hostname)"
exit "$status"
