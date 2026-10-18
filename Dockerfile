FROM public.ecr.aws/lambda/python:3.12

WORKDIR /var/task

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir "."

ENV PYTHONPATH=/var/task/src
ENV TRADING_MODE=RESEARCH_ONLY
ENV ENABLE_LIVE_ORDERS=false

# Default handler – overridden per-function via image_config in Terraform
CMD ["market_ai.handlers.morning.handler"]
