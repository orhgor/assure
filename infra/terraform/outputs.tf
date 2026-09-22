output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "db_endpoint" {
  value = aws_db_instance.main.address
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "objects_bucket" {
  value = aws_s3_bucket.objects.bucket
}

output "parse_queue" {
  value = aws_sqs_queue.queues["parse"].name
}

output "secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}
