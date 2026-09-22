# Assure on AWS (Terraform)

One stack, cost-first, all Graviton (ARM64):

| Tier | Service | Default size | Scales on |
|---|---|---|---|
| Web | ECS Fargate, `web` service | 2 × 0.5 vCPU / 1 GB | CPU 60 %, 400 req/target |
| Parse + verify | ECS Fargate On-Demand (`worker_use_spot=true` for Spot), `worker` service | 0–10 × 1 vCPU / 2 GB | SQS `parse` queue depth (scale to zero) |
| Database | RDS PostgreSQL 16 | db.t4g.micro, 20 GB gp3, 7-day backups | manual class change |
| Shared state | ElastiCache Redis 7 | cache.t4g.micro | — |
| Queue | SQS `parse`, `default` + DLQ | — | — |
| Objects | S3 (uploads expire in 1 day, artifacts → IA after 30 d) | — | — |
| Edge | ALB (idle 180 s for SSE) | — | — |

The same image runs both services; the worker only overrides the command.

## Deploy

```bash
cd infra/terraform
terraform init
terraform apply \
  -var environment=staging \
  -var image=ghcr.io/orhgor/assure-app:<sha> \
  -var 'app_secrets={OPENROUTER_API_KEY="...",CLERK_SECRET_KEY="..."}' \
  -var 'app_env={ASSURE_USE_FREE_MODELS="1"}'
```

`DATABASE_URL`, `REDIS_URL` and `PEM_SECRET_KEY` are generated and stored in
Secrets Manager; the tasks receive them as environment. First boot runs the
schema migrations (`db/connection.init_db`, idempotent).

Point the domain at `alb_dns_name` (CNAME) and pass `domain_name` +
`acm_certificate_arn` for HTTPS. CloudFront in front of the ALB is optional
and only worth it for static-asset caching.

## Local equivalent

`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`
runs the identical topology on one box: PostgreSQL, Redis (as broker), web,
worker. Only the broker differs (Redis locally, SQS on AWS); the application
reads both from `CELERY_BROKER_URL`.

## Cost shape (eu-central-1, on-demand list prices, approximate)

Fixed: ALB (~$20), NAT gateway (~$35), RDS db.t4g.micro (~$14), ElastiCache
cache.t4g.micro (~$13), 2 web tasks 0.5 vCPU/1 GB ARM (~$25). Variable: worker
On-Demand vCPU-hours (~$0.05/vCPU-h on ARM incl. 2 GB → a 10-page OCR scan
costs well under a cent), S3 and SQS at request rates that round to zero,
Textract only as a fallback ($1.50 per 1 000 pages for text, more with tables).
Run the pricing calculator for the exact figure before committing.
