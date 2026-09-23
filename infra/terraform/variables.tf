variable "project" {
  type    = string
  default = "assure"
}

variable "environment" {
  description = "production | staging"
  type        = string
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "image" {
  description = "Container image for web and worker (ghcr.io/... or the ECR repo created here). Must be built for linux/arm64."
  type        = string
}

variable "domain_name" {
  description = "Optional. When set together with acm_certificate_arn the ALB listens on 443 and redirects 80."
  type        = string
  default     = ""
}

variable "acm_certificate_arn" {
  type    = string
  default = ""
}

# ---- sizing: cost-first defaults, all Graviton ------------------------------

variable "web_cpu" {
  type    = number
  default = 512 # 0.5 vCPU
}

variable "web_memory" {
  type    = number
  default = 1024
}

variable "web_min_count" {
  type    = number
  default = 2
}

variable "web_max_count" {
  type    = number
  default = 6
}

variable "worker_cpu" {
  type    = number
  default = 1024 # tesseract is single-threaded per document; 1 vCPU per concurrency slot
}

variable "worker_memory" {
  type    = number
  default = 2048
}

variable "worker_concurrency" {
  type    = number
  default = 1
}

variable "worker_min_count" {
  description = "0 lets the worker tier scale to zero when the parse queue is empty."
  type        = number
  default     = 0
}

variable "worker_max_count" {
  type    = number
  default = 10
}

variable "worker_use_spot" {
  description = "Run workers on FARGATE_SPOT instead of On-Demand. Default false (customer decision 2026-09-22: everything On-Demand); the tasks are idempotent and acks_late, so Spot is safe to enable later."
  type        = bool
  default     = false
}

variable "db_instance_class" {
  description = "RDS PostgreSQL instance class. db.t4g.micro is the cheapest Graviton class; db.t4g.small/medium for production load."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 20
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "app_env" {
  description = "Extra non-secret environment for both services (provider settings, feature flags)."
  type        = map(string)
  default     = {}
}

variable "app_secrets" {
  description = "Secret environment written to Secrets Manager and injected into the tasks (API keys, Clerk, Stripe...). Values are sensitive."
  type        = map(string)
  default     = {}
  sensitive   = true
}
