# Scale Architecture

Status: show-readiness documentation (Phase 3). This describes the topology
Assure's pipeline is prepared for — it is architecture, not a build order.
For the show, everything runs on one EC2 instance with local storage; the
pieces below are how the same code scales when volume grows.

## 1. What runs where (show)

One EC2 instance runs the Flask app, SQLite, the OMP artifact store (local
disk or attached EBS), and the verification passes. There is no connection
back to a developer machine — "local" always means local to the EC2
instance. JDF CI is preferred when ops installs the binary; when it is not
present, the parser router still runs and selects Textract, which is itself
part of the demo (the routing decision is the architecture, not a fallback
apology).

## 2. Component concurrency (prepared for growth)

| Component | Concurrency | Notes |
|---|---|---|
| Parse workers (JDF CI) | 10–50 parallel | JDF CI is fast (~1–5s per doc); high concurrency OK |
| Parse workers (Textract) | 5–10 parallel | Textract is slower and costs money; limit concurrency |
| Z3 workers (sync) | 5–20 parallel | Z3 is compute-intensive; moderate concurrency |
| Z3 workers (async queue) | Worker pool, scalable | For >50-page documents (post-show) |
| Vault writes (SQLite) | Single writer | SQLite serializes writes; fine for metadata at moderate scale |
| OMP writes (S3) | Parallel OK | S3 handles concurrent writes (future client phase) |

## 3. Queue topology (description, not a show build)

```
+-----------+     +-------------------+     +------------+
|  Ingest   |---->|  Parse Workers    |---->|  Vault +   |
|  Queue    |     |  (JDF + Textract) |     |  OMP Store |
+-----------+     +-------------------+     +------------+
                                                |
                                                v
                                        +------------+
                                        |  Z3 Queue  |
                                        +------------+
                                                |
                                                v
                                        +------------+
                                        |  Z3 Workers|
                                        +------------+
```

For the show this topology is described, not implemented: the pipeline is
already shaped as stages (route → parse → verify → persist) that map onto
these queues without rework. The verification hook
(`services/verification.run_verification_after_parse`) is the seam an async
Z3 worker would replace once the write-back storage contract exists.

## 4. Batch processing model

For large document volumes (e.g., ten years of insurance documentation):

1. **Ingest batch** — documents enter the pipeline (upload, pull from storage).
2. **Parse batch** — workers pull from the queue and parse in parallel
   (JDF CI or Textract per the router's decision).
3. **Verify batch** — Z3 + Red-Hat run on parsed documents (sync for small,
   async for large).
4. **Persist batch** — vault rows + OMP artifacts written.
5. **Report** — progress visible: X documents parsed, Y verified, Z remaining.

## 5. SQLite scaling note

SQLite is fine for metadata rows (vault rows, OMP metadata) up to ~10M rows
comfortably. For very large volumes (100M+ documents):

- SQLite becomes a write bottleneck (single writer).
- Options: split by project, use read replicas, or migrate to
  PostgreSQL/RDS at scale.
- Now: acceptable for the show and moderate scale.
- Future: plan the migration; nothing in the schema blocks it.

## 6. Cost controls

| Resource | Cost concern | Control |
|---|---|---|
| Textract | Per-page OCR cost | Routing accuracy — the parser router (Phase 1) sends only text-layer-less documents to Textract |
| Z3 compute | Compute time | `Z3_TIMEOUT_SECONDS` (30s) and sync concurrency limits |
| S3 storage | Storage volume | Lifecycle policies, retention rules (future client phase) |

Routing accuracy is the primary cost control: Textract runs only when the
probe finds no text layer.

## 7. S3 readiness (interface, not persistence)

`store_omp_artifact` is parameterized (`storage_backend`, `s3_bucket`,
`s3_prefix`), configured from `ASSURE_S3_BACKEND` / `ASSURE_S3_BUCKET` /
`ASSURE_S3_PREFIX` (default: local). The S3 backend is a **stub**: it logs
the `s3://bucket/prefix/{project_id}/{artifact_id}.json` key it would write
and falls back to local storage. Switching to the client's bucket later is a
config change plus one real write implementation — not a rewrite. The vault
row's `omp_artifact_id` field is a plain text column: it stores the local
`artifact_id` today and can store an `s3://bucket/key` URI in the future
with no schema change.

## 8. Show readiness checklist

Ops-owned: EC2 instance, SSH/security groups, package install, Flask launch,
JDF CI binary (or Textract credentials), Z3 available, env vars set
(`DATABASE_PATH`, `TEMP_UPLOAD_DIR`, `LOG_LEVEL`, and the `ASSURE_S3_*` trio
if the client backend is being previewed).

Cline-owned verification: run the test suite, demonstrate the show flow —
clean PDF → JDF CI → high-confidence, scanned → Textract → lower
confidence, corrupt file → clear error, OMP artifact inspectable on the
instance disk, confidence report visible in the workbench. If a runtime
dependency is missing on EC2, the documented fallback applies (JDF CI absent
→ Textract; Z3/Red-Hat absent → explicit verification status) so the demo
succeeds regardless.