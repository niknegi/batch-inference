# Droplet API — local testing guide

Copy-paste curl commands to hit the live Droplet API from your laptop Terminal.

| | |
|---|---|
| **Base URL** | `http://167.71.233.238:8000` |
| **Auth** | `Authorization: Bearer demo-api-key-local-test` |
| **Prod LLM** | Droplet runs with `MOCK_PROVIDER=false` — use **digitalocean** + `openai-gpt-oss-20b` |

Interactive API docs (if reachable): http://167.71.233.238:8000/docs

---

## Table of contents

1. [Health check](#1-health-check)
2. [Create batch (live DigitalOcean LLM)](#2-create-batch-live-digitalocean-llm)
3. [Create from JSON file (`-d @file`)](#3-create-from-json-file--d-file)
4. [Smoke / load: 1000 prompts](#4-smoke--load-1000-prompts)
5. [Get one batch by id](#5-get-one-batch-by-id)
6. [List all batches](#6-list-all-batches)
7. [Get results (when completed)](#7-get-results-when-completed)
8. [Cancel a batch](#8-cancel-a-batch)
9. [Multipart NDJSON / file upload](#9-multipart-ndjson--file-upload)
10. [Mock provider (local / CI only)](#10-mock-provider-local--ci-only)
11. [Realtime webhook](#11-realtime-webhook)
12. [Common mistakes](#12-common-mistakes)

---

## 1. Health check

No auth required. Confirms the API process is up and which build is running.

```bash
curl -s "http://167.71.233.238:8000/health"
```

Expected shape:

```json
{
  "status": "ok",
  "version": "0.1.1",
  "git_sha": "c69095c",
  "build_id": "c69095c...",
  "built_at": "2026-07-18T07:45:00Z"
}
```

After a deploy, `git_sha` / `built_at` should match the commit you just pushed (or the manual deploy). If `git_sha` is `"unknown"`, the Droplet `.env` was not updated with `GIT_SHA`.

---

## 2. Create batch (live DigitalOcean LLM)

**Primary path on this Droplet.** Creates a small batch with real inference via `provider: digitalocean` and economy model `openai-gpt-oss-20b`. Response is `202` and includes an `id` — save it for later steps.

Requires on the Droplet: `MOCK_PROVIDER=false` and a valid `DO_INFERENCE_API_KEY`.

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: do-live-$(date +%s)" \
  -d '{
    "prompts": [
      "Explain how a black hole is formed in two sentences.",
      "What is the capital of France?"
    ],
    "provider": "digitalocean",
    "model": "openai-gpt-oss-20b",
    "chunk_size": 1,
    "rate_limit_rps": 5,
    "max_concurrency": 2
  }'
```

Example response:

```json
{
  "id": "01KXXXXXXXXXXXXXXXXXXXXXXX",
  "status": "queued",
  "total_items": 2,
  "chunk_size": 1
}
```

Capture the id for the rest of this guide:

```bash
BATCH_ID=$(curl -s -X POST "http://167.71.233.238:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: do-live-$(date +%s)" \
  -d '{
    "prompts": [
      "Explain how a black hole is formed in two sentences.",
      "What is the capital of France?"
    ],
    "provider": "digitalocean",
    "model": "openai-gpt-oss-20b",
    "chunk_size": 1,
    "rate_limit_rps": 5,
    "max_concurrency": 2
  }' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "BATCH_ID=$BATCH_ID"
```

---

## 3. Create from JSON file (`-d @file`)

From the **repo root**, post a ready-made body with curl's `@file` syntax. Use the **`_live`** samples on this Droplet (`MOCK_PROVIDER=false`).

5 prompts (live DigitalOcean):

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: file5-$(date +%s)" \
  -d @samples/batch_5_prompts_live.json
```

10 prompts (live DigitalOcean):

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: file10-$(date +%s)" \
  -d @samples/batch_10_prompts_live.json
```

Capture id from a file create:

```bash
BATCH_ID=$(curl -s -X POST "http://167.71.233.238:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: file5-$(date +%s)" \
  -d @samples/batch_5_prompts_live.json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "BATCH_ID=$BATCH_ID"
```

Note: `samples/batch_5_prompts.json` and `samples/batch_10_prompts.json` still use `"provider": "mock"` for local Compose / CI. Against this Droplet they will fail while `MOCK_PROVIDER=false` — prefer the `*_live.json` files, or edit `provider` / `model` in a copy before posting.

---

## 4. Smoke / load: 1000 prompts

**Warning:** This posts **1000** live DigitalOcean inferences. Expect real **token cost**, possible **rate limiting**, and a run that can take **many minutes**. Prefer the 5/10-prompt samples for routine checks. Do not fire this repeatedly.

From the **repo root**:

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: file1000-$(date +%s)" \
  -d @samples/batch_1000_prompts_live.json
```

Save the returned `id`, then poll status (see [§5](#5-get-one-batch-by-id)) until `completed` / `failed` / `cancelled` before fetching results.

Capture id:

```bash
BATCH_ID=$(curl -s -X POST "http://167.71.233.238:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: file1000-$(date +%s)" \
  -d @samples/batch_1000_prompts_live.json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "BATCH_ID=$BATCH_ID"
```

## 5. Get one batch by id

Poll status and progress. Use `$BATCH_ID` from the create response (or paste an id).

```bash
curl -s "http://167.71.233.238:8000/v1/batches/$BATCH_ID" \
  -H "Authorization: Bearer demo-api-key-local-test"
```

Watch for `status` (`queued` → `running` → `completed` / `failed` / `cancelled`) and `progress.fraction`. When done, `result_url` points at the authenticated API results endpoint (stream through the API — MinIO stays private).

Poll until complete:

```bash
while true; do
  curl -s "http://167.71.233.238:8000/v1/batches/$BATCH_ID" \
    -H "Authorization: Bearer demo-api-key-local-test" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d.get('progress')); raise SystemExit(0 if d['status'] in ('completed','failed','cancelled') else 1)" \
    && break
  sleep 2
done
```

---

## 6. List all batches

Newest first. Default page size is 50 (max 200).

```bash
curl -s "http://167.71.233.238:8000/v1/batches?limit=50&offset=0" \
  -H "Authorization: Bearer demo-api-key-local-test"
```

Pretty-print ids and statuses:

```bash
curl -s "http://167.71.233.238:8000/v1/batches?limit=50&offset=0" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(i['id'], i['status'], i['provider'], i['model']) for i in d['items']]"
```

---

## 7. Get results (when completed)

Only works after the batch has finished and `results_key` is set. The endpoint **streams NDJSON through the API** (auth required). MinIO does not need to be publicly reachable.

Download / print NDJSON for a completed batch (example id):

```bash
curl -s "http://167.71.233.238:8000/v1/batches/01KXT1PCPKM0K4Y0SXAF973NH6/results" \
  -H "Authorization: Bearer demo-api-key-local-test"
```

Or with `$BATCH_ID` from create/poll:

```bash
curl -s "http://167.71.233.238:8000/v1/batches/$BATCH_ID/results" \
  -H "Authorization: Bearer demo-api-key-local-test"
```

Save to a file:

```bash
curl -s "http://167.71.233.238:8000/v1/batches/$BATCH_ID/results" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -o results.ndjson
```

Optional: `?redirect=true` 302s to a Spaces presigned URL. That only works if `SPACES_PUBLIC_ENDPOINT_URL` is set to a browser-reachable MinIO/Spaces host (and port 9000 is published). Prefer the default stream path above.

```bash
curl -sI "http://167.71.233.238:8000/v1/batches/$BATCH_ID/results?redirect=true" \
  -H "Authorization: Bearer demo-api-key-local-test"
```

If you get `409 Results not ready`, poll get-by-id until `status` is `completed`.

---

## 8. Cancel a batch

Cancels an in-flight (or still-queued) batch. Create a larger/slower one first if you need something still running.

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/batches/$BATCH_ID/cancel" \
  -H "Authorization: Bearer demo-api-key-local-test"
```

Response is the updated batch (`status` → `cancelled` when successful).

---

## 9. Multipart NDJSON / file upload

`POST /v1/batches/upload` accepts an NDJSON or plain-text file via `-F file=@...`. Plain lines become `{"index": i, "prompt": "..."}`.

**Live DigitalOcean** using the repo NDJSON sample (from repo root):

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/batches/upload" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Idempotency-Key: upload-do-$(date +%s)" \
  -F "file=@samples/prompts_live.ndjson" \
  -F "provider=digitalocean" \
  -F "model=openai-gpt-oss-20b" \
  -F "chunk_size=1" \
  -F "rate_limit_rps=5" \
  -F "max_concurrency=2"
```

Or write a tiny NDJSON file ad hoc:

```bash
cat > /tmp/prompts.ndjson <<'NDJSON'
{"index": 0, "prompt": "Summarize the water cycle in two sentences."}
{"index": 1, "prompt": "List three benefits of unit testing."}
NDJSON

curl -s -X POST "http://167.71.233.238:8000/v1/batches/upload" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Idempotency-Key: upload-tmp-$(date +%s)" \
  -F "file=@/tmp/prompts.ndjson" \
  -F "provider=digitalocean" \
  -F "model=openai-gpt-oss-20b" \
  -F "chunk_size=1" \
  -F "rate_limit_rps=5" \
  -F "max_concurrency=2"
```

Plain-text upload (one prompt per line):

```bash
printf '%s\n' \
  "Explain photosynthesis in one sentence." \
  "What year did the Apollo 11 moon landing occur?" \
  > /tmp/prompts.txt

curl -s -X POST "http://167.71.233.238:8000/v1/batches/upload" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Idempotency-Key: txt-do-$(date +%s)" \
  -F "file=@/tmp/prompts.txt" \
  -F "provider=digitalocean" \
  -F "model=openai-gpt-oss-20b"
```

---

## 10. Mock provider (local / CI only)

**Not the Droplet default.** Prod is configured with `MOCK_PROVIDER=false`, so creates with `"provider": "mock"` typically fail with an unknown/unregistered provider error.

Use mock only when developing locally or in CI with `MOCK_PROVIDER=true`:

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: mock-$(date +%s)" \
  -d '{
    "prompts": ["hello mock"],
    "provider": "mock",
    "model": "mock-1"
  }'
```

Mock-shaped JSON files for local Compose (will fail on this Droplet while unmocked):

```bash
# Local / CI only — provider mock
curl -s -X POST "http://localhost:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: mock-file-$(date +%s)" \
  -d @samples/batch_5_prompts.json
```

---

## 11. Realtime webhook

End-to-end check that the API POSTs a signed webhook when a batch finishes.

**Shared test inbox** (ephemeral — regenerate on [webhook.site](https://webhook.site) if this expires):

`https://webhook.site/89d5421d-1978-4da8-bc95-5d6838df277e`

**Secret:** always pass `"webhook_secret": "test-secret-123"` (create/get responses never return the secret). Use that same value for HMAC verify below.

**Headers on every delivery**

| Header | Meaning |
|--------|---------|
| `X-Webhook-Signature` | `sha256=<hex>` HMAC-SHA256 of the **raw request body** using `webhook_secret` |
| `X-Webhook-Event` | e.g. `webhook.test`, `batch.completed`, `batch.failed` |
| `X-Batch-Id` | Batch id (or `"test"` for the ping endpoint) |

### Copy-paste steps

**1. Open the inbox in a browser**

Open [https://webhook.site/89d5421d-1978-4da8-bc95-5d6838df277e](https://webhook.site/89d5421d-1978-4da8-bc95-5d6838df277e) and leave it open to watch requests arrive.

**2. Optional ping** (`POST /v1/webhooks/test`)

Verifies delivery + signature headers before running a real batch:

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/webhooks/test" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://webhook.site/89d5421d-1978-4da8-bc95-5d6838df277e",
    "secret": "test-secret-123"
  }'
```

Expected API response: `{"ok": true, "error": null}`.

On webhook.site you should see a POST with `event: webhook.test` and headers `X-Webhook-Signature`, `X-Webhook-Event: webhook.test`, `X-Batch-Id: test`.

**3. Create a small DigitalOcean batch with webhook + secret**

This Droplet runs `MOCK_PROVIDER=false` — use **digitalocean** + `openai-gpt-oss-20b` (not mock).

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: webhook-live-$(date +%s)" \
  -d '{
    "prompts": [
      "What is the capital of France?",
      "Name one primary color."
    ],
    "provider": "digitalocean",
    "model": "openai-gpt-oss-20b",
    "chunk_size": 1,
    "rate_limit_rps": 5,
    "max_concurrency": 2,
    "webhook_url": "https://webhook.site/89d5421d-1978-4da8-bc95-5d6838df277e",
    "webhook_secret": "test-secret-123"
  }'
```

**4. Capture `BATCH_ID` from the response**

Either copy `id` from the JSON above, or create + capture in one shot:

```bash
BATCH_ID=$(curl -s -X POST "http://167.71.233.238:8000/v1/batches" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: webhook-live-$(date +%s)" \
  -d '{
    "prompts": [
      "What is the capital of France?",
      "Name one primary color."
    ],
    "provider": "digitalocean",
    "model": "openai-gpt-oss-20b",
    "chunk_size": 1,
    "rate_limit_rps": 5,
    "max_concurrency": 2,
    "webhook_url": "https://webhook.site/89d5421d-1978-4da8-bc95-5d6838df277e",
    "webhook_secret": "test-secret-123"
  }' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "BATCH_ID=$BATCH_ID"
```

**5. Poll `GET` until the batch completes**

```bash
while true; do
  curl -s "http://167.71.233.238:8000/v1/batches/$BATCH_ID" \
    -H "Authorization: Bearer demo-api-key-local-test" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d.get('webhook_status'), d.get('progress')); raise SystemExit(0 if d['status'] in ('completed','failed','cancelled') else 1)" \
    && break
  sleep 2
done
```

**6. Refresh webhook.site and confirm `batch.completed`**

When `status` is `completed`, refresh [https://webhook.site/89d5421d-1978-4da8-bc95-5d6838df277e](https://webhook.site/89d5421d-1978-4da8-bc95-5d6838df277e) and look for a POST with:

- `X-Webhook-Signature: sha256=...`
- `X-Webhook-Event: batch.completed`
- `X-Batch-Id: <your BATCH_ID>`
- JSON body including `"event": "batch.completed"`, `"batch_id"`, `"status": "completed"`, `stats`, `result_url`, `completed_at`, `timestamp`

On the batch, `webhook_status` should move to `delivered` (or `dead` after retries if the URL was unreachable).

**7. Optional — verify HMAC with `test-secret-123`**

Copy the **raw body** from webhook.site (exact bytes — no pretty-print).

Python:

```bash
python3 - <<'PY'
import hashlib, hmac
secret = "test-secret-123"
# Paste the exact raw body string from webhook.site:
body = b'{"event":"batch.completed",...}'  # replace with exact bytes
print("sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest())
PY
```

OpenSSL (save raw body from webhook.site into `/tmp/webhook-body.json` first):

```bash
printf 'sha256='; openssl dgst -sha256 -hmac 'test-secret-123' /tmp/webhook-body.json | awk '{print $2}'
```

That value must match `X-Webhook-Signature`.

---

## 12. Common mistakes

| Mistake | Symptom | Fix |
|---------|---------|-----|
| `https://` instead of `http://` | TLS / connection errors | Use **http** — Droplet listens on plain HTTP on port 8000 |
| Missing `:8000` | Connection refused / wrong service | Full base: `http://167.71.233.238:8000` |
| Wrong or missing API key | `401` | Header: `Authorization: Bearer demo-api-key-local-test` |
| Using `provider: mock` / mock sample JSON on prod | Create fails — mock not registered | Use section 2 or `samples/*_live.json` |
| Typo in path | `404` | Paths are `/health`, `/v1/batches`, `/v1/batches/{id}/results`, etc. |
| Results too early | `409 Results not ready` | Wait until get-by-id shows `completed` |
| Opening `result_url` that contains `minio:9000` | Browser DNS / connection failure | Use `GET /v1/batches/{id}/results` with the API key (streams NDJSON). Do not open internal Docker hostnames. |
| Missing DO key on Droplet | Live batch fails | Ensure `DO_INFERENCE_API_KEY` is set and `MOCK_PROVIDER=false` |

Quick sanity check that base URL + key work:

```bash
curl -s -o /dev/null -w "%{http_code}\n" "http://167.71.233.238:8000/health"
curl -s -o /dev/null -w "%{http_code}\n" "http://167.71.233.238:8000/v1/batches?limit=1" \
  -H "Authorization: Bearer demo-api-key-local-test"
# expect 200 and 200
```
