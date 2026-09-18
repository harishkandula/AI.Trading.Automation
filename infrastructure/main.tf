terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  name = "${var.project}-${var.environment}"
}

resource "aws_s3_bucket" "data" {
  bucket_prefix = "${local.name}-data-"
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
}

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
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.data.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.state.arn
      }
    ]
  })
}

resource "aws_lambda_function" "jobs" {
  for_each = {
    morning = "market_ai.handlers.morning.handler"
    evening = "market_ai.handlers.evening.handler"
    weekly  = "market_ai.handlers.weekly.handler"
  }
  function_name    = "${local.name}-${each.key}"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = each.value
  filename         = "${path.module}/../build/lambda.zip"
  source_code_hash = filebase64sha256("${path.module}/../build/lambda.zip")
  timeout          = 120
  memory_size      = 512
  environment {
    variables = {
      APP_ENV              = var.environment
      DATA_BUCKET          = aws_s3_bucket.data.id
      STATE_TABLE          = aws_dynamodb_table.state.name
      TRADING_MODE         = "RESEARCH_ONLY"
      LIVE_ORDER_PLACEMENT = "false"
      BROKER_PROVIDER      = "mock"
    }
  }
}

resource "aws_cloudwatch_event_rule" "morning" {
  name                = "${local.name}-morning"
  schedule_expression = "cron(30 1 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_rule" "evening" {
  name                = "${local.name}-evening"
  schedule_expression = "cron(30 13 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_rule" "weekly" {
  name                = "${local.name}-weekly"
  schedule_expression = "cron(30 4 ? * SUN *)"
}

locals {
  mappings = {
    morning = aws_cloudwatch_event_rule.morning
    evening = aws_cloudwatch_event_rule.evening
    weekly  = aws_cloudwatch_event_rule.weekly
  }
}

resource "aws_cloudwatch_event_target" "jobs" {
  for_each = local.mappings
  rule     = each.value.name
  arn      = aws_lambda_function.jobs[each.key].arn
}

resource "aws_lambda_permission" "events" {
  for_each      = local.mappings
  statement_id  = "AllowEventBridge-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.jobs[each.key].function_name
  principal     = "events.amazonaws.com"
  source_arn    = each.value.arn
}

output "data_bucket" {
  value = aws_s3_bucket.data.id
}

output "lambda_names" {
  value = { for k, v in aws_lambda_function.jobs : k => v.function_name }
}
