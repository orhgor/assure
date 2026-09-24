# ---- Web: CPU + request count -----------------------------------------------
resource "aws_appautoscaling_target" "web" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.web.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.web_min_count
  max_capacity       = var.web_max_count
}

resource "aws_appautoscaling_policy" "web_cpu" {
  name               = "web-cpu-60"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.web.service_namespace
  resource_id        = aws_appautoscaling_target.web.resource_id
  scalable_dimension = aws_appautoscaling_target.web.scalable_dimension
  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_policy" "web_requests" {
  name               = "web-requests-per-target"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.web.service_namespace
  resource_id        = aws_appautoscaling_target.web.resource_id
  scalable_dimension = aws_appautoscaling_target.web.scalable_dimension
  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.main.arn_suffix}/${aws_lb_target_group.web.arn_suffix}"
    }
    target_value       = 400
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

# ---- Worker: parse queue depth ----------------------------------------------
# One task per `worker_concurrency` documents waiting. Scales to zero when the
# queue has been empty for 10 minutes; a first message brings one task back
# within about a minute (Fargate pull + boot).
resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.worker_min_count
  max_capacity       = var.worker_max_count
}

resource "aws_appautoscaling_policy" "worker_out" {
  name               = "worker-scale-out"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 10
      scaling_adjustment          = 1
    }
    step_adjustment {
      metric_interval_lower_bound = 10
      metric_interval_upper_bound = 50
      scaling_adjustment          = 3
    }
    step_adjustment {
      metric_interval_lower_bound = 50
      scaling_adjustment          = 6
    }
  }
}

resource "aws_appautoscaling_policy" "worker_in" {
  name               = "worker-scale-in"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 300
    metric_aggregation_type = "Maximum"
    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = var.worker_min_count
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "parse_backlog" {
  alarm_name          = "${var.project}-${var.environment}-parse-backlog"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.queues["parse"].name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [aws_appautoscaling_policy.worker_out.arn]
}

resource "aws_cloudwatch_metric_alarm" "parse_idle" {
  alarm_name          = "${var.project}-${var.environment}-parse-idle"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.queues["parse"].name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 10
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_appautoscaling_policy.worker_in.arn]
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${var.project}-${var.environment}-dlq"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dead_letter.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_description   = "A parse/compile task failed three times; inspect the DLQ."
}
