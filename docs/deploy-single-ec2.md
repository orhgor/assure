# Single EC2 (Graviton) deploy with `docker compose up`

One arm64 instance runs the whole stack from `docker-compose.yml`: PostgreSQL,
Redis, web, parse worker, shell gate. Models come from Amazon Bedrock and files go to S3, both with the one IAM user
key pair in `.env` — no provider key; no instance role is needed.

## 1. Instance

| Item | Choice | Why |
|---|---|---|
| Family | Graviton: `m7g.large` (2 vCPU / 8 GB) minimum, `m7g.xlarge` (4 vCPU / 16 GB) comfortable, `c7g.xlarge` if parse-heavy | web + worker + PostgreSQL + Redis fit in 8 GB; OCR is CPU-bound (~3 s/page/vCPU) |
| AMI | Ubuntu 24.04 LTS **arm64** | the app image is built linux/arm64 |
| Disk | gp3 100 GB | PostgreSQL + Docker images + `./data` scratch (uploads are transient; documents live in S3) |
| Security group | inbound 22 from your IP, 443/80 from the world **only if** a reverse proxy runs on the box; otherwise nothing public and a Cloudflare tunnel to :8891 | 8891/8765 are never exposed directly |
| IAM | an IAM **user** with the runtime policy (§3); its access key + secret go into `.env` (no instance role) | S3 + Textract + Bedrock with one key pair |

## 2. Host setup

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu && newgrp docker
git clone https://github.com/orhgor/assure.git && cd assure
```

## 3. IAM (the only AWS work)

Two ways; both attach the same policy (`scripts/aws/iam-policy-assure-runtime.json`).

**A. IAM user with an access key** (what goes into `.env` as `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`; used for S3, Textract and Bedrock alike):

```bash
aws iam create-user --user-name assure-app
PRINCIPAL=user ASSURE_S3_BUCKET=<bucket> bash scripts/aws/attach-runtime-role.sh assure-app
aws iam create-access-key --user-name assure-app      # copy AccessKeyId + SecretAccessKey into .env
```
Rotate by creating a second key, updating `.env`, `docker compose up -d`, then deleting the old key.

**B. Instance role** (no keys anywhere): create a role for EC2 (trust policy `ec2.amazonaws.com`), attach it to the
instance as an instance profile, then attach the runtime policy:

```bash
# from your laptop with admin credentials
aws iam create-role --role-name assure-ec2-runtime \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam create-instance-profile --instance-profile-name assure-ec2-runtime
aws iam add-role-to-instance-profile --instance-profile-name assure-ec2-runtime --role-name assure-ec2-runtime
aws ec2 associate-iam-instance-profile --instance-id i-XXXXXXXX --iam-instance-profile Name=assure-ec2-runtime

ASSURE_S3_BUCKET=<bucket> CELERY_SQS_QUEUE_PREFIX=assure- \
  bash scripts/aws/attach-runtime-role.sh assure-ec2-runtime
```

`scripts/aws/iam-policy-assure-runtime.json` is what gets attached in both cases (inline policy `assure-runtime`):

| Sid | Actions | Resource | Used for |
|---|---|---|---|
| AssureObjectsBucket | `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:AbortMultipartUpload` | `arn:aws:s3:::<bucket>/*` | uploads, JDF mirror, OMP artifacts |
| AssureObjectsBucketList | `s3:ListBucket` | `arn:aws:s3:::<bucket>` | Sources panel probe (HeadBucket), listings |
| AssureQueues / AssureQueueDiscovery | `sqs:*Message*`, `GetQueueAttributes`, `GetQueueUrl`, `CreateQueue`, `ListQueues` | `arn:aws:sqs:<region>:<acct>:assure-*` | only if `CELERY_BROKER_URL=sqs://`; with Redis (compose default) not exercised |
| AssureTextractFallback | `textract:DetectDocumentText`, `textract:AnalyzeDocument` | `*` | scans whose OCR read nothing |
| AssureBedrockOptional | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:Converse`, `bedrock:ConverseStream` | `*` | all model calls with `ASSURE_LLM_BACKEND=bedrock` |

Also needed, not in the policy: **`sts:GetCallerIdentity`** is allowed for every
principal by default (the Sources panel calls it). SSM access (`AmazonSSMManagedInstanceCore`,
managed policy) if you want `scripts/aws/_box.sh` style remote commands instead of SSH.

**Bedrock model access:** in the Bedrock console for the region, request access
to the two models in `.env` (`ASSURE_BEDROCK_MODEL`, `ASSURE_BEDROCK_MODEL_B`;
defaults are the EU cross-region profiles for Claude Sonnet 4 and Claude 3.5
Haiku). Without it the first compile answers 424 naming the model.

## 4. S3 bucket

Private bucket in the same region, default encryption on, no public access.
Lifecycle rule (optional): expire `assure/uploads/` after 1 day — staged
uploads are deleted by the worker after a successful parse anyway. Nothing else.

## 5. Environment and start

```bash
./scripts/gen-env.sh ec2          # writes .env with generated secrets; fill in:
#   ASSURE_S3_BUCKET=<bucket>   AWS_DEFAULT_REGION=<region>
docker compose up -d --build      # or APP_IMAGE=ghcr.io/orhgor/assure-app:<sha> in .env to pull
docker compose ps
curl -s http://127.0.0.1:8765/ready
```

The shell listens on `0.0.0.0:8891` (`SHELL_BIND` in `.env`) behind the
`SHELL_ACCESS_KEY` the script generated (`grep SHELL_ACCESS_KEY .env`). Put TLS
in front: Cloudflare tunnel to `http://localhost:8891`, or nginx/caddy on 443.
The API port 8765 stays on 127.0.0.1.

## 6. Checks

- `GET /api/integrations/aws` (through the shell, with the key): `reachable: true`, `credential_source: role`.
- Upload a PDF on the Sources panel: Processing panel shows `jdf-cli`, Z3 verdict; object under `s3://<bucket>/assure/…`.
- Compile: first frame `Drafting with bedrock/…`.

## 7. Updating

```bash
git pull && docker compose up -d --build
```
PostgreSQL data lives in the `pgdata` volume; back it up with `pg_dump` from the
`postgres` container or move to RDS by setting `DATABASE_URL` in `.env`.
