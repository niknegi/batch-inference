# Droplet API — local testing guide

Copy-paste curl commands to hit the live Droplet API from your laptop Terminal.

| | |
|---|---|
| **Base URL** | `http://167.71.233.238:8000` |
| **Auth** | `Authorization: Bearer demo-api-key-local-test` |

Set once per shell session:

```bash
export BASE=http://167.71.233.238:8000
export AUTH="Authorization: Bearer demo-api-key-local-test"
```

Interactive API docs (if reachable): http://167.71.233.238:8000/docs

---

## Table of contents

1. [Health check](#1-health-check)
2. [Create batch (JSON, mock)](#2-create-batch-json-mock)
3. [Get one batch by id](#3-get-one-batch-by-id)
4. [List all batches](#4-list-all-batches)
5. [Get results (when completed)](#5-get-results-when-completed)
6. [Cancel a batch](#6-cancel-a-batch)
7. [Multipart upload create](#7-multipart-upload-create)
8. [Live DigitalOcean inference](#8-live-digitalocean-inference)
9. [Webhook test](#9-webhook-test)
10. [Common mistakes](#10-common-mistakes)

---

## 1. Health check

No auth required. Confirms the API process is up.

```bash
curl -s "$BASE/health"
```

Expected shape: `{"status":"ok","version":"..."}`.

---

## 2. Create batch (JSON, mock)

Creates a batch with inline prompts using the **mock** provider (no real LLM calls). Response is `202` and includes an `id` — save it for later steps.

```bash
curl -s -X POST "$BASE/v1/batches" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: local-test-$(date +%s)" \
  -d '{
    "prompts": [
      "Explain how a black hole is formed in two sentences.",
      "What is the capital of France?",
      "Write a one-line haiku about rain."
    ],
    "provider": "mock",
    "model": "mock-1",
    "chunk_size": 2,
    "rate_limit_rps": 50,
    "max_concurrency": 4
  }'
```

Example response:

```json
{
  "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "status": "queued",
  "total_items": 3,
  "chunk_size": 2
}
```

Capture the id for the rest of this guide:

```bash
BATCH_ID=$(curl -s -X POST "$BASE/v1/batches" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: local-test-$(date +%s)" \
  -d '{
    "prompts": [
      "Explain how a black hole is formed in two sentences.",
      "What is the capital of France?",
      "Write a one-line haiku about rain."
    ],
    "provider": "mock",
    "model": "mock-1",
    "chunk_size": 2,
    "rate_limit_rps": 50,
    "max_concurrency": 4
  }' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "BATCH_ID=$BATCH_ID"
```

---

## 3. Get one batch by id

Poll status and progress. Use `$BATCH_ID` from the create response (or paste a UUID).

```bash
# If you already have the create JSON in the clipboard / last response:
# BATCH_ID=$(echo '<paste create response>' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl -s "$BASE/v1/batches/$BATCH_ID" \
  -H "$AUTH"
```

Watch for `status` (`queued` → `running` → `completed` / `failed` / `cancelled`) and `progress.fraction`. When done, `result_url` may be present (presigned Spaces URL).

Poll until complete:

```bash
while true; do
  curl -s "$BASE/v1/batches/$BATCH_ID" -H "$AUTH" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d['progress']); raise SystemExit(0 if d['status'] in ('completed','failed','cancelled') else 1)" \
    && break
  sleep 2
done
```

---

## 4. List all batches

Newest first. Default page size is 50 (max 200).

```bash
curl -s "$BASE/v1/batches?limit=50&offset=0" \
  -H "$AUTH"
```

Pretty-print ids and statuses:

```bash
curl -s "$BASE/v1/batches?limit=50&offset=0" -H "$AUTH" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(i['id'], i['status'], i['provider'], i['model']) for i in d['items']]"
```

---

## 5. Get results (when completed)

Only works after the batch has finished and `results_key` is set. By default the endpoint **302-redirects** to a presigned Spaces URL.

Follow redirect and print NDJSON results:

```bash
curl -sL "$BASE/v1/batches/$BATCH_ID/results" \
  -H "$AUTH"
```

JSON with URL only (no redirect):

```bash
curl -s "$BASE/v1/batches/$BATCH_ID/results?redirect=false" \
  -H "$AUTH"
```

If you get `409 Results not ready`, poll get-by-id until `status` is `completed`.

---

## 6. Cancel a batch

Cancels an in-flight (or still-queued) batch. Create a larger/slower one first if you need something still running.

```bash
curl -s -X POST "$BASE/v1/batches/$BATCH_ID/cancel" \
  -H "$AUTH"
```

Response is the updated batch (`status` → `cancelled` when successful).

---

## 7. Multipart upload create

`POST /v1/batches/upload` accepts an NDJSON or plain-text file. Plain lines become `{"index": i, "prompt": "..."}`.

Create a tiny NDJSON file, then upload:

```bash
cat > /tmp/prompts.ndjson <<'EOF'
{"index": 0, "prompt": "Summarize the water cycle in two sentences."}
{"index": 1, "prompt": "List three benefits of unit testing."}
{"index": 2, "prompt": "Name one constellation visible from the northern hemisphere."}
EOF

curl -s -X POST "$BASE/v1/batches/upload" \
  -H "$AUTH" \
  -H "Idempotency-Key: upload-$(date +%s)" \
  -F "file=@/tmp/prompts.ndjson" \
  -F "provider=mock" \
  -F "model=mock-1" \
  -F "chunk_size=2" \
  -F "rate_limit_rps=50" \
  -F "max_concurrency=4"
```

Or reuse repo samples as a prompt list (JSON body, not multipart):

```bash
# From repo root — samples/batch_5_prompts.json is already mock-shaped
curl -s -X POST "$BASE/v1/batches" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: sample-$(date +%s)" \
  -d @samples/batch_5_prompts.json
```

Plain-text upload also works:

```bash
printf '%s\n' \
  "Explain photosynthesis in one sentence." \
  "What year did the Apollo 11 moon landing occur?" \
  > /tmp/prompts.txt

curl -s -X POST "$BASE/v1/batches/upload" \
  -H "$AUTH" \
  -H "Idempotency-Key: txt-$(date +%s)" \
  -F "file=@/tmp/prompts.txt" \
  -F "provider=mock" \
  -F "model=mock-1"
```

---

## 8. Live DigitalOcean inference

Uses `provider: digitalocean` and a chat-capable catalog model. **Real inference only works when the Droplet has `MOCK_PROVIDER=false` and a valid `DO_INFERENCE_API_KEY`.**

If mock mode is still on the Droplet, this create may still be accepted but will not call live DO Inference — exercise mock first (section 2), then ask whoever runs the Droplet to set:

```bash
MOCK_PROVIDER=false
DO_INFERENCE_API_KEY=...   # Model Access Key from DO Control Panel → Inference
```

Live create:

```bash
curl -s -X POST "$BASE/v1/batches" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: do-live-$(date +%s)" \
  -d '{
    "prompts": [
      "Explain how a black hole is formed.",
      "In one paragraph, what is gradient descent?"
    ],
    "provider": "digitalocean",
    "model": "openai-gpt-oss-20b",
    "chunk_size": 1,
    "rate_limit_rps": 5,
    "max_concurrency": 2
  }'
```

Then poll with section 3 / fetch results with section 5.

---

## 9. Webhook test

Pings an arbitrary HTTPS endpoint with a signed test payload (`event: webhook.test`). Useful to verify your receiver and firewall before attaching `webhook_url` on a real batch.

Replace the URL with your own webhook.site / RequestBin / ngrok URL:

```bash
curl -s -X POST "$BASE/v1/webhooks/test" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://webhook.site/YOUR-UUID",
    "secret": "optional-shared-secret"
  }'
```

Expected: `{"ok": true, "error": null}` if delivery succeeded.

On a real batch you can also pass `"webhook_url": "https://..."` in the create JSON (sections 2 / 8).

---

## 10. Common mistakes

| Mistake | Symptom | Fix |
|---------|---------|-----|
| `https://` instead of `http://` | TLS / connection errors | Use **http** — Droplet listens on plain HTTP on port 8000 |
| Missing `:8000` | Connection refused / wrong service | Full base: `http://167.71.233.238:8000` |
| Wrong or missing API key | `401` | Header: `Authorization: Bearer demo-api-key-local-test` |
| Typo in path | `404` | Paths are `/health`, `/v1/batches`, `/v1/batches/{id}/results`, etc. |
| Results too early | `409 Results not ready` | Wait until get-by-id shows `completed` |
| Expecting live DO while mock is on | Mock-ish / non-DO behavior | Confirm `MOCK_PROVIDER=false` on the Droplet for section 8 |

Quick sanity check that base URL + key work:

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/health"
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/v1/batches?limit=1" -H "$AUTH"
# expect 200 and 200
```
