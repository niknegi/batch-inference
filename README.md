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
curl -s -X POST http://localhost:8000/v1/batches \
  -H "Authorization: Bearer dev-api-key-change-me" \
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
| `POST` | `/v1/batches` | Create batch (`Idempotency-Key` supported) |
| `GET` | `/v1/batches/{id}` | Status + progress + result URL |
| `GET` | `/v1/batches/{id}/results` | Redirect to presigned Spaces URL |
| `POST` | `/v1/batches/{id}/cancel` | Cancel in-flight batch |
| `POST` | `/v1/webhooks/test` | Ping a webhook endpoint |
| `GET` | `/health` | Liveness |
| `GET` | `/metrics` | Prometheus metrics |

Auth: `Authorization: Bearer <API_KEY>` (comma-separated keys in `API_KEYS`).

## Providers

Set env vars and pass `provider` on create:

| `provider` | Config |
|------------|--------|
| `mock` | `MOCK_PROVIDER=true` (default in Compose) |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai_compatible` | `OPENAI_COMPATIBLE_BASE_URL` + optional `OPENAI_COMPATIBLE_API_KEY` (vLLM, Groq, Together, etc.) |

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
| **CD** | push to `master`/`main`, tags `v*`, or manual | Re-runs full test suite, then builds & pushes to `ghcr.io/niknegi/batch-inference` (publish is blocked if tests fail) |

Pull the published image:

```bash
docker pull ghcr.io/niknegi/batch-inference:latest
```

If the package is private, authenticate first:

```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
```

Tag a release to publish a semver image:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## License

MIT
