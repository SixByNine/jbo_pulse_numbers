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
        description="Log an unrecoverable processing error for a pulsar run through the timing review API."
    )
    parser.add_argument("pulsar", help="pulsar name")
    parser.add_argument("run_id", help="run identifier")
    parser.add_argument("message", help="error message describing the failure")
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


def main():
    args = parse_args()
    api_key = get_env("TIMING_API_KEY")
    base_url = os.environ.get("TIMING_WEB_BASE_URL", "").strip()
    error_url = endpoint_url(base_url, os.environ.get("TIMING_WEB_ERROR_URL", "").strip(), "/api/error.php")

    if not error_url:
        raise SystemExit(
            "error: set TIMING_WEB_BASE_URL or TIMING_WEB_ERROR_URL"
        )

    url = error_url + "?" + urllib.parse.urlencode({"key": api_key})
    response = api_request(url, payload={
        "pulsar": args.pulsar,
        "run_id": args.run_id,
        "message": args.message,
    })

    if not isinstance(response, dict) or not response.get("ok"):
        raise SystemExit(f"error: unexpected response: {response}")

    print(
        "error_logged"
        f" pulsar={response.get('pulsar', '')}"
        f" run_id={response.get('run_id', '')}"
        f" status={response.get('status', '')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
