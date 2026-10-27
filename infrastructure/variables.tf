variable "project" {
  type    = string
  default = "market-ai"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "notification_email" {
  type    = string
  default = ""
}

# ── New variables for v2 platform ─────────────────────────────────────────────

variable "ecr_image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag to deploy from ECR"
}

variable "rds_instance_class" {
  type        = string
  default     = "db.t4g.micro"
  description = "RDS instance class (cost-conscious default)"
}

variable "rds_allocated_storage" {
  type        = number
  default     = 20
  description = "RDS storage in GB"
}

variable "rds_username" {
  type        = string
  default     = "market_ai"
  description = "RDS master username (password via Secrets Manager)"
}

variable "ecs_task_cpu" {
  type        = number
  default     = 512
  description = "Fargate task CPU units"
}

variable "ecs_task_memory" {
  type        = number
  default     = 1024
  description = "Fargate task memory in MB"
}

variable "enable_rds" {
  type        = bool
  default     = false
  description = "Set true to provision RDS PostgreSQL (incurs cost)"
}

variable "enable_ecs" {
  type        = bool
  default     = true
  description = "Set true to provision ECS cluster and task definitions"
}

variable "vpc_id" {
  type        = string
  default     = "vpc-3110f058"
  description = "Existing VPC ID for ECS tasks"
}

variable "private_subnet_ids" {
  type        = list(string)
  default     = ["subnet-1d896974", "subnet-31995e4a", "subnet-0dcfed47"]
  description = "Subnet IDs for ECS Fargate tasks (public subnets, default VPC)"
}

variable "log_retention_days" {
  type        = number
  default     = 30
  description = "CloudWatch log retention in days"
}

variable "telegram_bot_token" {
  type        = string
  default     = ""
  description = "Telegram bot token for notifications (injected from CI secrets)"
  sensitive   = true
}

variable "telegram_chat_id" {
  type        = string
  default     = ""
  description = "Telegram chat ID for notifications (injected from CI secrets)"
  sensitive   = true
}

variable "universe" {
  type        = string
  default     = "nifty500"
  description = "Symbol universe for daily scans: nifty50, nifty500"
}
