# Architecture

This MVP is Lambda-first. EventBridge invokes three Lambda commands: morning, evening, and weekly. Synthetic data and the mock broker are enabled by default. S3 stores documents/reports and DynamoDB stores lightweight application state. Groww is isolated behind a read-only adapter. Live order mutations are blocked in code and configuration.

Schedules are UTC: 01:30 UTC = 07:00 IST, 13:30 UTC = 19:00 IST, and Sunday 04:30 UTC = 10:00 IST.

A later heavy-worker boundary can use ECS Fargate for large PDF parsing and backtests without replacing this core stack.
