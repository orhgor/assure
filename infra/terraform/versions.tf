terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
  }
  # Remote state: create the bucket/table once, then uncomment.
  # backend "s3" {
  #   bucket         = "assure-terraform-state"
  #   key            = "assure/${var.environment}/terraform.tfstate"
  #   region         = "eu-central-1"
  #   dynamodb_table = "assure-terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
