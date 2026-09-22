# ---- PostgreSQL (RDS) --------------------------------------------------------
# db.t4g.micro is enough for the metadata workload (rows, not blobs: documents
# live in S3). Storage autoscaling absorbs growth; automated backups replace
# the old SQLite→R2 cron. Move to Aurora Serverless v2 only when connections or
# IOPS say so — nothing in the schema changes.
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-${var.environment}"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_db_parameter_group" "pg16" {
  name   = "${var.project}-${var.environment}-pg16"
  family = "postgres16"
  parameter {
    name  = "log_min_duration_statement"
    value = "500"
  }
  parameter {
    name  = "idle_in_transaction_session_timeout"
    value = "60000"
  }
}

resource "aws_db_instance" "main" {
  identifier                   = "${var.project}-${var.environment}"
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = var.db_instance_class
  allocated_storage            = var.db_allocated_storage_gb
  max_allocated_storage        = var.db_allocated_storage_gb * 5
  storage_type                 = "gp3"
  storage_encrypted            = true
  db_name                      = "assure"
  username                     = "assure"
  password                     = random_password.db.result
  db_subnet_group_name         = aws_db_subnet_group.main.name
  vpc_security_group_ids       = [aws_security_group.data.id]
  parameter_group_name         = aws_db_parameter_group.pg16.name
  multi_az                     = var.db_multi_az
  publicly_accessible          = false
  backup_retention_period      = 7
  deletion_protection          = var.environment == "production"
  skip_final_snapshot          = var.environment != "production"
  performance_insights_enabled = false
  apply_immediately            = var.environment != "production"
}

# ---- Redis (ElastiCache) -----------------------------------------------------
# Rate-limit counters, Red-Hat debounce locks and Celery task results. One
# cache.t4g.micro; the broker itself is SQS (see queue.tf) so the app keeps
# working through a Redis failover with only rate limits degraded.
resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project}-${var.environment}"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "${var.project}-${var.environment}"
  description                = "Assure shared state"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  num_cache_clusters         = 1
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.data.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = false
  automatic_failover_enabled = false
  apply_immediately          = true
}

# ---- Object storage ----------------------------------------------------------
# uploads/  — staged documents, deleted by the worker, expired after 1 day anyway
# omp/, jdf/ — artifact mirrors, cheaper storage class after 30 days
resource "aws_s3_bucket" "objects" {
  bucket        = "${var.project}-${var.environment}-objects-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.environment != "production"
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket_public_access_block" "objects" {
  bucket                  = aws_s3_bucket.objects.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "objects" {
  bucket = aws_s3_bucket.objects.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "objects" {
  bucket = aws_s3_bucket.objects.id
  rule {
    id     = "expire-staged-uploads"
    status = "Enabled"
    filter {
      prefix = "assure/uploads/"
    }
    expiration {
      days = 1
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
  rule {
    id     = "tier-artifacts"
    status = "Enabled"
    filter {
      prefix = "assure/omp/"
    }
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
  }
}

# Browser-direct presigned PUTs need CORS from the app origin.
resource "aws_s3_bucket_cors_configuration" "objects" {
  bucket = aws_s3_bucket.objects.id
  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = var.domain_name != "" ? ["https://${var.domain_name}"] : ["*"]
    allowed_headers = ["Content-Type"]
    max_age_seconds = 3600
  }
}
