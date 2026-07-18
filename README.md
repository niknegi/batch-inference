# Batch Inference Service

[![CI](https://github.com/niknegi/batch-inference/actions/workflows/ci.yml/badge.svg)](https://github.com/niknegi/batch-inference/actions/workflows/ci.yml)

Production-ready Python service for batching LLM inferences at scale (1k → 500k prompts).

## What it does

1. Accepts a batch of prompts via HTTP API
2. Persists prompts as NDJSON to **DigitalOcean Spaces** (S3-compatible; MinIO locally)
3. Chunks work and fans out across a **bounded arq worker pool**
4. Applies a shared **Redis token-bucket** rate limiter across workers
5. Writes **per-chunk result checkpoints** to Spaces (crash loses at most one in-flight chunk)
6. Aggregates results + fires a **signed webhook** on completion/failure

```text
Client → FastAPI → Postgres (metadata) + Spaces (prompts/results) + Redis (jobs)
                 → arq workers (orchestrate → process_chunk → finalize → webhook)
```

## Quick start (Docker Compose)

```bash
cp .env.example .env
docker compose up --build
```

Services:

| Service | URL |
|---------|-----|
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 (minioadmin/minioadmin) |
| Postgres | localhost:5432 |
| Redis | localhost:6379 |

`MOCK_PROVIDER=true` is enabled in Compose so you can run without real LLM keys.

### Submit a batch

```bash
curl -s -X POST http://167.71.233.238:8000/v1/batches \
  -H "Authorization: Bearer demo-api-key-local-test" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-1" \
  -d '{
    "prompts": ["hello", "world", "batch me"],
    "provider": "mock",
    "model": "mock-1",
    "chunk_size": 2,
    "rate_limit_rps": 50,
    "max_concurrency": 8,
    "webhook_url": "https://example.com/hooks/batch"
  }'
```

`provider`, `model`, and `cost_preference` are optional — they fall back to `DEFAULT_PROVIDER`, `DEFAULT_MODEL`, and `DEFAULT_COST_PREFERENCE` (economy routing prefers cheap catalog models such as `openai-gpt-oss-20b` when applicable).

You can also supply prompts without inlining them:

| Field | Meaning |
|-------|---------|
| `prompts` | Inline list (≤50k) |
| `prompts_url` | HTTP(S) URL of NDJSON / plain-text lines to download |
| `prompts_key` | Existing object key in the Spaces bucket to copy |
| `cost_preference` | `economy` (default) / `standard` / `premium` |

At least one of `prompts`, `prompts_url`, or `prompts_key` is required.

### Upload an NDJSON file

```bash
curl -s -X POST http://localhost:8000/v1/batches/upload \
  -H "Authorization: Bearer dev-api-key-change-me" \
  -H "Idempotency-Key: upload-1" \
  -F "file=@prompts.ndjson" \
  -F "provider=mock" \
  -F "model=mock-1" \
  -F "cost_preference=economy" \
  -F "chunk_size=100" \
  -F "rate_limit_rps=50" \
  -F "max_concurrency=8"
```

Plain-text lines in the file are normalized to `{"index": i, "prompt": "..."}`.

Poll status:

```bash
curl -s http://localhost:8000/v1/batches/<BATCH_ID> \
  -H "Authorization: Bearer dev-api-key-change-me"
```

Load script (1k–50k+ with mock provider):

```bash
pip install -e ".[dev]"
python scripts/load_batch.py --count 1000 --poll
# Scale-path smoke (needs running stack + enough memory for JSON body):
python scripts/load_batch.py --count 50000 --chunk-size 100 --poll
```

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/batches` | Create batch from JSON (`prompts` / `prompts_url` / `prompts_key`; `Idempotency-Key` supported) |
| `POST` | `/v1/batches/upload` | Create batch from multipart NDJSON/file upload |
| `GET` | `/v1/batches` | List batches (`limit` default 50 max 200, `offset`) newest first |
| `GET` | `/v1/batches/{id}` | Status + progress + result URL |
| `GET` | `/v1/batches/{id}/results` | Redirect to presigned Spaces URL |
| `POST` | `/v1/batches/{id}/cancel` | Cancel in-flight batch |
| `POST` | `/v1/webhooks/test` | Ping a webhook endpoint |
| `GET` | `/health` | Liveness + `version` / `git_sha` / `built_at` |
| `GET` | `/metrics` | Prometheus metrics |

Auth: `Authorization: Bearer <API_KEY>` (comma-separated keys in `API_KEYS`).

## Providers

Set env vars and pass `provider` on create (or rely on defaults):

| Env | Default | Purpose |
|-----|---------|---------|
| `DEFAULT_PROVIDER` | `mock` | Used when create omits `provider` |
| `DEFAULT_MODEL` | `openai-gpt-oss-20b` | Used when create omits `model` (if it fits `cost_preference`) |
| `DEFAULT_COST_PREFERENCE` | `economy` | Prefer cheap catalog models unless overridden |

| `provider` | Config |
|------------|--------|
| `mock` | `MOCK_PROVIDER=true` — **use for local/CI** (no real LLM calls) |
| `digitalocean` | `DO_INFERENCE_API_KEY` — **prod** DigitalOcean Serverless Inference |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai_compatible` | `OPENAI_COMPATIBLE_BASE_URL` + optional `OPENAI_COMPATIBLE_API_KEY` |

### DigitalOcean Inference (production)

Create a **Model Access Key**: Control Panel → Inference → API Keys → Generate Key.

```bash
# .env (prod)
MOCK_PROVIDER=false
DO_INFERENCE_API_KEY=your-model-access-key
DO_INFERENCE_BASE_URL=https://inference.do-ai.run/v1
```

Submit a batch against a catalog model ID (chat completions):

```bash
curl -s -X POST http://localhost:8000/v1/batches \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompts": ["Explain how a black hole is formed."],
    "provider": "digitalocean",
    "model": "llama3.3-70b-instruct",
    "rate_limit_rps": 10,
    "max_concurrency": 8
  }'
```

Under the hood each item calls:

`POST https://inference.do-ai.run/v1/chat/completions` with Bearer auth and an OpenAI-compatible `{ model, messages }` body.

> **Note:** fal image/audio models use `/v1/async-invoke` with `{ model_id, input }` — that path is not used by this batch text service. Use chat-capable catalog models for `provider: digitalocean`.

## Crash safety & scale model

- Prompts never sit in Redis — only `(batch_id, chunk_id, chunk_index)` job messages
- 500k items @ `chunk_size=100` → 5,000 chunk jobs (~1MB queue payload)
- Each finished chunk is written to `batches/{id}/chunks/{index}.ndjson` before DB success
- Expired leases (`CHUNK_LEASE_SECONDS`) are reclaimed; succeeded chunks are not recomputed
- Finalize concatenates chunk objects into `results.ndjson` + `manifest.json`

## Webhooks

Events: `batch.completed`, `batch.failed`

Headers:

- `X-Webhook-Signature: sha256=<hmac>`
- `X-Webhook-Event`
- `X-Batch-Id`

Verify with HMAC-SHA256 of the raw body using `webhook_secret` (auto-generated if omitted).

Deliveries retry with exponential backoff (cap 5m, `WEBHOOK_MAX_ATTEMPTS` then `dead`).

## DigitalOcean production

1. **Spaces**: create a bucket; set:
   ```bash
   SPACES_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com
   SPACES_REGION=nyc3
   SPACES_ACCESS_KEY=...
   SPACES_SECRET_KEY=...
   SPACES_BUCKET=your-bucket
   SPACES_FORCE_PATH_STYLE=false
   ```
   For Compose + MinIO on a Droplet, keep the internal endpoint for server ops and optionally set a public rewrite for presigned redirects only:
   ```bash
   SPACES_ENDPOINT_URL=http://minio:9000
   # Optional — only if you expose MinIO and use ?redirect=true
   # SPACES_PUBLIC_ENDPOINT_URL=http://YOUR_DROPLET_IP:9000
   PUBLIC_BASE_URL=http://YOUR_DROPLET_IP:8000
   ```
   Prefer downloading results via authenticated `GET /v1/batches/{id}/results` (API streams NDJSON; MinIO can stay private).
2. **Managed Postgres** → `DATABASE_URL=postgresql+asyncpg://...`
3. **Managed Redis** → `REDIS_URL=rediss://...` (or `redis://`)
4. Run **API** (stateless, horizontally scalable) and **N worker** replicas:
   ```bash
   arq app.workers.main.WorkerSettings
   ```
   Tune `WORKER_CONCURRENCY` per process and replica count independently of `rate_limit_rps`.
5. Set real provider keys and `MOCK_PROVIDER=false`
6. Rotate `API_KEYS`

## Local development (without Compose for app)

```bash
docker compose up -d postgres redis minio minio-init
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
# other terminal:
arq app.workers.main.WorkerSettings
pytest
```

## Project layout

```text
app/
  api/          # FastAPI routes, auth, schemas
  core/         # config, db, logging, Spaces client, metrics
  models/       # SQLAlchemy Batch / BatchChunk
  providers/    # Protocol + OpenAI / Anthropic / compatible / mock
  rate_limit/   # Redis token bucket
  services/     # batch create + webhook helpers
  workers/      # arq jobs
alembic/        # migrations
scripts/        # load_batch.py
tests/
```

## CI / CD

GitHub Actions workflows live in `.github/workflows/`:

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| **CI** | push/PR to `master`/`main` | Ruff lint + format, **full pytest suite** (`tests/`), Docker build (no push) |
| **CD** | push to `master`/`main`, tags `v*`, or manual | Tests → publish image to GHCR → **SSH deploy to Droplet** (on `master`/`main` only) |

### Verify a deploy

```bash
curl -s http://YOUR_DROPLET_IP:8000/health
```

Example:

```json
{
  "status": "ok",
  "version": "0.1.1",
  "git_sha": "abc1234",
  "build_id": "full-sha-or-github-sha",
  "built_at": "2026-07-18T12:00:00Z"
}
```

`GIT_SHA` / `BUILD_ID` / `BUILT_AT` are written into the Droplet `.env` at deploy time and passed into the `api` / `worker` containers.

### GitHub secrets (required for Droplet CD)

In the repo: **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|--------|-------|
| `DROPLET_HOST` | `167.71.233.238` |
| `DROPLET_USER` | `root` |
| `DROPLET_SSH_KEY` | **Private** SSH key whose public key is in Droplet `~/.ssh/authorized_keys` |

Do **not** commit private keys. `GITHUB_TOKEN` is provided automatically by Actions (used for GHCR push and optional git pull on the Droplet).

After secrets are set, every push to `master`/`main` that passes tests will:

1. Publish `ghcr.io/niknegi/batch-inference`
2. SSH to the Droplet, `git fetch` + reset to `origin/master`, set build env vars, `docker compose up --build -d`, and `curl` `/health`

### Pull the published image

```bash
docker pull ghcr.io/niknegi/batch-inference:latest
```

If the package is private, authenticate first:

```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
```

Tag a release to publish a semver image:

```bash
git tag v0.1.1 && git push origin v0.1.1
```

### Optional: deploy via git push to Droplet

You can still push directly to a bare repo on the Droplet (`post-receive` sets `GIT_SHA` and rebuilds). See [`deploy/`](deploy/).

## License

MIT
