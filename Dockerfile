FROM python:3.14-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

COPY scripts/collector-loop.sh /app/scripts/collector-loop.sh
RUN chmod +x /app/scripts/collector-loop.sh

CMD ["/app/scripts/collector-loop.sh"]
