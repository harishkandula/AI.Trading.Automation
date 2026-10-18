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
  default     = false
  description = "Set true to provision ECS cluster and task definitions"
}

variable "vpc_id" {
  type        = string
  default     = ""
  description = "Existing VPC ID for RDS/ECS (required if enable_rds or enable_ecs = true)"
}

variable "private_subnet_ids" {
  type        = list(string)
  default     = []
  description = "Private subnet IDs for RDS/ECS tasks"
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
