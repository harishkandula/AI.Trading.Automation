terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "market-ai-tf-state-ap-south-1"
    key            = "market-ai/dev/terraform.tfstate"
    region         = "ap-south-1"
    dynamodb_table = "market-ai-tf-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  name = "${var.project}-${var.environment}"

  common_env = {
    APP_ENV              = var.environment
    DATA_BUCKET          = aws_s3_bucket.data.id
    STATE_TABLE          = aws_dynamodb_table.state.name
    LEADERBOARD_TABLE    = aws_dynamodb_table.leaderboard.name
    TRADING_MODE         = "RESEARCH_ONLY"
    LIVE_ORDER_PLACEMENT = "false"
    ENABLE_LIVE_ORDERS   = "false"
    BROKER_PROVIDER      = "mock"
    TELEGRAM_BOT_TOKEN   = var.telegram_bot_token
    TELEGRAM_CHAT_ID     = var.telegram_chat_id
    UNIVERSE             = var.universe
  }
}

# ── S3 ────────────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "data" {
  bucket = "${local.name}-data-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── DynamoDB ──────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "state" {
  name         = "${local.name}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

# ── DynamoDB – Leaderboard ───────────────────────────────────────────────────

resource "aws_dynamodb_table" "leaderboard" {
  name         = "${local.name}-leaderboard"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

# ── ECR ───────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# ── IAM – Lambda ──────────────────────────────────────────────────────────────

resource "aws_iam_role" "lambda" {
  name = "${local.name}-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda" {
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${local.name}-*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = ["${aws_s3_bucket.data.arn}", "${aws_s3_bucket.data.arn}/*"]
      },
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query",
        "dynamodb:UpdateItem", "dynamodb:Scan"]
        Resource = [
          aws_dynamodb_table.state.arn,
          aws_dynamodb_table.leaderboard.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:*:secret:${local.name}/*"
      }
    ]
  })
}

# ── Lambda Functions (preserved from v1) ─────────────────────────────────────

resource "aws_lambda_function" "jobs" {
  for_each = {
    morning       = "market_ai.handlers.morning.handler"
    evening       = "market_ai.handlers.evening.handler"
    weekly        = "market_ai.handlers.weekly.handler"
    token_refresh = "market_ai.handlers.token_refresh.handler"
    data_pipeline = "market_ai.handlers.data_pipeline.handler"
  }

  function_name = "${local.name}-${each.key}"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.ecr_image_tag}"
  timeout       = 120
  memory_size   = 1024

  image_config {
    command = [each.value]
  }

  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_function" "data_pipeline_extended" {
  function_name = "${local.name}-data-pipeline-backfill"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.ecr_image_tag}"
  timeout       = 900
  memory_size   = 1024

  image_config {
    command = ["market_ai.handlers.data_pipeline.handler"]
  }

  environment {
    variables = local.common_env
  }
}

resource "aws_cloudwatch_log_group" "data_pipeline_backfill" {
  name              = "/aws/lambda/${aws_lambda_function.data_pipeline_extended.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "backtest" {
  function_name = "${local.name}-backtest"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.ecr_image_tag}"
  timeout       = 900
  memory_size   = 1024

  image_config {
    command = ["market_ai.handlers.backtest.handler"]
  }

  environment {
    variables = local.common_env
  }
}

# ── CloudWatch Log Groups (Lambda) ────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = aws_lambda_function.jobs
  name              = "/aws/lambda/${each.value.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "backtest" {
  name              = "/aws/lambda/${aws_lambda_function.backtest.function_name}"
  retention_in_days = var.log_retention_days
}

# ── EventBridge Schedules ─────────────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "morning" {
  name                = "${local.name}-morning"
  description         = "07:00 IST Mon-Fri swing watchlist"
  schedule_expression = "cron(30 1 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_rule" "evening" {
  name                = "${local.name}-evening"
  description         = "19:00 IST Mon-Fri post-market scan"
  schedule_expression = "cron(30 13 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_rule" "weekly" {
  name                = "${local.name}-weekly"
  description         = "10:00 IST Sunday compounder research"
  schedule_expression = "cron(30 4 ? * SUN *)"
}

resource "aws_cloudwatch_event_rule" "token_refresh" {
  name                = "${local.name}-token-refresh"
  description         = "06:00 IST every day Groww token refresh"
  schedule_expression = "cron(30 0 ? * * *)"
}

resource "aws_cloudwatch_event_rule" "data_pipeline" {
  name                = "${local.name}-data-pipeline"
  description         = "04:00 IST Mon-Fri daily bhavcopy fetch and indicator computation"
  schedule_expression = "cron(30 22 ? * SUN-THU *)"
}

resource "aws_cloudwatch_event_rule" "backtest" {
  name                = "${local.name}-backtest"
  description         = "11:00 IST Sunday weekly full backtest"
  schedule_expression = "cron(30 5 ? * SUN *)"
}

locals {
  event_mappings = {
    morning       = aws_cloudwatch_event_rule.morning
    evening       = aws_cloudwatch_event_rule.evening
    weekly        = aws_cloudwatch_event_rule.weekly
    token_refresh = aws_cloudwatch_event_rule.token_refresh
    data_pipeline = aws_cloudwatch_event_rule.data_pipeline
  }
}

resource "aws_cloudwatch_event_target" "jobs" {
  for_each = local.event_mappings
  rule     = each.value.name
  arn      = aws_lambda_function.jobs[each.key].arn
}

resource "aws_lambda_permission" "events" {
  for_each      = local.event_mappings
  statement_id  = "AllowEventBridge-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.jobs[each.key].function_name
  principal     = "events.amazonaws.com"
  source_arn    = each.value.arn
}

# Backtest schedule now triggers ECS Fargate (see aws_cloudwatch_event_target.backtest_ecs).
# Lambda backtest function is kept for manual ad-hoc invocations only.

# ── Pre-warm rules (2 min before morning and evening) ────────────────────────
# Fires a {_prewarm: true} ping so the container is already warm when the real
# scan invocation arrives, eliminating the ~10 s cold-start INIT timeout.

resource "aws_cloudwatch_event_rule" "prewarm_morning" {
  name                = "${local.name}-prewarm-morning"
  description         = "06:58 IST Mon-Fri pre-warm for morning Lambda"
  schedule_expression = "cron(28 1 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_rule" "prewarm_evening" {
  name                = "${local.name}-prewarm-evening"
  description         = "18:58 IST Mon-Fri pre-warm for evening Lambda"
  schedule_expression = "cron(28 13 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_target" "prewarm_morning" {
  rule  = aws_cloudwatch_event_rule.prewarm_morning.name
  arn   = aws_lambda_function.jobs["morning"].arn
  input = jsonencode({ _prewarm = true })
}

resource "aws_cloudwatch_event_target" "prewarm_evening" {
  rule  = aws_cloudwatch_event_rule.prewarm_evening.name
  arn   = aws_lambda_function.jobs["evening"].arn
  input = jsonencode({ _prewarm = true })
}

resource "aws_lambda_permission" "prewarm_morning" {
  statement_id  = "AllowEventBridge-prewarm-morning"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.jobs["morning"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.prewarm_morning.arn
}

resource "aws_lambda_permission" "prewarm_evening" {
  statement_id  = "AllowEventBridge-prewarm-evening"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.jobs["evening"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.prewarm_evening.arn
}

# ── CloudWatch Alarms – Lambda failures ───────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = aws_lambda_function.jobs

  alarm_name          = "${each.value.function_name}-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Lambda ${each.value.function_name} produced errors"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = each.value.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "backtest_errors" {
  alarm_name          = "${aws_lambda_function.backtest.function_name}-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Lambda ${aws_lambda_function.backtest.function_name} produced errors"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.backtest.function_name
  }
}

# ── Leaderboard Lambda + Schedule ────────────────────────────────────────────

resource "aws_lambda_function" "leaderboard" {
  function_name = "${local.name}-leaderboard"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.ecr_image_tag}"
  timeout       = 60
  memory_size   = 256

  image_config {
    command = ["market_ai.handlers.leaderboard.handler"]
  }

  environment {
    variables = local.common_env
  }
}

resource "aws_cloudwatch_log_group" "leaderboard" {
  name              = "/aws/lambda/${aws_lambda_function.leaderboard.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_event_rule" "leaderboard" {
  name                = "${local.name}-leaderboard"
  description         = "12:30 IST Sunday — strategy leaderboard after backtest completes"
  schedule_expression = "cron(0 7 ? * SUN *)"
}

resource "aws_cloudwatch_event_target" "leaderboard" {
  rule = aws_cloudwatch_event_rule.leaderboard.name
  arn  = aws_lambda_function.leaderboard.arn
}

resource "aws_lambda_permission" "leaderboard" {
  statement_id  = "AllowEventBridge-leaderboard"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.leaderboard.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.leaderboard.arn
}

resource "aws_cloudwatch_metric_alarm" "leaderboard_errors" {
  alarm_name          = "${aws_lambda_function.leaderboard.function_name}-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Lambda ${aws_lambda_function.leaderboard.function_name} produced errors"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.leaderboard.function_name
  }
}

# ── Secrets Manager placeholders ─────────────────────────────────────────────
# Values must be populated manually or via CI/CD – never in Terraform state.

resource "aws_secretsmanager_secret" "groww" {
  name                    = "${local.name}/groww"
  description             = "Groww API credentials"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "telegram" {
  name                    = "${local.name}/telegram"
  description             = "Telegram bot token and chat ID"
  recovery_window_in_days = 7
}

# ── ECS (optional – enable_ecs = true) ───────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  count = var.enable_ecs ? 1 : 0
  name  = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  count = var.enable_ecs ? 1 : 0
  name  = "${local.name}-ecs-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_exec_managed" {
  count      = var.enable_ecs ? 1 : 0
  role       = aws_iam_role.ecs_task_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  count = var.enable_ecs ? 1 : 0
  name  = "${local.name}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ecs_task" {
  count = var.enable_ecs ? 1 : 0
  role  = aws_iam_role.ecs_task[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = ["${aws_s3_bucket.data.arn}", "${aws_s3_bucket.data.arn}/*"]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem",
          "dynamodb:Query", "dynamodb:UpdateItem", "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.state.arn,
          aws_dynamodb_table.leaderboard.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:*:secret:${local.name}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:/ecs/${local.name}*"
      }
    ]
  })
}

# Screener ECS task definition
resource "aws_ecs_task_definition" "screener" {
  count                    = var.enable_ecs ? 1 : 0
  family                   = "${local.name}-screener"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_task_cpu
  memory                   = var.ecs_task_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution[0].arn
  task_role_arn            = aws_iam_role.ecs_task[0].arn

  container_definitions = jsonencode([{
    name       = "screener"
    image      = "${aws_ecr_repository.app.repository_url}:${var.ecr_image_tag}"
    entryPoint = ["python", "-m"]
    command = [
      "scripts.run_screener",
      "--strategy", "all",
      "--universe", "mock"
    ]
    environment = [
      { name = "APP_ENV", value = var.environment },
      { name = "TRADING_MODE", value = "RESEARCH_ONLY" },
      { name = "ENABLE_LIVE_ORDERS", value = "false" },
      { name = "DATA_BUCKET", value = aws_s3_bucket.data.id }
    ]
    secrets = [
      { name = "GROWW_ACCESS_TOKEN", valueFrom = "${aws_secretsmanager_secret.groww.arn}:access_token::" },
      { name = "TELEGRAM_BOT_TOKEN", valueFrom = "${aws_secretsmanager_secret.telegram.arn}:bot_token::" },
      { name = "TELEGRAM_CHAT_ID", valueFrom = "${aws_secretsmanager_secret.telegram.arn}:chat_id::" }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = "/ecs/${local.name}-screener"
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "screener"
      }
    }
  }])
}

# Security group for ECS Fargate tasks (outbound only — no inbound needed)
resource "aws_security_group" "ecs_tasks" {
  count       = var.enable_ecs ? 1 : 0
  name        = "${local.name}-ecs-tasks"
  description = "ECS Fargate tasks - outbound to internet only"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound (S3, ECR, NSE, DynamoDB)"
  }
}

# Backtest ECS task definition — 4 vCPU / 8 GB, no timeout limit
resource "aws_ecs_task_definition" "backtest" {
  count                    = var.enable_ecs ? 1 : 0
  family                   = "${local.name}-backtest"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 4096
  memory                   = 8192
  execution_role_arn       = aws_iam_role.ecs_task_execution[0].arn
  task_role_arn            = aws_iam_role.ecs_task[0].arn

  container_definitions = jsonencode([{
    name       = "backtest"
    image      = "${aws_ecr_repository.app.repository_url}:${var.ecr_image_tag}"
    entryPoint = ["python", "-m"]
    command = [
      "scripts.run_backtest",
      "--strategy", "all",
      "--universe", "nifty500",
      "--start", "2019-01-01",
      "--end", "2024-12-31",
      "--output", "/tmp/backtest_results.json"
    ]
    environment = [
      { name = "APP_ENV", value = var.environment },
      { name = "TRADING_MODE", value = "RESEARCH_ONLY" },
      { name = "ENABLE_LIVE_ORDERS", value = "false" },
      { name = "DATA_BUCKET", value = aws_s3_bucket.data.id },
      { name = "STATE_TABLE", value = aws_dynamodb_table.state.name },
      { name = "LEADERBOARD_TABLE", value = aws_dynamodb_table.leaderboard.name },
      { name = "TELEGRAM_BOT_TOKEN", value = var.telegram_bot_token },
      { name = "TELEGRAM_CHAT_ID", value = var.telegram_chat_id }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = "/ecs/${local.name}-backtest"
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "backtest"
      }
    }
  }])
}

# IAM role for EventBridge to launch ECS tasks
resource "aws_iam_role" "eventbridge_ecs" {
  count = var.enable_ecs ? 1 : 0
  name  = "${local.name}-eventbridge-ecs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "eventbridge_ecs" {
  count = var.enable_ecs ? 1 : 0
  role  = aws_iam_role.eventbridge_ecs[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ecs:RunTask"
      Resource = aws_ecs_task_definition.backtest[0].arn
      }, {
      Effect = "Allow"
      Action = "iam:PassRole"
      Resource = [
        aws_iam_role.ecs_task_execution[0].arn,
        aws_iam_role.ecs_task[0].arn,
      ]
    }]
  })
}

# EventBridge target: Sunday 11:00 IST → ECS Fargate backtest task
resource "aws_cloudwatch_event_target" "backtest_ecs" {
  count    = var.enable_ecs ? 1 : 0
  rule     = aws_cloudwatch_event_rule.backtest.name
  arn      = aws_ecs_cluster.main[0].arn
  role_arn = aws_iam_role.eventbridge_ecs[0].arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.backtest[0].arn
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = var.private_subnet_ids
      security_groups  = [aws_security_group.ecs_tasks[0].id]
      assign_public_ip = true
    }
  }
}

resource "aws_cloudwatch_log_group" "ecs_screener" {
  count             = var.enable_ecs ? 1 : 0
  name              = "/ecs/${local.name}-screener"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "ecs_backtest" {
  count             = var.enable_ecs ? 1 : 0
  name              = "/ecs/${local.name}-backtest"
  retention_in_days = var.log_retention_days
}

# ── RDS PostgreSQL (optional – enable_rds = true) ────────────────────────────

resource "aws_db_subnet_group" "main" {
  count      = var.enable_rds ? 1 : 0
  name       = "${local.name}-db"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "${local.name}-db-subnet-group"
  }
}

resource "aws_security_group" "rds" {
  count       = var.enable_rds ? 1 : 0
  name        = "${local.name}-rds"
  description = "RDS access from ECS tasks only"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    description = "PostgreSQL from ECS tasks"
    # Restrict to private subnets – tighten with ECS security group ID when available
    cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_secretsmanager_secret" "db_password" {
  count                   = var.enable_rds ? 1 : 0
  name                    = "${local.name}/db-password"
  description             = "RDS master password – set value manually after creation"
  recovery_window_in_days = 7
}

resource "aws_db_instance" "main" {
  count = var.enable_rds ? 1 : 0

  identifier        = local.name
  engine            = "postgres"
  engine_version    = "16"
  instance_class    = var.rds_instance_class
  allocated_storage = var.rds_allocated_storage
  storage_encrypted = true

  db_name  = "market_ai"
  username = var.rds_username
  # Password must be set via manage_master_user_password or manually
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main[0].name
  vpc_security_group_ids = [aws_security_group.rds[0].id]
  publicly_accessible    = false

  backup_retention_period   = 7
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name}-final"

  deletion_protection = true
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "data_bucket" {
  value       = aws_s3_bucket.data.id
  description = "S3 bucket for data and reports"
}

output "ecr_repository_url" {
  value       = aws_ecr_repository.app.repository_url
  description = "ECR repository – push your Docker image here"
}

output "lambda_names" {
  value       = { for k, v in aws_lambda_function.jobs : k => v.function_name }
  description = "Lambda function names"
}

output "leaderboard_table" {
  value       = aws_dynamodb_table.leaderboard.name
  description = "DynamoDB leaderboard table name"
}

output "groww_secret_arn" {
  value       = aws_secretsmanager_secret.groww.arn
  description = "Secrets Manager ARN for Groww credentials"
}

output "telegram_secret_arn" {
  value       = aws_secretsmanager_secret.telegram.arn
  description = "Secrets Manager ARN for Telegram credentials"
}

output "rds_endpoint" {
  value       = var.enable_rds ? aws_db_instance.main[0].endpoint : "RDS not enabled (set enable_rds=true)"
  description = "RDS PostgreSQL endpoint"
}

output "ecs_cluster_name" {
  value       = var.enable_ecs ? aws_ecs_cluster.main[0].name : "ECS not enabled (set enable_ecs=true)"
  description = "ECS cluster name"
}
