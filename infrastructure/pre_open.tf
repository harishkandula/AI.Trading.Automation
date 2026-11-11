# ── Pre-open Lambda (09:05 IST Mon-Fri) ──────────────────────────────────────
# Runs during NSE pre-open call auction window (09:00-09:08 IST).
# Fetches live IEP data directly from NSE — independent of S3 data pipeline.

resource "aws_lambda_function" "pre_open" {
  function_name = "${local.name}-pre-open"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.ecr_image_tag}"
  timeout       = 60
  memory_size   = 512

  image_config {
    command = ["market_ai.handlers.pre_open.handler"]
  }

  environment {
    variables = merge(local.common_env, {
      TELEGRAM_SECRET = "${local.name}/telegram"
      APP_NAME        = local.name
    })
  }
}

resource "aws_cloudwatch_log_group" "pre_open" {
  name              = "/aws/lambda/${aws_lambda_function.pre_open.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_event_rule" "pre_open" {
  name                = "${local.name}-pre-open"
  description         = "09:05 IST Mon-Fri NSE pre-open IEP watchlist"
  schedule_expression = "cron(35 3 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_target" "pre_open" {
  rule = aws_cloudwatch_event_rule.pre_open.name
  arn  = aws_lambda_function.pre_open.arn
}

resource "aws_lambda_permission" "pre_open" {
  statement_id  = "AllowEventBridge-pre-open"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pre_open.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.pre_open.arn
}

resource "aws_cloudwatch_metric_alarm" "pre_open_errors" {
  alarm_name          = "${aws_lambda_function.pre_open.function_name}-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Pre-open Lambda errors"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.pre_open.function_name
  }
}
