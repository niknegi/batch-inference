from prometheus_client import Counter, Gauge, Histogram

INFERENCE_REQUESTS = Counter(
    "batch_inference_requests_total",
    "Total inference requests",
    ["provider", "model", "status"],
)
INFERENCE_LATENCY = Histogram(
    "batch_inference_latency_seconds",
    "Inference latency",
    ["provider", "model"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
RATE_LIMIT_WAITS = Counter(
    "batch_rate_limit_waits_total",
    "Times workers waited on rate limiter",
    ["key"],
)
CHUNKS_INFLIGHT = Gauge(
    "batch_chunks_inflight",
    "Chunks currently leased",
)
WEBHOOK_DELIVERIES = Counter(
    "batch_webhook_deliveries_total",
    "Webhook delivery attempts",
    ["status"],
)
BATCHES_TOTAL = Counter(
    "batch_jobs_total",
    "Batches by terminal status",
    ["status"],
)
