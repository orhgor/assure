# One VPC, two AZs. Tasks and the database sit in private subnets; the ALB in
# public ones. A single NAT gateway keeps the fixed cost at one gateway (~$35/mo);
# S3, ECR, CloudWatch and Secrets Manager go through VPC endpoints so the
# parse tier's traffic (uploads, images, logs) never pays NAT data charges.
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name = "${var.project}-${var.environment}"
  cidr = "10.40.0.0/16"

  azs             = ["${var.region}a", "${var.region}b"]
  public_subnets  = ["10.40.0.0/24", "10.40.1.0/24"]
  private_subnets = ["10.40.10.0/24", "10.40.11.0/24"]

  enable_nat_gateway     = true
  single_nat_gateway     = true
  enable_dns_hostnames   = true
  enable_dns_support     = true
}

resource "aws_security_group" "alb" {
  name   = "${var.project}-${var.environment}-alb"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "tasks" {
  name   = "${var.project}-${var.environment}-tasks"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port       = 8765
    to_port         = 8765
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "data" {
  name   = "${var.project}-${var.environment}-data"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }
  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids
}

resource "aws_security_group" "endpoints" {
  name   = "${var.project}-${var.environment}-endpoints"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [module.vpc.vpc_cidr_block]
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(["ecr.api", "ecr.dkr", "logs", "secretsmanager", "sqs"])

  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
}
