# Single EC2 (Graviton) deploy with `docker compose up`

One arm64 instance runs the whole stack from `docker-compose.yml`: PostgreSQL,
Redis, Ollama (the models, downloaded once into a volume), web, parse worker,
shell gate. No model provider key and no cloud model: every LLM call stays on
the box (user decision 2026-09-25). AWS is optional and only for files (S3) and
the Textract fallback, with one IAM user key pair in `.env` or an instance role.

## 1. Instance

| Item | Choice | Why |
|---|---|---|
| Family | Graviton: `m7g.large` (2 vCPU / 8 GB) minimum, `m7g.xlarge` (4 vCPU / 16 GB) recommended, `m7g.2xlarge` for `qwen2.5:7b` | web + worker + PostgreSQL + Redis ≈ 2 GB; the default 1–2B models take ~1.5 GB each when loaded; a 7B model ~4.7 GB. OCR is CPU-bound (~3 s/page/vCPU) and model inference shares the same vCPUs |
| AMI | Ubuntu 24.04 LTS **arm64** | the app image is built linux/arm64 |
| Disk | gp3 100 GB | PostgreSQL + Docker images (ollama image ~1 GB, app ~1.8 GB) + models (~2.3 GB default, ~5 GB with a 7B) + `./data` scratch |
| Security group | inbound 22 from your IP, 443/80 from the world **only if** a reverse proxy runs on the box; otherwise nothing public and a Cloudflare tunnel to :8891 | 8891/8765 are never exposed directly |
| IAM | optional — an IAM **user** with the runtime policy (§3) or an instance role; needed only for S3 / Textract | without it files stay under `./data/objects` on the instance |

## 2. Host setup

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu && newgrp docker
git clone https://github.com/orhgor/assure.git && cd assure
```

## 3. IAM (optional — S3 and the Textract fallback only)

Skip this section to run with local files only. Two ways; both attach the same policy (`scripts/aws/iam-policy-assure-runtime.json`).

**A. IAM user with an access key** (what goes into `.env` as `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`; used for S3 and Textract):

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
| AssureBedrockOptional | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:Converse`, `bedrock:ConverseStream` | `*` | only if you switch `ASSURE_LLM_BACKEND=bedrock`; unused with the default local models |

Also needed, not in the policy: **`sts:GetCallerIdentity`** is allowed for every
principal by default (the Sources panel calls it). SSM access (`AmazonSSMManagedInstanceCore`,
managed policy) if you want `scripts/aws/_box.sh` style remote commands instead of SSH.

**Models:** nothing to do here for the default. The `ollama` service serves
`ASSURE_OLLAMA_MODEL` / `ASSURE_OLLAMA_MODEL_B` from `.env`; `ollama-pull`
downloads them on the first `docker compose up` (outbound HTTPS to ollama.com
once, ~2.3 GB) and only verifies them afterwards. To use Amazon Bedrock instead,
set `ASSURE_LLM_BACKEND=bedrock` and request access to the two models in the
Bedrock console for the region (`ASSURE_BEDROCK_MODEL(_B)`); without that
access the first compile answers 424 naming the model.

## 4. S3 bucket

Private bucket in the same region, default encryption on, no public access.
Lifecycle rule (optional): expire `assure/uploads/` after 1 day — staged
uploads are deleted by the worker after a successful parse anyway. Nothing else.

## 5. Environment and start

```bash
./scripts/gen-env.sh ec2          # writes .env with generated secrets; optional:
#   ASSURE_S3_BUCKET=<bucket>   AWS_DEFAULT_REGION=<region>
docker compose up -d --build      # or APP_IMAGE=ghcr.io/orhgor/assure-app:<sha> in .env to pull
docker compose logs -f ollama-pull   # first start: "pulling qwen2.5:1.5b" … "models ready"
docker compose ps                    # ollama-pull Exited (0); app + worker healthy after it
curl -s http://127.0.0.1:8765/ready
docker compose exec ollama ollama list   # the two models
```

The first start downloads the models before `assure-app` / `assure-worker`
come up (a few minutes on a typical EC2 link); later starts only check the
volume and need no network. `docker compose down` keeps the `ollama` volume;
`docker compose down -v` deletes it and the next start pulls again.

The shell listens on `0.0.0.0:80` (`SHELL_BIND` / `SHELL_PORT` in `.env`) behind
the `SHELL_ACCESS_KEY` the script generated (`grep SHELL_ACCESS_KEY .env`);
security group: inbound 80 (and 22 from your IP). For TLS put a Cloudflare
tunnel or caddy on 443 in front. The API port 8765 stays on 127.0.0.1.

## 6. Checks

- `docker compose ps`: `ollama-pull` is `Exited (0)`, the other six services are `healthy`/`running`.
- `curl -s http://127.0.0.1:8765/health | python3 -m json.tool | grep -A6 '"models"'`: `status: ok` with both models under `present`; `missing` or `unreachable` makes `/health` report `degraded`.
- Upload a PDF or a phone photo on the Sources panel: Processing panel shows `jdf-cli`, Z3 verdict; the Parsure page (`/parsing`) shows quality and fields; object under `s3://<bucket>/assure/…` when S3 is set, else `./data/objects`.
- Compile: first frame `Drafting with ollama/qwen2.5:1.5b`; minutes on CPU.
- With S3: `GET /api/integrations/aws` (through the shell, with the key): `reachable: true`, `credential_source: keys` or `role`.

## 7. Updating

```bash
git pull && docker compose up -d --build
```
PostgreSQL data lives in the `pgdata` volume; back it up with `pg_dump` from the
`postgres` container or move to RDS by setting `DATABASE_URL` in `.env`.
