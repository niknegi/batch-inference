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
4. [Get one batch by id](#4-get-one-batch-by-id)
5. [List all batches](#5-list-all-batches)
6. [Get results (when completed)](#6-get-results-when-completed)
7. [Cancel a batch](#7-cancel-a-batch)
8. [Multipart NDJSON / file upload](#8-multipart-ndjson--file-upload)
9. [Mock provider (local / CI only)](#9-mock-provider-local--ci-only)
10. [Webhook test](#10-webhook-test)
11. [Common mistakes](#11-common-mistakes)

---

## 1. Health check

No auth required. Confirms the API process is up.

```bash
curl -s "http://167.71.233.238:8000/health"
```

Expected shape: `{"status":"ok","version":"..."}`.

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

## 4. Get one batch by id

Poll status and progress. Use `$BATCH_ID` from the create response (or paste an id).

```bash
curl -s "http://167.71.233.238:8000/v1/batches/$BATCH_ID" \
  -H "Authorization: Bearer demo-api-key-local-test"
```

Watch for `status` (`queued` → `running` → `completed` / `failed` / `cancelled`) and `progress.fraction`. When done, `result_url` may be present (presigned Spaces URL).

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

## 5. List all batches

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

## 6. Get results (when completed)

Only works after the batch has finished and `results_key` is set. By default the endpoint **302-redirects** to a presigned Spaces URL.

Follow redirect and print NDJSON results:

```bash
curl -sL "http://167.71.233.238:8000/v1/batches/$BATCH_ID/results" \
  -H "Authorization: Bearer demo-api-key-local-test"
```

JSON with URL only (no redirect):

```bash
curl -s "http://167.71.233.238:8000/v1/batches/$BATCH_ID/results?redirect=false" \
  -H "Authorization: Bearer demo-api-key-local-test"
```

If you get `409 Results not ready`, poll get-by-id until `status` is `completed`.

---

## 7. Cancel a batch

Cancels an in-flight (or still-queued) batch. Create a larger/slower one first if you need something still running.

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/batches/$BATCH_ID/cancel" \
  -H "Authorization: Bearer demo-api-key-local-test"
```

Response is the updated batch (`status` → `cancelled` when successful).

---

## 8. Multipart NDJSON / file upload

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

## 9. Mock provider (local / CI only)

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

## 10. Webhook test

Pings an arbitrary HTTPS endpoint with a signed test payload (`event: webhook.test`). Useful to verify your receiver and firewall before attaching `webhook_url` on a real batch.

Replace the URL with your own webhook.site / RequestBin / ngrok URL:

```bash
curl -s -X POST "http://167.71.233.238:8000/v1/webhooks/test" \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://webhook.site/YOUR-UUID",
    "secret": "optional-shared-secret"
  }'
```

Expected: `{"ok": true, "error": null}` if delivery succeeded.

On a real batch you can also pass `"webhook_url": "https://..."` in the create JSON (section 2).

---

## 11. Common mistakes

| Mistake | Symptom | Fix |
|---------|---------|-----|
| `https://` instead of `http://` | TLS / connection errors | Use **http** — Droplet listens on plain HTTP on port 8000 |
| Missing `:8000` | Connection refused / wrong service | Full base: `http://167.71.233.238:8000` |
| Wrong or missing API key | `401` | Header: `Authorization: Bearer demo-api-key-local-test` |
| Using `provider: mock` / mock sample JSON on prod | Create fails — mock not registered | Use section 2 or `samples/*_live.json` |
| Typo in path | `404` | Paths are `/health`, `/v1/batches`, `/v1/batches/{id}/results`, etc. |
| Results too early | `409 Results not ready` | Wait until get-by-id shows `completed` |
| Missing DO key on Droplet | Live batch fails | Ensure `DO_INFERENCE_API_KEY` is set and `MOCK_PROVIDER=false` |

Quick sanity check that base URL + key work:

```bash
curl -s -o /dev/null -w "%{http_code}\n" "http://167.71.233.238:8000/health"
curl -s -o /dev/null -w "%{http_code}\n" "http://167.71.233.238:8000/v1/batches?limit=1" \
  -H "Authorization: Bearer demo-api-key-local-test"
# expect 200 and 200
```
