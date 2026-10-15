#!/usr/bin/env python3
"""
Quick smoke checks for MajorDoMo API endpoints used by MCP.

Usage:
  python smoke_check.py --base-url http://127.0.0.1 --object ThisComputer --property uptime
  python smoke_check.py --base-url http://127.0.0.1 --object SomeObject --property status --set-test --set-value 1
"""

import argparse
import sys
import requests


def check(resp, name):
    ok = resp is not None and resp.status_code == 200
    status = resp.status_code if resp is not None else "N/A"
    print(f"[{'OK' if ok else 'FAIL'}] {name} (status={status})")
    return ok


def extract_data_value(resp):
    try:
        payload = resp.json()
    except Exception:
        return None
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1")
    parser.add_argument("--object", default="ThisComputer")
    parser.add_argument("--property", default="uptime")
    parser.add_argument("--set-test", action="store_true", help="Run POST /api/data/... write test with rollback")
    parser.add_argument("--set-value", default="1", help="Temporary value for --set-test")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    prop_path = f"{args.object}.{args.property}"
    checks_ok = True

    try:
        r = requests.get(f"{base}/api/data/{prop_path}", timeout=10)
        checks_ok &= check(r, f"GET /api/data/{prop_path}")
        original_value = extract_data_value(r)
    except Exception:
        print(f"[FAIL] GET /api/data/{prop_path} (exception)")
        checks_ok = False
        original_value = None

    try:
        r = requests.get(f"{base}/api/rooms", timeout=10)
        checks_ok &= check(r, "GET /api/rooms")
    except Exception:
        print("[FAIL] GET /api/rooms (exception)")
        checks_ok = False

    try:
        r = requests.get(f"{base}/api/method/{args.object}.getProperty", params={"property": args.property}, timeout=10)
        checks_ok &= check(r, f"GET /api/method/{args.object}.getProperty")
    except Exception:
        print(f"[FAIL] GET /api/method/{args.object}.getProperty (exception)")
        checks_ok = False

    if args.set_test:
        if original_value is None:
            print(f"[FAIL] --set-test requires readable current value for {prop_path}")
            checks_ok = False
        else:
            try:
                write_resp = requests.post(
                    f"{base}/api/data/{prop_path}",
                    json={"data": args.set_value},
                    timeout=10,
                )
                checks_ok &= check(write_resp, f"POST /api/data/{prop_path} (set {args.set_value})")
            except Exception:
                print(f"[FAIL] POST /api/data/{prop_path} (exception)")
                checks_ok = False

            try:
                rollback_resp = requests.post(
                    f"{base}/api/data/{prop_path}",
                    json={"data": original_value},
                    timeout=10,
                )
                checks_ok &= check(rollback_resp, f"POST /api/data/{prop_path} (rollback)")
            except Exception:
                print(f"[FAIL] POST /api/data/{prop_path} rollback (exception)")
                checks_ok = False

    return 0 if checks_ok else 1


if __name__ == "__main__":
    sys.exit(main())
