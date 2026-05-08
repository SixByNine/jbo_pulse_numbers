#!/bin/bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <pulsar> <run_id>" >&2
    exit 2
fi

pulsar="$1"
run_id="$2"

if [[ -z "${TIMING_API_KEY:-}" ]]; then
    echo "error: TIMING_API_KEY is required" >&2
    exit 2
fi

if [[ -z "${TIMING_WEB_BASE_URL:-}" ]]; then
    echo "error: TIMING_WEB_BASE_URL is required" >&2
    exit 2
fi

url_encode() {
    python3 - "$1" <<'PY'
import sys
import urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=''))
PY
}

import_url="${TIMING_WEB_BASE_URL%/}/api/import.php?key=$(url_encode "$TIMING_API_KEY")"
payload="$(python3 - "$pulsar" "$run_id" <<'PY'
import json
import sys

print(json.dumps({
    'pulsar': sys.argv[1],
    'run_id': sys.argv[2],
}))
PY
)"

response="$(curl -fsS -X POST \
    -H 'Content-Type: application/json' \
    --data "$payload" \
    "$import_url")"

result_summary="$(python3 - "$response" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if not payload.get('ok'):
    raise SystemExit(payload.get('error', 'import_failed'))

state = 'inserted' if payload.get('inserted') else 'skipped'
parts = [state]
outdated = int(payload.get('outdated', 0) or 0)
if outdated > 0:
    parts.append(f'outdated={outdated}')
if payload.get('blocked_by_manual'):
    parts.append(f"blocked_by_manual={payload.get('blocking_manual_run_id') or 'unknown'}")
print(' '.join(parts))
PY
)"

echo "Import result for ${pulsar}/${run_id}: ${result_summary}"
return 0 2>/dev/null || exit 0
