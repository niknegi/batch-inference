# Scaling, memory, and throttling

Concrete footprint and throughput guidance for large batches (focus: **500k items**), derived from settings defaults and code paths in `app/core/config.py`, `app/workers/`, `app/core/spaces.py`, and `app/rate_limit/`.

Companion: [architecture.md](./architecture.md).

## Defaults (settings / `.env.example`)

| Setting | Default | Where |
|---|---|---|
| `DEFAULT_CHUNK_SIZE` | **100** | `Settings` / batch create |
| `DEFAULT_RATE_LIMIT_RPS` | **50** | Per-batch; Redis token bucket |
| `DEFAULT_MAX_CONCURRENCY` | **16** | Per-batch; **per worker process** |
| `WORKER_CONCURRENCY` | **32** | arq `max_jobs` |
| `CHUNK_LEASE_SECONDS` | **300** | Postgres `leased_until` |
| `CHUNK_MAX_ATTEMPTS` | **5** | Exhaustion → batch `failed` |
| `job_timeout` | **600** s | Hardcoded in `WorkerSettings` |
| Per-item retries | **3** | Inside `process_chunk` / `run_one` |
| `chunk_size` API max | **10_000** | `BatchCreateRequest` |
| Inline JSON prompts max | **50_000** | Use upload / `prompts_url` / `prompts_key` for larger |

---

## 1. Memory footprint

### What lives where

| Data | Store | Held in RAM? |
|---|---|---|
| Prompts NDJSON | Spaces `batches/{id}/prompts.ndjson` | Only during ingest and per-chunk `read_line_range` |
| Chunk results | Spaces `.../chunks/{index:06d}.ndjson` | Built in worker, then uploaded |
| Final results | Spaces `.../results.ndjson` | **Yes — full body during `concatenate_chunks`** |
| Manifest | Spaces `.../manifest.json` | Small (chunk metadata list) |
| Job queue | Redis / arq | Tiny: `(batch_id, chunk_id, chunk_index)` only |
| Progress / leases | Postgres `Batch` + `BatchChunk` | Metadata only |

Prompts never sit in Redis — only job handles (see README crash-safety notes).

### API (ingest)

- **Inline JSON** (`POST /v1/batches` with `prompts`): capped at 50k items. Not viable for 500k.
- **Multipart upload** (`POST /v1/batches/upload`): `file.read()` then `upload_raw_ndjson` rebuilds the full NDJSON in memory → put to Spaces. Peak ≈ **2–3× file size**.
- **`prompts_url`**: downloads `resp.content` then same rebuild — full object in RAM.
- **`prompts_key`** (pre-uploaded + `copy_key`): best path for 500k — API copies within the bucket and counts lines via streaming.

Rough prompts size @ ~300 B/line → **~150 MB** object; avoid buffering that twice on the API.

### Worker (per chunk)

`process_chunk`:

1. `SpacesClient.read_line_range(prompts_key, offset, limit)` — loads **all `chunk_size` rows** into a list (streams the object from the start; see bottlenecks).
2. `asyncio.gather(*[run_one(item) for item in rows])` — one coroutine per item; `BatchConcurrencyGate` only limits concurrent provider calls.
3. `write_chunk_results` — builds the full chunk NDJSON body in memory, then `put_bytes`.

At default `chunk_size=100`:

- Peak per in-flight chunk ≈ prompts slice + outcomes + result NDJSON → **few MB to tens of MB** (depends on output size).
- Up to `WORKER_CONCURRENCY` (32) overlapping chunk jobs per process → order **100s of MB–1+ GB** if outputs are large.

### Finalize / `concatenate_chunks`

`SpacesClient.concatenate_chunks` reads **every** chunk object fully into a `parts` list, joins into one body, then `put_object`. Peak worker RAM ≈ **entire results NDJSON**.

| Avg result line | Final object (~500k lines) | Finalize peak RAM |
|---|---|---|
| ~1 KB | ~0.5 GB | **~0.5–1 GB** |
| ~5 KB | ~2.5 GB | **~2.5–5 GB** |
| ~10 KB | ~5 GB | **~5–10 GB** |

This is the main OOM risk at 500k. Client download via `GET /v1/batches/{id}/results` is streaming (`stream_object`, 64 KiB chunks) and does not load the full file into API RAM.

### Redis

- ~5k jobs @ `chunk_size=100` → ~**1 MB** queue payload (order-of-magnitude).
- Token-bucket hash per provider rate-limit key: negligible (`EXPIRE` 3600 s).

### Postgres row counts (500k items)

| `chunk_size` | `Batch` rows | `BatchChunk` rows |
|---|---|---|
| 50 | 1 | **10,000** |
| **100 (default)** | 1 | **5,000** |
| 500 | 1 | **1,000** |
| 1,000 | 1 | **500** |
| 10,000 (API max) | 1 | **50** |

Chunk rows are small; 5k rows is trivial for Postgres. Manifest JSON grows with chunk count (~few hundred KB at 5k).

### Spaces / MinIO object count

1 prompts + N chunk results + 1 results + 1 manifest.

At `chunk_size=100`: **~5,002 objects** for a completed batch.

---

## 2. Resource throttling

| Control | Scope | Default | Behavior |
|---|---|---|---|
| `rate_limit_rps` | **Global** (Redis Lua token bucket) | 50 | Shared across all workers; `capacity = rate` |
| `max_concurrency` | **Per worker process** | 16 | `BatchConcurrencyGate` = local `asyncio.Semaphore` |
| `WORKER_CONCURRENCY` | Per process | 32 | arq `max_jobs` |
| Chunk lease | Per chunk (Postgres) | 300 s | `reclaim_leases_cron` every second |
| Item retries | Inside `run_one` | 3 | Full-jitter backoff (`exponential_backoff_seconds`, cap 8 s) |
| Chunk retries | Re-enqueue until fail | 5 | Then batch → `failed` + webhook |
| Provider pause | Redis `pause_until` | — | On `retry_after` from provider |

**Concurrency vs RPS:** with **W** worker processes, theoretical concurrent in-flight calls ≈ `W × max_concurrency`, but sustained throughput is still capped by the shared bucket at `rate_limit_rps`. Extra workers mainly reduce queue wait / soak provider latency; they do not raise RPS past the bucket.

`TokenBucketRateLimiter.acquire(..., max_wait=120)` — if starved longer than 120 s, the chunk raises and may retry or fail.

---

## 3. Scaling to 500k

### Wall-clock estimate

\[
T \approx \frac{N}{\min\left(R,\; C_{\mathrm{eff}} / L,\; \text{other}\right)} + T_{\mathrm{ingest}} + T_{\mathrm{finalize}} + T_{\mathrm{overhead}}
\]

- \(N\) = total items (500 000)
- \(R\) = `rate_limit_rps` (default **50**)
- \(L\) = average provider latency
- \(C_{\mathrm{eff}}\) ≈ `W × max_concurrency` (soft; RPS usually binds first)
- Overhead: Spaces reads, lease races, retries, finalize concat

**At defaults (\(R = 50\) binding):**

\[
500\,000 / 50 = 10\,000~\mathrm{s} \approx \mathbf{\sim 2.8~h}
\]

| Scenario | Effective RPS | ~Wall clock |
|---|---|---|
| Defaults (50 RPS) | 50 | **~3 h** |
| Provider allows 200 RPS | 200 | **~40–50 min** |
| Slow model, 4 workers × 16 conc, \(L = 4\) s | \(\min(50, 64/4) = 16\) | **~9 h** |
| +20% retries / overhead | — | multiply by ~1.2 |

### Recommendations for 500k

| Knob | Recommendation | Why |
|---|---|---|
| Ingest | **`prompts_key`** (or stage via URL into Spaces first) | Avoid API OOM on 150 MB+ bodies |
| `chunk_size` | **100–500** (default 100 OK; prefer **200–500**) | Fewer jobs + less `read_line_range` scan waste; watch lease vs chunk duration |
| `rate_limit_rps` | Match provider quota | Primary throughput dial |
| `max_concurrency` | Provider-friendly; do not multiply blindly by \(W\) | Semaphore is local per process |
| Workers | **2–8** processes until RPS saturates | Helps latency-bound batches |
| Finalize worker RAM | **≥ 2–3× expected results size** | Concat loads the full results object |
| Lease | Raise `CHUNK_LEASE_SECONDS` if chunk wall time ≫ 5 min | Avoid mid-chunk reclaim |

Avoid `chunk_size=1` (500k jobs) and very large chunks when outputs are huge (`gather` + write memory).

### Bottlenecks / known gaps

1. **`concatenate_chunks` OOM** — full results in memory; critical at 500k with large outputs.
2. **`read_line_range` is O(chunks × file)** — streams from line 0 every time. At 5k chunks × 500k lines, order **~10⁹** line visits (average half-file). Dominates Spaces/CPU at small `chunk_size`. Larger chunks help a lot.
3. **Lease 300 s vs `job_timeout` 600 s** — lease can expire while the job still runs → reclaim → second worker may re-run. Success path is mostly idempotent but wastes work and can amplify queue load.
4. **`check_batch_completion` re-enqueues all pending** on each poll — under load can stampede Redis/arq.
5. **`orchestrate_batch` enqueues all chunks at once** — 5k jobs is fine; tens/hundreds of thousands (tiny chunks) stresses the job bus.
6. **Gate is not global** — horizontal workers multiply intended concurrency; only `rate_limit_rps` is truly shared.

### Horizontal scale: what helps vs what doesn’t

| Scale out | Helps? |
|---|---|
| More **workers** | Yes until `rate_limit_rps` (or provider) saturates |
| Higher **`rate_limit_rps`** | Yes — main dial if the provider allows |
| Larger **`chunk_size`** | Yes — fewer jobs, less scan waste, faster orchestrate |
| More **API** replicas | Ingest only; not inference throughput |
| Bigger **Postgres / Redis** | Not the bottleneck at ~5k chunk rows / tiny job payloads |
| Bigger **Spaces** disk / bandwidth | Yes for storage + finalize upload |
| More workers **without** raising RPS | Diminishing returns; can worsen lease races |

---

## Bottom line (500k @ defaults)

- **~5,000** chunk jobs / `BatchChunk` rows; Redis stays small; Postgres is fine.
- Steady-state time ≈ \(N / \texttt{rate\_limit\_rps}\) → **~3 hours** at 50 RPS.
- Hold prompts and results in Spaces; workers hold one chunk’s worth of items (times overlapping jobs).
- Hard limits: **finalize concat RAM** and **`read_line_range` scan cost** — prefer `chunk_size` 200–500, size the finalize worker for the full results object, and ingest via **`prompts_key`**.

## Code map

| Concern | Primary modules |
|---|---|
| Defaults | `app/core/config.py`, `.env.example` |
| Ingest / chunk planning | `app/services/batches.py`, `app/api/routes.py`, `app/api/schemas.py` |
| Spaces I/O / concatenate | `app/core/spaces.py` |
| Scatter + finalize jobs | `app/workers/jobs.py`, `app/workers/main.py` |
| Rate limit + concurrency gate | `app/rate_limit/__init__.py` |
