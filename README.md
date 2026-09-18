# Market AI MVP

Lambda-first personal Indian equity research platform. This release uses synthetic symbols and produces research candidates only. **No live order execution is enabled.**

## Included

- Morning swing watchlist Lambda
- Evening post-market scanner Lambda
- Sunday compounder research Lambda
- Deterministic scoring
- Mock broker and synthetic data
- Read-only Groww adapter boundary
- Terraform for Lambda, EventBridge, S3 and DynamoDB
- GitHub Actions CI/deployment
- Tests that verify live orders remain disabled

## Local setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
python - <<'PY'
from market_ai.handlers.morning import handler
from pprint import pprint
pprint(handler({}, None))
PY
```

## AWS deployment

Prerequisites: AWS CLI/SSO, Terraform 1.6+, Python 3.12 and zip.

```bash
./scripts/build_lambda.sh
cd infrastructure
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform fmt -check
terraform validate
terraform plan
terraform apply
```

Review the plan before apply. The delivered GitHub deployment workflow expects an OIDC role ARN stored as `AWS_DEPLOY_ROLE_ARN` in the `prod` environment variables.

## Groww

Official Groww documentation is at https://groww.in/trade-api/docs. Groww advertises market-data, historical-data, portfolio and order APIs. This repository intentionally does not guess exact SDK methods. Add the official SDK only after validating current authentication, scopes and account access. Keep order methods disabled.

## Next implementation work

1. Implement documented Groww holdings and historical-candle calls in `providers/groww.py`.
2. Persist generated reports to S3 and state to DynamoDB.
3. Add Telegram delivery using a Secrets Manager token.
4. Add licensed filings/news providers.
5. Add an ECS on-demand worker only for long backtests and large-document extraction.

## Disclaimer

For personal research and education only. This software does not provide guaranteed returns or personalized investment advice. Market investments involve risk, including loss of capital. Verify all information with original sources.
