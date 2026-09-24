# ---- Cluster, roles, logs ----------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = "${var.project}-${var.environment}"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.project}-${var.environment}"
  retention_in_days = 30
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.project}-${var.environment}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.app.arn]
    }]
  })
}

# What the application itself may touch: its bucket, its queues, Textract.
resource "aws_iam_role" "task" {
  name               = "${var.project}-${var.environment}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = ["${aws_s3_bucket.objects.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.objects.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility",
          "sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:ListQueues", "sqs:CreateQueue"
        ]
        Resource = concat([for q in aws_sqs_queue.queues : q.arn], [aws_sqs_queue.dead_letter.arn])
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:ListQueues"]
        Resource = ["*"]
      },
      {
        # Only reached when jdf-cli's OCR fails or PARSER_SCAN_BACKEND=textract.
        Effect   = "Allow"
        Action   = ["textract:AnalyzeDocument", "textract:DetectDocumentText"]
        Resource = ["*"]
      }
    ]
  })
}

# ---- Secrets -----------------------------------------------------------------
resource "random_password" "secret_key" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.project}/${var.environment}/app"
  recovery_window_in_days = var.environment == "production" ? 30 : 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode(merge(
    {
      DATABASE_URL   = "postgresql://assure:${random_password.db.result}@${aws_db_instance.main.address}:5432/assure"
      REDIS_URL      = "redis://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0"
      PEM_SECRET_KEY = random_password.secret_key.result
    },
    var.app_secrets,
  ))
}

# ---- Task definitions --------------------------------------------------------
locals {
  common_env = merge(
    {
      ENVIRONMENT               = var.environment
      ASSURE_ENV                = var.environment
      AWS_DEFAULT_REGION        = var.region
      ASSURE_S3_BUCKET          = aws_s3_bucket.objects.bucket
      ASSURE_S3_PREFIX          = "assure/"
      ASSURE_DATA_DIR           = "/tmp/assure"
      TEMP_UPLOAD_DIR           = "/tmp/assure/tmp_uploads"
      CELERY_BROKER_URL         = "sqs://"
      CELERY_SQS_QUEUE_PREFIX   = "${var.project}-${var.environment}-"
      CELERY_SQS_VISIBILITY_TIMEOUT = "1800"
      PARSE_ASYNC               = "1"
      SUBSTRATE_ASYNC_UPLOAD    = "1"
      PARSER_SCAN_BACKEND       = "jdf-ocr"
      JDF_OCR                   = "tesseract"
      ASSURE_LOG_STDOUT         = "1"
      PROXY_FIX_HOPS            = "1"
      PYTHONUNBUFFERED          = "1"
    },
    var.app_env,
  )
  secret_keys = concat(["DATABASE_URL", "REDIS_URL", "PEM_SECRET_KEY"], keys(var.app_secrets))
  # CELERY_RESULT_BACKEND must equal REDIS_URL; a secret cannot reference another,
  # so the worker/web read REDIS_URL and celery_app falls back to it when the
  # broker is SQS? No — it falls back to PostgreSQL. Point results at Redis
  # explicitly via the same secret key.
  container_secrets = concat(
    [for k in local.secret_keys : { name = k, valueFrom = "${aws_secretsmanager_secret.app.arn}:${k}::" }],
    [{ name = "CELERY_RESULT_BACKEND", valueFrom = "${aws_secretsmanager_secret.app.arn}:REDIS_URL::" }],
  )
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.project}-${var.environment}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  ephemeral_storage {
    size_in_gib = 21
  }
  container_definitions = jsonencode([{
    name      = "web"
    image     = var.image
    essential = true
    portMappings = [{ containerPort = 8765, protocol = "tcp" }]
    environment = concat(
      [for k, v in local.common_env : { name = k, value = v }],
      [
        { name = "PORT", value = "8765" },
        { name = "GUNICORN_WORKERS", value = "2" },
        { name = "GUNICORN_THREADS", value = "8" },
        { name = "GUNICORN_TIMEOUT", value = "120" },
      ]
    )
    secrets = local.container_secrets
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/ready',timeout=3)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "web"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project}-${var.environment}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  ephemeral_storage {
    size_in_gib = 30
  }
  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.image
    essential = true
    command = [
      "sh", "-c",
      "exec celery -A prompt_matrix.celery_app:celery_app worker --loglevel=info --concurrency=${var.worker_concurrency} --prefetch-multiplier=1 -Q parse,default"
    ]
    environment = [for k, v in local.common_env : { name = k, value = v }]
    secrets     = local.container_secrets
    # Graceful drain: SQS visibility 1800 s > the longest OCR job; ECS gives the
    # task this long to finish in-flight work before SIGKILL on scale-in / Spot reclaim.
    stopTimeout = 120
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "worker"
      }
    }
  }])
}

# ---- Load balancer -----------------------------------------------------------
resource "aws_lb" "main" {
  name               = "${var.project}-${var.environment}"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = module.vpc.public_subnets
  idle_timeout       = 180 # SSE streams (draft/inquire) send a keepalive every 12 s
}

resource "aws_lb_target_group" "web" {
  name        = "${var.project}-${var.environment}-web"
  port        = 8765
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = module.vpc.vpc_id
  deregistration_delay = 30
  health_check {
    path                = "/ready"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  dynamic "default_action" {
    for_each = var.acm_certificate_arn != "" ? [1] : []
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
  dynamic "default_action" {
    for_each = var.acm_certificate_arn == "" ? [1] : []
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.web.arn
    }
  }
}

resource "aws_lb_listener" "https" {
  count             = var.acm_certificate_arn != "" ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

# ---- Services ----------------------------------------------------------------
resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.web_min_count
  launch_type     = "FARGATE"
  platform_version = "LATEST"
  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8765
  }
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  health_check_grace_period_seconds = 60
  lifecycle {
    ignore_changes = [desired_count]
  }
  depends_on = [aws_lb_listener.http]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_min_count
  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }
  capacity_provider_strategy {
    capacity_provider = var.worker_use_spot ? "FARGATE_SPOT" : "FARGATE"
    weight            = 1
    base              = 0
  }
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200
  lifecycle {
    ignore_changes = [desired_count]
  }
}
