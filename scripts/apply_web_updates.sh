#!/bin/bash
set -euo pipefail

usage() {
    echo "Usage: $0 [<pulsar_directory_or_name> ...]"
}

if [[ -z "${TIMING_API_KEY:-}" ]]; then
    echo "error: TIMING_API_KEY is required"
    exit 2
fi


if [[ -z "${TIMING_WEB_BASE_URL:-}" ]]; then
    echo "error: TIMING_WEB_BASE_URL is required"
    exit 2
fi

state_url="${state_url:-${TIMING_WEB_BASE_URL%/}/api/state.php}"
artifact_url="${artifact_url:-${TIMING_WEB_BASE_URL%/}/api/artifact.php}"
merge_url="${merge_url:-${TIMING_WEB_BASE_URL%/}/api/merge.php}"
pending_pulsars_url="${pending_pulsars_url:-${TIMING_WEB_BASE_URL%/}/api/pulsars_pending_merge.php}"

if [[ -z "$state_url" || -z "$artifact_url" || -z "$merge_url" || -z "$pending_pulsars_url" ]]; then
    echo "error: set TIMING_WEB_BASE_URL"
    exit 2
fi

merged_by="${TIMING_WEB_MERGED_BY:-${USER:-godrevy}}"
pulsar_base_dir="${TIMING_PULSAR_BASE_DIR:-$(pwd)}"

url_encode() {
    python3 - "$1" <<'PY'
import sys
import urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=''))
PY
}

resolve_pulsar_dir() {
    local target="$1"
    if [[ -d "$target" ]]; then
        (cd "$target" && pwd -P)
        return 0
    fi

    local candidate="$pulsar_base_dir/$target"
    if [[ -d "$candidate" ]]; then
        (cd "$candidate" && pwd -P)
        return 0
    fi

    return 1
}

discover_pulsars() {
    local query
    local payload
    query="$pending_pulsars_url?key=$(url_encode "$TIMING_API_KEY")"
    payload="$(curl -fsS "$query")"
    PULSAR_PAYLOAD="$payload" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ['PULSAR_PAYLOAD'])
for row in payload.get('pulsars', []):
    name = str(row.get('pulsar', '')).strip()
    if name:
        print(name)
PY
}

process_pulsar() {
    local pulsar_dir="$1"
    local pulsar_name
    local best_tim
    local log_file
    local marker_dir
    local lock_dir
    local encoded_pulsar
    local state_query
    local state_json
    local parse_result
    local ids
    local run_id
    local artifact_query
    local tmp_tim
    local backup_path
    local new_best
    local marker_file
    local merge_payload
    local merge_query
    local merge_resp

    pulsar_name="$(basename "$pulsar_dir")"
    best_tim="$pulsar_dir/best.tim"
    log_file="${pulsar_dir}/web_merge.log"
    marker_dir="$pulsar_dir/.web_merge_markers"
    mkdir -p "$marker_dir"

    lock_dir="$pulsar_dir/.web_merge.lock"
    if ! mkdir "$lock_dir" 2>/dev/null; then
        echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] skip pulsar=$pulsar_name reason=locked" | tee -a "$log_file"
        return 0
    fi

    cleanup_pulsar() {
        rm -rf "$lock_dir"
        if [[ -n "${tmp_tim:-}" ]]; then
            rm -f "$tmp_tim"
        fi
    }
    trap cleanup_pulsar RETURN

    log_line() {
        local message="$1"
        echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $message" | tee -a "$log_file"
    }

    if [[ ! -f "$best_tim" ]]; then
        log_line "error pulsar=$pulsar_name reason=best_tim_missing path=$best_tim"
        return 1
    fi

    encoded_pulsar="$(url_encode "$pulsar_name")"
    state_query="$state_url?key=$(url_encode "$TIMING_API_KEY")&pulsar=$encoded_pulsar&status=accepted"
    state_json="$(curl -fsS "$state_query")"

    parse_result="$(STATE_JSON="$state_json" python3 - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ['STATE_JSON'])
accepted = payload.get('accepted_runs') or []
if len(accepted) == 0:
    print('NONE')
    sys.exit(0)
if len(accepted) > 1:
    ids = ','.join(str(run.get('run_id', '')) for run in accepted)
    print('MULTIPLE\t' + ids)
    sys.exit(0)
run = accepted[0]
print('ONE\t%s\t%s' % (run.get('run_id', ''), run.get('status', '')))
PY
)"

    if [[ "$parse_result" == "NONE" ]]; then
        log_line "skip pulsar=$pulsar_name reason=no_accepted_run"
        return 0
    fi

    if [[ "$parse_result" == MULTIPLE$'\t'* ]]; then
        ids="${parse_result#MULTIPLE$'\t'}"
        log_line "error pulsar=$pulsar_name reason=multiple_accepted runs=$ids"
        return 1
    fi

    run_id="$(echo "$parse_result" | awk -F'\t' '{print $2}')"
    if [[ -z "$run_id" ]]; then
        log_line "error pulsar=$pulsar_name reason=missing_run_id"
        return 1
    fi

    artifact_query="$artifact_url?key=$(url_encode "$TIMING_API_KEY")&run_id=$(url_encode "$run_id")&type=tim"
    tmp_tim="$(mktemp "$pulsar_dir/.web_accepted_${run_id}.XXXXXX.tim")"
    curl -fsS "$artifact_query" -o "$tmp_tim"
    if [[ ! -s "$tmp_tim" ]]; then
        log_line "error pulsar=$pulsar_name run_id=$run_id reason=empty_download"
        return 1
    fi

    backup_path="$pulsar_dir/best.tim.$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    cp "$best_tim" "$backup_path"

    new_best="$pulsar_dir/.best.tim.new"
    cp "$tmp_tim" "$new_best"
    mv -f "$new_best" "$best_tim"

    marker_file="$marker_dir/merged_${run_id}_$(date -u +'%Y%m%dT%H%M%SZ').txt"
    {
        echo "timestamp_utc=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
        echo "pulsar=$pulsar_name"
        echo "run_id=$run_id"
        echo "best_tim=$best_tim"
        echo "backup=$backup_path"
        echo "source=$artifact_query"
    } > "$marker_file"

    log_line "updated_best_tim pulsar=$pulsar_name run_id=$run_id backup=$backup_path marker=$marker_file"

    merge_payload="$(printf '{"run_id":"%s","merged_by":"%s","merge_note":"%s"}' "$run_id" "$merged_by" "$marker_file")"
    merge_query="$merge_url?key=$(url_encode "$TIMING_API_KEY")"
    merge_resp="$(curl -fsS -X POST -H 'Content-Type: application/json' --data "$merge_payload" "$merge_query")"
    log_line "marked_merged pulsar=$pulsar_name run_id=$run_id response=$(echo "$merge_resp" | tr '\n' ' ')"

    return 0
}

declare -a targets
if [[ $# -gt 0 ]]; then
    targets=("$@")
else
    while IFS= read -r pulsar; do
        [[ -n "$pulsar" ]] && targets+=("$pulsar")
    done < <(discover_pulsars)
fi

if [[ ${#targets[@]} -eq 0 ]]; then
    echo "No pulsars with accepted unmerged runs."
    exit 0
fi

failures=0
for target in "${targets[@]}"; do
    if ! pulsar_dir="$(resolve_pulsar_dir "$target")"; then
        echo "error: pulsar directory not found for target=$target (set TIMING_PULSAR_BASE_DIR if using pulsar names)"
        failures=$((failures + 1))
        continue
    fi

    if ! process_pulsar "$pulsar_dir"; then
        failures=$((failures + 1))
    fi
done

if [[ $failures -gt 0 ]]; then
    exit 1
fi
