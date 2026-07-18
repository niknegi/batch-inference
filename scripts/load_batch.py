#!/usr/bin/env python3
"""Load / scale-path script: create a large batch against a running API (mock provider).

Usage:
  python scripts/load_batch.py --base-url http://localhost:8000 --api-key dev-api-key-change-me --count 1000
  python scripts/load_batch.py --count 50000 --chunk-size 100 --poll
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit a large batch to the API")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", default="dev-api-key-change-me")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--model", default="mock-1")
    parser.add_argument("--rate-limit-rps", type=float, default=200)
    parser.add_argument("--max-concurrency", type=int, default=32)
    parser.add_argument("--poll", action="store_true")
    parser.add_argument("--webhook-url", default=None)
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.api_key}"}
    prompts = [f"load-test prompt {i}" for i in range(args.count)]
    payload = {
        "prompts": prompts,
        "provider": args.provider,
        "model": args.model,
        "chunk_size": args.chunk_size,
        "rate_limit_rps": args.rate_limit_rps,
        "max_concurrency": args.max_concurrency,
    }
    if args.webhook_url:
        payload["webhook_url"] = args.webhook_url

    print(f"Submitting {args.count} prompts (chunk_size={args.chunk_size})...")
    t0 = time.time()
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=120.0) as client:
        resp = client.post("/v1/batches", json=payload)
        resp.raise_for_status()
        data = resp.json()
        batch_id = data["id"]
        print(f"Created batch {batch_id} in {time.time() - t0:.2f}s")

        if not args.poll:
            return 0

        while True:
            st = client.get(f"/v1/batches/{batch_id}").json()
            prog = st["progress"]
            print(
                f"status={st['status']} "
                f"{prog['completed_items']}/{prog['total_items']} "
                f"({prog['fraction']:.2%})"
            )
            if st["status"] in ("completed", "failed", "cancelled"):
                if st.get("result_url"):
                    print(f"result_url={st['result_url']}")
                if st.get("error"):
                    print(f"error={st['error']}", file=sys.stderr)
                return 0 if st["status"] == "completed" else 1
            time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
