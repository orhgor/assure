# Edge PDF worker — deploy and lifecycle

## Deploy

```bash
cd worker
npm ci
npx wrangler deploy --env staging
npx wrangler deploy --env production
```

## Secrets (per environment)

```bash
npx wrangler secret put EC2_BACKEND_URL --env production
npx wrangler secret put SUBSTRATE_INGEST_SECRET --env production
npx wrangler secret put AWS_ACCESS_KEY_ID --env production
npx wrangler secret put AWS_SECRET_ACCESS_KEY --env production
npx wrangler secret put AWS_REGION --env production
```

Repeat for `--env staging` with staging EC2 URL and credentials.

## R2 buckets

- Staging: `assure-pdf-uploads-staging`
- Production: `assure-pdf-uploads-prod`

Create buckets:

```bash
npx wrangler r2 bucket create assure-pdf-uploads-staging
npx wrangler r2 bucket create assure-pdf-uploads-prod
```

## Lifecycle (safety net — delete after 1 day)

```bash
npx wrangler r2 bucket lifecycle add assure-pdf-uploads-prod --expire-days 1
npx wrangler r2 bucket lifecycle add assure-pdf-uploads-staging --expire-days 1
```

## EC2 env

Set on staging/production `.env`:

```
ASSURE_EDGE_WORKER_URL=https://assure-worker-prod.<account>.workers.dev
SUBSTRATE_INGEST_SECRET=<same as worker secret>
```

## Limits

| Limit | Value |
|-------|-------|
| File size | 10 MB |
| Pages per document | 5 |
| Pages per project | 50 |
| Pages per day (Textract control) | 20 |
