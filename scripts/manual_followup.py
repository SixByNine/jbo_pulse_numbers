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
        description="Query or clear manual follow-up state through the timing review API."
    )
    parser.add_argument("pulsar", nargs="?", help="pulsar name to query or clear")
    parser.add_argument("--clear", action="store_true", help="clear an active manual follow-up block")
    parser.add_argument("--run-id", default="", help="manual run id to clear explicitly")
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


def query_manual_runs(state_url, api_key, pulsar=None):
    params = {"key": api_key}
    if pulsar:
        params["pulsar"] = pulsar
        params["status"] = "manual"
    else:
        params["manual_only"] = "1"
    url = state_url + "?" + urllib.parse.urlencode(params)
    payload = api_request(url)
    runs = payload.get("manual_runs") or payload.get("runs") or []
    return payload, runs


def clear_manual(manual_url, api_key, args):
    payload = {
        "action": "clear_manual",
        "cleared_by": args.cleared_by,
    }
    if args.note:
        payload["note"] = args.note
    if args.run_id:
        payload["run_id"] = args.run_id
    elif args.pulsar:
        payload["pulsar"] = args.pulsar
    else:
        raise SystemExit("error: --clear requires a pulsar or --run-id")

    url = manual_url + "?" + urllib.parse.urlencode({"key": api_key})
    response = api_request(url, payload=payload)
    print(
        "cleared_manual"
        f" pulsar={response.get('pulsar', '')}"
        f" run_id={response.get('run_id', '')}"
        f" status={response.get('status', '')}"
    )
    return 0


def main():
    args = parse_args()
    api_key = get_env("TIMING_API_KEY")
    base_url = os.environ.get("TIMING_WEB_BASE_URL", "").strip()
    state_url = endpoint_url(base_url, os.environ.get("TIMING_WEB_STATE_URL", "").strip(), "/api/state.php")
    manual_url = endpoint_url(base_url, os.environ.get("TIMING_WEB_MANUAL_URL", "").strip(), "/api/manual.php")

    if not state_url or not manual_url:
        raise SystemExit(
            "error: set TIMING_WEB_BASE_URL or both TIMING_WEB_STATE_URL and TIMING_WEB_MANUAL_URL"
        )

    if args.clear:
        return clear_manual(manual_url, api_key, args)

    payload, runs = query_manual_runs(state_url, api_key, args.pulsar)
    if args.pulsar:
        if runs:
            run = runs[0]
            print(
                "manual_active"
                f" pulsar={run.get('pulsar', '')}"
                f" run_id={run.get('run_id', '')}"
                f" decision_by={run.get('decision_by', '')}"
            )
            return 0
        print(f"manual_clear pulsar={args.pulsar}")
        return 0

    manual_pulsars = payload.get("manual_pulsars") or []
    for row in manual_pulsars:
        print(f"{row.get('pulsar', '')}\t{row.get('run_id', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())