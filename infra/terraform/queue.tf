# Celery broker on AWS: SQS. Zero idle cost, at-least-once delivery matched by
# the tasks' acks_late + idempotent object keys, and a native CloudWatch metric
# (ApproximateNumberOfMessagesVisible) the worker tier autoscales on. Celery's
# SQS transport names queues "<prefix><queue>"; the app routes parse work to
# "parse" and everything else to "default".
resource "aws_sqs_queue" "dead_letter" {
  name                      = "${var.project}-${var.environment}-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "queues" {
  for_each = toset(["parse", "default"])

  name                       = "${var.project}-${var.environment}-${each.key}"
  visibility_timeout_seconds = each.key == "parse" ? 1800 : 900
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 3
  })
}
