#!/usr/bin/env python3

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def parse_args():
    parser = argparse.ArgumentParser(
        description="Query or clear postponed state through the timing review API."
    )
    parser.add_argument("pulsar", nargs="?", help="pulsar name to query or clear")
    parser.add_argument("--clear", action="store_true", help="clear active postponed state")
    parser.add_argument("--run-id", default="", help="run id to resolve pulsar and clear postponed state")
    parser.add_argument("--cleared-by", default=os.environ.get("USER", "cli"), help="actor name for clear action")
    parser.add_argument("--note", default="", help="optional note for clear action")
    return parser.parse_args()


def get_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"error: {name} is required")
    return value


def endpoint_url(base_url, explicit_url, suffix):
    if explicit_url:
        return explicit_url
    if not base_url:
        return ""
    return base_url.rstrip("/") + suffix


def api_request(url, payload=None):
    headers = {}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"error": body or exc.reason}
        error_text = parsed.get("error") if isinstance(parsed, dict) else body
        raise SystemExit(f"error: API request failed ({exc.code}): {error_text}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: API request failed: {exc.reason}")


def resolve_pulsar_from_run_id(state_url, api_key, run_id):
    url = state_url + "?" + urllib.parse.urlencode({"key": api_key})
    payload = api_request(url)
    runs = payload.get("runs") or []
    for run in runs:
        if str(run.get("run_id", "")).strip() == run_id:
            pulsar = str(run.get("pulsar", "")).strip()
            if pulsar:
                return pulsar
            break
    raise SystemExit(f"error: run_id not found: {run_id}")


def query_is_postponed(postponed_url, api_key, pulsar):
    url = postponed_url + "?" + urllib.parse.urlencode({"key": api_key})
    payload = api_request(url, payload={
        "action": "is_postponed",
        "pulsar": pulsar,
    })
    return payload


def query_postponed_list(postponed_url, api_key):
    url = postponed_url + "?" + urllib.parse.urlencode({"key": api_key})
    payload = api_request(url, payload={"action": "list_postponed"})
    return payload.get("postponed_pulsars") or []


def clear_postponed(postponed_url, state_url, api_key, args):
    target_pulsar = (args.pulsar or "").strip()
    run_id = args.run_id.strip()
    if not target_pulsar and run_id:
        target_pulsar = resolve_pulsar_from_run_id(state_url, api_key, run_id)

    if not target_pulsar:
        raise SystemExit("error: --clear requires a pulsar or --run-id")

    payload = {
        "action": "clear_postponed",
        "pulsar": target_pulsar,
        "cleared_by": args.cleared_by,
    }
    if args.note:
        payload["note"] = args.note

    url = postponed_url + "?" + urllib.parse.urlencode({"key": api_key})
    response = api_request(url, payload=payload)
    print(
        "cleared_postponed"
        f" pulsar={response.get('pulsar', target_pulsar)}"
        f" outdated_runs={int(response.get('postponed_runs_marked_outdated', 0) or 0)}"
        f" rules_deleted={int(response.get('pulsar_rules_deleted', 0) or 0)}"
    )
    return 0


def main():
    args = parse_args()
    api_key = get_env("TIMING_API_KEY")
    base_url = os.environ.get("TIMING_WEB_BASE_URL", "").strip()
    state_url = endpoint_url(base_url, os.environ.get("TIMING_WEB_STATE_URL", "").strip(), "/api/state.php")
    postponed_url = endpoint_url(base_url, os.environ.get("TIMING_WEB_POSTPONED_URL", "").strip(), "/api/postponed.php")

    if not state_url or not postponed_url:
        raise SystemExit(
            "error: set TIMING_WEB_BASE_URL or both TIMING_WEB_STATE_URL and TIMING_WEB_POSTPONED_URL"
        )

    if args.clear:
        return clear_postponed(postponed_url, state_url, api_key, args)

    if args.pulsar:
        payload = query_is_postponed(postponed_url, api_key, args.pulsar)
        if payload.get("is_postponed"):
            print(
                "postponed_active"
                f" pulsar={payload.get('pulsar', args.pulsar)}"
                f" postpone_until_utc={payload.get('postpone_until_utc', '')}"
                f" source_run_id={payload.get('source_run_id', '')}"
            )
            return 0
        print(f"postponed_clear pulsar={args.pulsar}")
        return 0

    rows = query_postponed_list(postponed_url, api_key)
    for row in rows:
        print(
            f"{row.get('pulsar', '')}\t"
            f"{row.get('postpone_until_utc', '')}\t"
            f"{row.get('source_run_id', '')}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
